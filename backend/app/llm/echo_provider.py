"""
backend/app/llm/echo_provider.py

Purpose
-------
A deterministic, offline `LLMProvider` used when no model server is available —
for CI, for the test suite, and as a safe default on a machine where Ollama has
not been set up yet.

Responsibility
--------------
- `available()` is always True.
- `generate()` does NOT call any model. It returns a structured, readable
  summary of the *context it was given* (question + a digest of the evidence
  block), clearly labelled as a stub. This lets the whole pipeline
  (classify -> retrieve -> build context -> prompt -> parse -> API) be tested
  end-to-end without inference, and shows the user exactly what evidence would
  have been sent to a real model.

It never fabricates analysis — everything it prints is echoed from the prompt.
"""
from __future__ import annotations

import re

from backend.app.config.settings import LLMConfig
from backend.app.llm.provider import LLMProvider, LLMResponse


class EchoProvider(LLMProvider):
    name = "echo"

    def __init__(self, cfg: LLMConfig) -> None:
        self.cfg = cfg

    def available(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return ["echo"]

    def generate(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        question = ""
        m = re.search(r"QUESTION:\s*(.+)", prompt)
        if m:
            question = m.group(1).strip()

        # Pull the section headings the ContextBuilder emitted, for a quick digest.
        sections = re.findall(r"^([A-Z][A-Z /]+):\s*$", prompt, re.MULTILINE)
        # ...and the agent's tool headers, if this is an investigation prompt.
        sections += [f"tool: {t}" for t in re.findall(r"^### tool:\s*(\S+)", prompt, re.MULTILINE)]
        evidence_lines = [
            ln.strip() for ln in prompt.splitlines()
            if ln.strip().startswith("- ") or re.search(r"\.(java|sql|properties|xml|ya?ml):\d+", ln)
        ]

        section_lines = [f"  - {s.strip()}" for s in sections] or ["  (none)"]
        ev_digest = [f"  {ln}" for ln in evidence_lines[:20]] or ["  (no evidence lines)"]
        body = [
            "[echo provider — no local model was queried]",
            "",
            f"Question: {question or '(not found in prompt)'}",
            "",
            "Context sections assembled for this question:",
            *section_lines,
            "",
            "FACT: The following evidence was retrieved from the project index:",
            *ev_digest,
            "",
            "UNKNOWN: A real answer requires a local model. Configure one with:",
            f"  export CODEXRAY_LLM_PROVIDER=ollama && ollama pull {self.cfg.model}",
        ]
        return LLMResponse(
            text="\n".join(body),
            model="echo",
            provider=self.name,
            duration_s=0.0,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=0,
        )
