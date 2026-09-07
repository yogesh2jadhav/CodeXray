"""
backend/app/llm/client.py

Purpose
-------
Orchestrate one question -> answer cycle (build plan §59): classify, build
context, build prompt, call the provider, parse the response.

Responsibility
--------------
- `AskService.ask(project_id, question)` runs the full pipeline and returns an
  `AskResult` with: the answer + parsed FACT/INFERENCE/UNKNOWN, the
  classification, the structured evidence list, the dynamic-SQL sites referenced,
  timings, and the provider/model used.
- `health()` reports whether the configured provider is reachable.
- Raises `LLMUnavailable` (surfaced by the API as 503) if the engine is down —
  it never falls back to a cloud service.

This is the single entry point the API and the future agent both use.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from backend.app.config.settings import get_settings
from backend.app.llm import prompt_builder
from backend.app.llm.context_builder import ContextBuilder
from backend.app.llm.provider import LLMProvider, LLMResponse, LLMUnavailable, get_provider
from backend.app.llm.question_classifier import classify
from backend.app.llm.response_parser import ParsedAnswer, parse
from backend.app.models.database import get_connection


@dataclass
class AskResult:
    project_id: int
    question: str
    parsed: ParsedAnswer
    classification: dict
    evidence: list[dict]
    context_summary: dict
    llm: dict
    timings: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "question": self.question,
            **self.parsed.as_dict(),
            "classification": self.classification,
            "evidence": self.evidence,
            "context": self.context_summary,
            "llm": self.llm,
            "timings": self.timings,
        }


class AskService:
    def __init__(self, provider: LLMProvider | None = None, conn=None) -> None:
        self.settings = get_settings()
        self.provider = provider or get_provider(self.settings.llm)
        self.conn = conn or get_connection()

    # ------------------------------------------------------------------ health
    def health(self) -> dict:
        try:
            ok = self.provider.available()
            models = self.provider.list_models()
        except LLMUnavailable:
            ok, models = False, []
        return {
            "provider": self.provider.name,
            "model": self.settings.llm.model,
            "available": ok,
            "models_installed": models,
        }

    # -------------------------------------------------------------------- ask
    def ask(self, project_id: int, question: str) -> AskResult:
        t0 = time.perf_counter()
        cls = classify(question)
        t_classify = time.perf_counter()

        context = ContextBuilder(project_id, conn=self.conn).build(cls)
        t_context = time.perf_counter()

        system, user_prompt = prompt_builder.build(question, context)

        # A line-by-line walkthrough or a full-flow narration needs a bigger budget.
        max_tokens = self.settings.llm.max_tokens
        if cls.line_by_line or cls.flow:
            max_tokens = max(max_tokens, 4096)

        try:
            resp: LLMResponse = self.provider.generate(
                system, user_prompt,
                temperature=self.settings.llm.temperature,
                max_tokens=max_tokens,
            )
        except LLMUnavailable:
            raise
        t_llm = time.perf_counter()

        parsed = parse(resp.text)

        # Cross-link the model's cited file refs back to our structured evidence.
        evidence = [e.as_dict() for e in context.evidence]

        return AskResult(
            project_id=project_id,
            question=question,
            parsed=parsed,
            classification=cls.as_dict(),
            evidence=evidence,
            context_summary=context.as_dict(),
            llm=resp.as_dict(),
            timings={
                "classify_s": round(t_classify - t0, 3),
                "context_s": round(t_context - t_classify, 3),
                "llm_s": round(t_llm - t_context, 3),
                "total_s": round(t_llm - t0, 3),
            },
        )
