"""
backend/app/config/settings.py

Purpose
-------
Typed, cached loader for CodeXray configuration.

Responsibility
--------------
- Read `config/config.yaml` (path overridable via ``CODEXRAY_CONFIG``).
- Expand ``${VAR}`` / ``${VAR:-default}`` references against the process
  environment (``.env`` is loaded first if python-dotenv is available).
- Apply a small set of ``CODEXRAY_*`` env overrides for the values that
  operators most often change (DB path, LLM model, security flags).
- Expose the result as a frozen dataclass tree so the rest of the app never
  touches raw dictionaries or the environment directly.

Nothing here performs I/O against the index or the network.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# Repo root = three levels up from this file (backend/app/config/settings.py).
REPO_ROOT = Path(__file__).resolve().parents[3]

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _load_dotenv() -> None:
    """Best-effort load of a repo-root .env; silently skipped if unavailable."""
    try:
        from dotenv import load_dotenv
    except Exception:  # pragma: no cover - optional dependency
        return
    load_dotenv(REPO_ROOT / ".env")


def _expand(value: Any) -> Any:
    """Recursively expand ${VAR} / ${VAR:-default} inside strings."""
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            return os.environ.get(m.group(1), m.group(2) if m.group(2) is not None else "")
        return _VAR_RE.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- #
# Config sections. Each dataclass mirrors a top-level key in config.yaml.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ScanConfig:
    java_extensions: tuple[str, ...]
    sql_extensions: tuple[str, ...]
    config_extensions: tuple[str, ...]
    doc_extensions: tuple[str, ...]
    ignore_dirs: frozenset[str]
    max_file_bytes: int


@dataclass(frozen=True)
class IndexConfig:
    database: Path
    vector_store: Path
    incremental: bool


@dataclass(frozen=True)
class LLMConfig:
    provider: str            # ollama | echo (echo = offline deterministic stub)
    model: str
    host: str
    temperature: float
    context_length: int      # model context window (num_ctx), tokens
    max_tokens: int          # cap on generated tokens
    request_timeout_s: float
    context_char_budget: int  # ContextBudgetManager ceiling for assembled evidence


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str            # ollama | hashing (hashing = offline, no model)
    model: str
    host: str
    dim: int                 # vector dimension for the hashing fallback
    batch: int


@dataclass(frozen=True)
class GraphConfig:
    enabled: bool
    max_impact_depth: int


@dataclass(frozen=True)
class RetrievalConfig:
    weights: dict[str, float]
    semantic_top_k: int
    hybrid_top_k: int


@dataclass(frozen=True)
class AgentConfig:
    max_iterations: int


@dataclass(frozen=True)
class DynamicSqlConfig:
    # How many nested method returns MethodReturnResolver will inline before it
    # stops and marks the part PARTIALLY_RESOLVED (guards against recursion and
    # keeps analysis bounded on large projects).
    max_depth: int
    enabled: bool


@dataclass(frozen=True)
class SecurityConfig:
    local_only: bool
    redact_secrets: bool
    secret_key_patterns: tuple[str, ...]


@dataclass(frozen=True)
class Settings:
    project_root: Path
    scan: ScanConfig
    index: IndexConfig
    llm: LLMConfig
    embedding: EmbeddingConfig
    agent: AgentConfig
    dynamic_sql: DynamicSqlConfig
    graph: GraphConfig
    retrieval: RetrievalConfig
    security: SecurityConfig
    retrieval_weights: dict[str, float]   # kept for back-compat (== retrieval.weights)
    logging_level: str
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


def _resolve_path(value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load, expand and validate configuration once per process."""
    _load_dotenv()

    cfg_path = _resolve_path(os.environ.get("CODEXRAY_CONFIG", "config/config.yaml"))
    raw: dict[str, Any] = _expand(yaml.safe_load(cfg_path.read_text()) or {})

    scan = raw.get("scan", {})
    index = raw.get("index", {})
    llm = raw.get("llm", {})
    emb = raw.get("embedding", {})
    agent = raw.get("agent", {})
    dsql = raw.get("dynamic_sql", {})
    graph = raw.get("graph", {})
    retr = raw.get("retrieval", {})
    sec = raw.get("security", {})
    weights = {k: float(v) for k, v in retr.get("weights", {}).items()}

    return Settings(
        project_root=_resolve_path(raw.get("project", {}).get("root", "./projects")),
        scan=ScanConfig(
            java_extensions=tuple(scan.get("java_extensions", [".java"])),
            sql_extensions=tuple(scan.get("sql_extensions", [".sql"])),
            config_extensions=tuple(scan.get("config_extensions", [])),
            doc_extensions=tuple(scan.get("doc_extensions", [])),
            ignore_dirs=frozenset(scan.get("ignore_dirs", [])),
            max_file_bytes=int(scan.get("max_file_bytes", 2_000_000)),
        ),
        index=IndexConfig(
            database=_resolve_path(os.environ.get("CODEXRAY_INDEX_DB", index.get("database", "data/indexes/codexray.db"))),
            vector_store=_resolve_path(index.get("vector_store", "data/vectors")),
            incremental=_as_bool(index.get("incremental", True), True),
        ),
        llm=LLMConfig(
            provider=os.environ.get("CODEXRAY_LLM_PROVIDER", llm.get("provider", "ollama")),
            model=os.environ.get("CODEXRAY_LLM_MODEL", llm.get("model", "qwen2.5-coder:7b")),
            host=os.environ.get("CODEXRAY_LLM_HOST", llm.get("host", "http://localhost:11434")),
            temperature=float(llm.get("temperature", 0.1)),
            context_length=int(llm.get("context_length", 32768)),
            max_tokens=int(llm.get("max_tokens", 1024)),
            request_timeout_s=float(llm.get("request_timeout_s", 120)),
            context_char_budget=int(llm.get("context_char_budget", 12000)),
        ),
        embedding=EmbeddingConfig(
            provider=os.environ.get("CODEXRAY_EMBEDDING_PROVIDER", emb.get("provider", "ollama")),
            model=os.environ.get("CODEXRAY_EMBEDDING_MODEL", emb.get("model", "nomic-embed-text")),
            host=os.environ.get("CODEXRAY_EMBEDDING_HOST", emb.get("host", llm.get("host", "http://localhost:11434"))),
            dim=int(emb.get("dim", 512)),
            batch=int(emb.get("batch", 16)),
        ),
        agent=AgentConfig(max_iterations=int(agent.get("max_iterations", 8))),
        dynamic_sql=DynamicSqlConfig(
            max_depth=int(dsql.get("max_depth", 5)),
            enabled=_as_bool(dsql.get("enabled", True), True),
        ),
        graph=GraphConfig(
            enabled=_as_bool(graph.get("enabled", True), True),
            max_impact_depth=int(graph.get("max_impact_depth", 4)),
        ),
        retrieval=RetrievalConfig(
            weights=weights,
            semantic_top_k=int(retr.get("semantic_top_k", 8)),
            hybrid_top_k=int(retr.get("hybrid_top_k", 12)),
        ),
        security=SecurityConfig(
            local_only=_as_bool(os.environ.get("CODEXRAY_LOCAL_ONLY", sec.get("local_only", True)), True),
            redact_secrets=_as_bool(os.environ.get("CODEXRAY_REDACT_SECRETS", sec.get("redact_secrets", True)), True),
            secret_key_patterns=tuple(sec.get("secret_key_patterns", [])),
        ),
        retrieval_weights=weights,
        logging_level=str(raw.get("logging", {}).get("level", "INFO")),
        raw=raw,
    )
