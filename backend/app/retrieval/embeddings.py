"""
backend/app/retrieval/embeddings.py

Purpose
-------
Local embedding abstraction (build plan §33 — a separate, configurable embedding
model; never reuse the generation model).

Responsibility
--------------
- `Embedder` interface: `embed(list[str]) -> np.ndarray (n, dim)`, `dim`, `name`.
- `OllamaEmbedder`: calls `{host}/api/embeddings` per text (e.g. `nomic-embed-text`).
  Falls back to the hashing embedder if the server/model is unreachable, so
  indexing never hard-fails.
- `HashingEmbedder`: pure-python, deterministic bag-of-tokens hashed into a fixed
  `dim` vector with sub-linear term weighting + L2 norm. No ML dependency;
  used offline, in CI, and as the OllamaEmbedder fallback.
- `get_embedder()`: factory driven by `embedding.provider`.

All vectors are L2-normalised so cosine similarity is a dot product.
"""
from __future__ import annotations

import abc
import hashlib
import logging
import re

import numpy as np

from backend.app.config.settings import EmbeddingConfig, get_settings

log = logging.getLogger("codexray.embeddings")
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


def _normalise(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


class Embedder(abc.ABC):
    name: str = "abstract"
    dim: int = 0

    @abc.abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray:
        ...

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]


class HashingEmbedder(Embedder):
    """Deterministic, dependency-free. Good enough for a small project and for
    keeping the pipeline testable without a model server."""

    name = "hashing"

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def _tokens(self, text: str) -> list[str]:
        toks = [t.lower() for t in _TOKEN_RE.findall(text)]
        # add CamelCase splits so "loadEligibleCustomers" matches "eligible"
        extra = []
        for t in toks:
            parts = re.findall(r"[a-z]+|[0-9]+", t)
            if len(parts) > 1:
                extra.extend(parts)
        return toks + extra

    def embed(self, texts: list[str]) -> np.ndarray:
        mat = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            counts: dict[int, float] = {}
            for tok in self._tokens(text):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                idx = h % self.dim
                sign = 1.0 if (h >> 8) & 1 else -1.0
                counts[idx] = counts.get(idx, 0.0) + sign
            for idx, val in counts.items():
                mat[i, idx] = np.sign(val) * np.log1p(abs(val))
        return _normalise(mat)


class OllamaEmbedder(Embedder):
    name = "ollama"

    def __init__(self, cfg: EmbeddingConfig) -> None:
        self.cfg = cfg
        self.model = cfg.model
        self.host = cfg.host.rstrip("/")
        self._fallback = HashingEmbedder(cfg.dim)
        self._dim: int | None = None
        self._degraded = False

    @property
    def dim(self) -> int:
        return self._dim or self.cfg.dim

    def embed(self, texts: list[str]) -> np.ndarray:
        if self._degraded:
            return self._fallback.embed(texts)
        try:
            import httpx
        except Exception:
            self._degraded = True
            return self._fallback.embed(texts)

        vectors: list[list[float]] = []
        try:
            with httpx.Client(base_url=self.host, timeout=60) as c:
                for t in texts:
                    r = c.post("/api/embeddings", json={"model": self.model, "prompt": t[:8000]})
                    r.raise_for_status()
                    vectors.append(r.json()["embedding"])
        except Exception as exc:
            log.warning("Ollama embeddings unavailable (%s); using hashing embedder", exc)
            self._degraded = True
            return self._fallback.embed(texts)

        mat = np.asarray(vectors, dtype=np.float32)
        self._dim = mat.shape[1]
        return _normalise(mat)


def get_embedder(cfg: EmbeddingConfig | None = None, *, override: Embedder | None = None) -> Embedder:
    if override is not None:
        return override
    cfg = cfg or get_settings().embedding
    if (cfg.provider or "ollama").lower() == "hashing":
        return HashingEmbedder(cfg.dim)
    return OllamaEmbedder(cfg)
