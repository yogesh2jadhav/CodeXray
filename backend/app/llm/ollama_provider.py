"""
backend/app/llm/ollama_provider.py

Purpose
-------
`LLMProvider` implementation backed by a local Ollama server (build plan §32).

Responsibility
--------------
- Call `POST {host}/api/chat` (non-streaming) with a system + user message.
- Pass `temperature`, `num_ctx` and `num_predict` options from config.
- `available()` -> GET `{host}/api/tags`; also verifies the configured model is
  pulled (matches with or without an explicit `:tag`).
- Translate connection errors / timeouts / missing model into `LLMUnavailable`
  with an actionable message ("run `ollama serve`", "run `ollama pull <model>`").

CPU-only, no CUDA. Nothing leaves the machine.
"""
from __future__ import annotations

import time

from backend.app.config.settings import LLMConfig
from backend.app.llm.provider import LLMProvider, LLMResponse, LLMUnavailable


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, cfg: LLMConfig) -> None:
        self.cfg = cfg
        self.host = cfg.host.rstrip("/")

    # ------------------------------------------------------------------ http
    def _client(self):
        try:
            import httpx
        except Exception as exc:  # pragma: no cover
            raise LLMUnavailable(f"httpx is required for the Ollama provider: {exc}")
        return httpx.Client(base_url=self.host, timeout=self.cfg.request_timeout_s)

    # --------------------------------------------------------------- health
    def list_models(self) -> list[str]:
        try:
            with self._client() as c:
                r = c.get("/api/tags")
                r.raise_for_status()
                return [m.get("name", "") for m in r.json().get("models", [])]
        except Exception:
            return []

    def _model_present(self, models: list[str]) -> bool:
        want = self.cfg.model
        base = want.split(":")[0]
        return any(m == want or m.split(":")[0] == base for m in models)

    def available(self) -> bool:
        models = self.list_models()
        return bool(models) and self._model_present(models)

    # -------------------------------------------------------------- generate
    def generate(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        payload = {
            "model": self.cfg.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "options": {
                "temperature": self.cfg.temperature if temperature is None else temperature,
                "num_ctx": self.cfg.context_length,
                "num_predict": self.cfg.max_tokens if max_tokens is None else max_tokens,
            },
        }
        started = time.perf_counter()
        try:
            with self._client() as c:
                r = c.post("/api/chat", json=payload)
                if r.status_code == 404:
                    raise LLMUnavailable(
                        f"Ollama has no model '{self.cfg.model}'. Run:  ollama pull {self.cfg.model}"
                    )
                r.raise_for_status()
                data = r.json()
        except LLMUnavailable:
            raise
        except Exception as exc:
            raise LLMUnavailable(
                f"cannot reach Ollama at {self.host} ({exc}). "
                f"Start it with `ollama serve` and `ollama pull {self.cfg.model}`."
            )

        duration = time.perf_counter() - started
        message = (data.get("message") or {}).get("content", "")
        return LLMResponse(
            text=message.strip(),
            model=data.get("model", self.cfg.model),
            provider=self.name,
            duration_s=duration,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            truncated=data.get("done_reason") == "length",
        )
