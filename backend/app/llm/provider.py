"""
backend/app/llm/provider.py

Purpose
-------
Inference-engine abstraction (build plan §30). The rest of CodeXray talks to
`LLMProvider`; it never imports Ollama / llama.cpp directly, so the model backend
can be swapped from config alone.

Responsibility
--------------
- `LLMProvider` interface: `generate()`, `available()`, `list_models()`.
- Shared value types: `LLMResponse`, `LLMUnavailable`.
- `get_provider()` factory: reads `llm.provider` and returns the right
  implementation (`ollama` or the offline `echo` stub). Accepts an explicit
  override so tests / callers can inject a fake.

No provider here performs retrieval or prompt building — they only take a system
prompt + user prompt and return text.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass

from backend.app.config.settings import LLMConfig, get_settings


class LLMUnavailable(RuntimeError):
    """Raised when the local inference engine cannot be reached or the model is
    not installed. The API turns this into a 503 with remediation steps rather
    than a stack trace."""


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    duration_s: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    truncated: bool = False

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "provider": self.provider,
            "duration_s": round(self.duration_s, 2),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "truncated": self.truncated,
        }


class LLMProvider(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    def generate(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        ...

    @abc.abstractmethod
    def available(self) -> bool:
        """True if the engine responds and the configured model is present."""

    @abc.abstractmethod
    def list_models(self) -> list[str]:
        ...


def get_provider(cfg: LLMConfig | None = None, *, override: LLMProvider | None = None) -> LLMProvider:
    if override is not None:
        return override
    cfg = cfg or get_settings().llm
    provider = (cfg.provider or "ollama").lower()
    if provider == "echo":
        from backend.app.llm.echo_provider import EchoProvider
        return EchoProvider(cfg)
    if provider == "ollama":
        from backend.app.llm.ollama_provider import OllamaProvider
        return OllamaProvider(cfg)
    raise LLMUnavailable(f"unknown llm.provider '{cfg.provider}' (expected: ollama | echo)")
