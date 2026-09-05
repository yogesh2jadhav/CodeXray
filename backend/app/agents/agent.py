"""
backend/app/agents/agent.py

Purpose
-------
The tool-calling investigation agent (build plan §26, §27, §60).

Agent loop
----------
    question
      -> classify                         (question_classifier)
      -> plan investigation               (planner: deterministic seed tool calls)
      -> execute seed tools               (ToolRegistry)
      -> [optional] LLM requests more tools, up to agent.max_iterations
      -> assemble evidence bundle
      -> LLM synthesis                    (prompt_builder rules: FACT/INFERENCE/UNKNOWN)
      -> parse                            (response_parser)

Responsibility
--------------
- Run the loop and return an `AgentResult` with: the answer + parsed labels, the
  classification, the plan, the full tool trace (tool, args, ok, evidence), the
  aggregated evidence, iteration count and timings.
- Degrade safely: if the model cannot emit a valid tool request (e.g. the `echo`
  provider, or a weak model), the agent still answers from the deterministic seed
  plan. It never invents tool output.
- Stay read-only — every tool in the registry is read-only.

The LLM is the reasoning layer; the tools + planner are the source of truth.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from backend.app.agents.planner import ToolCall, plan
from backend.app.agents.tools import ToolRegistry, ToolResult
from backend.app.config.settings import get_settings
from backend.app.llm import prompt_builder
from backend.app.llm.provider import LLMProvider, LLMUnavailable, get_provider
from backend.app.llm.question_classifier import classify
from backend.app.llm.response_parser import ParsedAnswer, parse
from backend.app.models.database import get_connection

_LOOP_SYSTEM = """You are CodeXray's investigation planner. You may call read-only tools to gather
evidence about a Java + SQL project before answering.

Reply with ONE JSON object and nothing else:
  {"thought": "<why>", "tool": "<tool_name>", "args": { ... }}      to call a tool
  {"thought": "<why>", "done": true}                                 when you have enough evidence

Rules: use the exact tool names from the catalogue; keep args minimal; do not repeat
a call that already appears in the transcript; stop as soon as the evidence answers
the question."""

_ACTION_RE = re.compile(r"\{.*\}", re.S)


@dataclass
class AgentResult:
    project_id: int
    question: str
    parsed: ParsedAnswer
    classification: dict
    plan: list[dict]
    tool_trace: list[dict]
    evidence: list[dict]
    iterations: int
    llm: dict
    timings: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "question": self.question,
            **self.parsed.as_dict(),
            "classification": self.classification,
            "plan": self.plan,
            "tool_trace": self.tool_trace,
            "evidence": self.evidence,
            "iterations": self.iterations,
            "llm": self.llm,
            "timings": self.timings,
        }


class InvestigationAgent:
    def __init__(
        self,
        project_id: int,
        provider: LLMProvider | None = None,
        conn=None,
        *,
        enable_llm_planning: bool | None = None,
    ) -> None:
        self.pid = project_id
        self.settings = get_settings()
        self.conn = conn or get_connection()
        self.provider = provider or get_provider(self.settings.llm)
        self.registry = ToolRegistry(project_id)
        self.enable_llm_planning = (
            self.settings.agent.enable_llm_planning if enable_llm_planning is None else enable_llm_planning
        )

    # ------------------------------------------------------------------ run
    def investigate(self, question: str) -> AgentResult:
        t0 = time.perf_counter()
        cls = classify(question)
        seed = plan(cls)
        t_plan = time.perf_counter()

        trace: list[ToolResult] = []
        done_calls: set[tuple] = set()

        for call in seed:
            trace.append(self._exec(call, done_calls))

        iterations = 0
        if self.enable_llm_planning:
            iterations = self._llm_tool_loop(question, cls, trace, done_calls)
        t_tools = time.perf_counter()

        answer_text, llm_meta = self._synthesise(question, cls, trace)
        parsed = parse(answer_text)
        t_llm = time.perf_counter()

        evidence = _dedupe([e for r in trace for e in r.evidence])
        return AgentResult(
            project_id=self.pid,
            question=question,
            parsed=parsed,
            classification=cls.as_dict(),
            plan=[{"tool": c.tool, "args": c.args, "reason": c.reason} for c in seed],
            tool_trace=[r.as_dict() for r in trace],
            evidence=evidence,
            iterations=iterations,
            llm=llm_meta,
            timings={
                "plan_s": round(t_plan - t0, 3),
                "tools_s": round(t_tools - t_plan, 3),
                "synthesis_s": round(t_llm - t_tools, 3),
                "total_s": round(t_llm - t0, 3),
            },
        )

    # ------------------------------------------------------------- internals
    def _exec(self, call: ToolCall, done: set) -> ToolResult:
        key = (call.tool, json.dumps(call.args, sort_keys=True, default=str))
        done.add(key)
        return self.registry.run(call.tool, call.args)

    def _llm_tool_loop(self, question, cls, trace: list[ToolResult], done: set) -> int:
        max_iter = self.settings.agent.max_iterations
        catalogue = json.dumps(self.registry.catalogue(), indent=0)
        for i in range(max_iter):
            transcript = self._render_transcript(trace)
            prompt = (
                f"QUESTION: {question}\nQUESTION TYPE: {cls.qtype.value}\n\n"
                f"TOOL CATALOGUE:\n{catalogue}\n\n"
                f"TRANSCRIPT SO FAR:\n{transcript}\n\n"
                f"Decide the next single tool call, or {{\"done\": true}} if you can answer now."
            )
            try:
                resp = self.provider.generate(_LOOP_SYSTEM, prompt, temperature=0.0, max_tokens=300)
            except LLMUnavailable:
                break
            action = _parse_action(resp.text)
            if action is None or action.get("done"):
                return i
            tool = action.get("tool")
            args = action.get("args") or {}
            if not tool or tool not in _tool_names():
                trace.append(ToolResult(str(tool), args, ok=False, error="unknown tool requested"))
                return i
            key = (tool, json.dumps(args, sort_keys=True, default=str))
            if key in done:
                return i  # model is looping — bail to synthesis
            done.add(key)
            trace.append(self.registry.run(tool, args))
        return max_iter

    def _render_transcript(self, trace: list[ToolResult]) -> str:
        out = []
        for r in trace:
            summary = r.error if not r.ok else json.dumps(r.data, default=str)[:1200]
            out.append(f'- {r.tool}({json.dumps(r.args, default=str)}) -> {"OK" if r.ok else "ERROR"}: {summary}')
        return "\n".join(out) or "(no tools run yet)"

    def _synthesise(self, question, cls, trace: list[ToolResult]) -> tuple[str, dict]:
        proj = self.conn.execute("SELECT name FROM projects WHERE id=?", (self.pid,)).fetchone()
        pname = proj["name"] if proj else str(self.pid)

        blocks = [f"--- PROJECT: {pname} ---", f"QUESTION TYPE: {cls.qtype.value}", ""]
        for r in trace:
            body = r.error if not r.ok else json.dumps(r.data, indent=1, default=str)
            blocks.append(f"### tool: {r.tool}  args={json.dumps(r.args, default=str)}\n{body[:3500]}")
        evidence_block = "\n\n".join(blocks)

        user = (
            f"QUESTION: {question}\n\n"
            f"ANSWER FORMAT: {prompt_builder._FORMAT_HINTS.get(cls.qtype, '')}\n\n"
            f"--- INVESTIGATION EVIDENCE (read-only tool results) ---\n\n{evidence_block}\n\n"
            f"--- END EVIDENCE ---\n\n"
            f"Answer using ONLY this evidence. Label FACT / INFERENCE / UNKNOWN, cite file:line, "
            f"use the analyzer's dynamic-SQL status verbatim and never invent a table/column. "
            f"Finish with an Evidence: list."
        )
        try:
            resp = self.provider.generate(
                prompt_builder.SYSTEM_PROMPT, user,
                temperature=self.settings.llm.temperature,
                max_tokens=self.settings.llm.max_tokens,
            )
            return resp.text, resp.as_dict()
        except LLMUnavailable:
            raise

    # ------------------------------------------------------------- provider health
    def health(self) -> dict:
        try:
            return {"provider": self.provider.name, "available": self.provider.available()}
        except LLMUnavailable:
            return {"provider": self.provider.name, "available": False}


def _tool_names() -> set[str]:
    from backend.app.agents.tools import REGISTRY
    return set(REGISTRY)


def _parse_action(text: str) -> dict | None:
    m = _ACTION_RE.search(text or "")
    if not m:
        return None
    blob = m.group(0)
    for candidate in (blob, blob[: blob.rfind("}") + 1]):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for e in items:
        k = (e.get("kind"), e.get("detail"), e.get("file"), e.get("line"))
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out
