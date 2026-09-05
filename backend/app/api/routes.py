"""
backend/app/api/routes.py

Purpose
-------
HTTP surface for CodeXray (build plan §53). Sprint 1 subset.

Responsibility
--------------
- Project CRUD-lite: create / list / status.
- Trigger indexing (full or incremental) and report results.
- Browse the index: files, symbols, class + method detail, SQL, table usage.
- Search: unified keyword search plus typed symbol / sql / file search.
- Call-site lookups: callers / references of a method.

Endpoints that require the LLM, the graph or embeddings return 501 until their
sprint lands, so the API contract is stable from day one.

All responses are read-only. No endpoint mutates source code.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.config.settings import get_settings
from backend.app.indexing.index_writer import IndexWriter
from backend.app.indexing.indexer import ProjectIndexer
from backend.app.models.database import get_connection
from backend.app.retrieval import search as S

router = APIRouter()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class CreateProject(BaseModel):
    name: str = Field(..., examples=["synthetic"])
    root_path: str = Field(..., description="Absolute or repo-relative path to the project source")


class IndexRequest(BaseModel):
    force: bool = Field(False, description="Ignore content hashes and re-parse every file")


class SearchRequest(BaseModel):
    query: str
    mode: str = Field("keyword", description="keyword | symbol | sql | file")
    limit: int = 40


class AskRequest(BaseModel):
    question: str


class TraceRequest(BaseModel):
    selector: str = Field(..., description="class name, Class.method, table name, or a SQL fragment")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _project_or_404(project_id: int) -> dict:
    conn = get_connection()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"project {project_id} not found")
    return dict(row)


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #
@router.post("/projects", status_code=201)
def create_project(body: CreateProject) -> dict:
    root = Path(body.root_path).expanduser()
    if not root.is_absolute():
        root = (get_settings().project_root.parent / body.root_path).resolve()
    if not root.is_dir():
        raise HTTPException(400, f"root_path is not a directory: {root}")
    writer = IndexWriter()
    pid = writer.upsert_project(body.name, str(root))
    return {"id": pid, "name": body.name, "root_path": str(root)}


@router.get("/projects")
def list_projects() -> list[dict]:
    conn = get_connection()
    return [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY id").fetchall()]


@router.post("/projects/{project_id}/index")
def index_project(project_id: int, body: IndexRequest) -> dict:
    proj = _project_or_404(project_id)
    report = ProjectIndexer().index_project(proj["name"], proj["root_path"], force=body.force)
    return report.as_dict()


@router.get("/projects/{project_id}/status")
def project_status(project_id: int) -> dict:
    proj = _project_or_404(project_id)
    conn = get_connection()
    run = conn.execute(
        "SELECT * FROM analysis_runs WHERE project_id = ? ORDER BY id DESC LIMIT 1", (project_id,)
    ).fetchone()
    counts = {
        "files": conn.execute("SELECT COUNT(*) FROM files WHERE project_id=?", (project_id,)).fetchone()[0],
        "classes": conn.execute("SELECT COUNT(*) FROM classes WHERE project_id=?", (project_id,)).fetchone()[0],
        "methods": conn.execute("SELECT COUNT(*) FROM methods WHERE project_id=?", (project_id,)).fetchone()[0],
        "sql_queries": conn.execute("SELECT COUNT(*) FROM sql_queries WHERE project_id=?", (project_id,)).fetchone()[0],
        "symbols": conn.execute("SELECT COUNT(*) FROM symbols WHERE project_id=?", (project_id,)).fetchone()[0],
        "dynamic_sql": conn.execute("SELECT COUNT(*) FROM dynamic_sql WHERE project_id=?", (project_id,)).fetchone()[0],
        "parse_failures": conn.execute("SELECT COUNT(*) FROM parse_failures WHERE project_id=?", (project_id,)).fetchone()[0],
    }
    return {"project": proj, "last_run": dict(run) if run else None, "counts": counts}


# --------------------------------------------------------------------------- #
# Browse
# --------------------------------------------------------------------------- #
@router.get("/projects/{project_id}/files")
def list_files(project_id: int, q: str | None = None) -> list[dict]:
    _project_or_404(project_id)
    conn = get_connection()
    if q:
        return S.search_files(project_id, q, conn=conn)
    return [dict(r) for r in conn.execute(
        "SELECT id, path, language, size_bytes, indexed_at FROM files WHERE project_id=? ORDER BY path",
        (project_id,)).fetchall()]


@router.get("/projects/{project_id}/files/{file_id}/source")
def file_source(project_id: int, file_id: int) -> dict:
    _project_or_404(project_id)
    conn = get_connection()
    row = conn.execute("SELECT * FROM files WHERE id=? AND project_id=?", (file_id, project_id)).fetchone()
    if row is None:
        raise HTTPException(404, "file not found")
    text = Path(row["abs_path"]).read_text(encoding="utf-8", errors="replace")
    return {"path": row["path"], "language": row["language"], "content": text}


@router.get("/projects/{project_id}/symbols")
def symbols(project_id: int, q: str = Query(..., min_length=1), limit: int = 50) -> list[dict]:
    _project_or_404(project_id)
    return S.search_symbols(project_id, q, limit)


@router.get("/projects/{project_id}/classes/{class_id}")
def class_detail(project_id: int, class_id: int) -> dict:
    _project_or_404(project_id)
    conn = get_connection()
    cls = conn.execute("SELECT * FROM classes WHERE id=? AND project_id=?", (class_id, project_id)).fetchone()
    if cls is None:
        raise HTTPException(404, "class not found")
    methods = [dict(r) for r in conn.execute("SELECT * FROM methods WHERE class_id=?", (class_id,)).fetchall()]
    fields = [dict(r) for r in conn.execute("SELECT * FROM fields WHERE class_id=?", (class_id,)).fetchall()]
    return {"class": dict(cls), "methods": methods, "fields": fields}


@router.get("/projects/{project_id}/methods/{method_id}")
def method_detail(project_id: int, method_id: int) -> dict:
    _project_or_404(project_id)
    conn = get_connection()
    m = conn.execute("SELECT * FROM methods WHERE id=? AND project_id=?", (method_id, project_id)).fetchone()
    if m is None:
        raise HTTPException(404, "method not found")
    callers = S.find_callers(project_id, m["name"])
    return {"method": dict(m), "callers": callers}


# --------------------------------------------------------------------------- #
# SQL
# --------------------------------------------------------------------------- #
@router.get("/projects/{project_id}/sql")
def list_sql(project_id: int, q: str | None = None, limit: int = 100) -> list[dict]:
    _project_or_404(project_id)
    if q:
        return S.search_sql(project_id, q, limit)
    conn = get_connection()
    return [dict(r) for r in conn.execute(
        "SELECT id, query_type, origin, resolution_status, line_number, source_class, source_method, raw_sql "
        "FROM sql_queries WHERE project_id=? ORDER BY id LIMIT ?", (project_id, limit)).fetchall()]


@router.get("/projects/{project_id}/sql/{query_id}")
def sql_detail(project_id: int, query_id: int) -> dict:
    _project_or_404(project_id)
    conn = get_connection()
    q = conn.execute("SELECT * FROM sql_queries WHERE id=? AND project_id=?", (query_id, project_id)).fetchone()
    if q is None:
        raise HTTPException(404, "query not found")
    kids = {}
    for tbl in ("sql_tables", "sql_columns", "sql_joins", "sql_conditions", "sql_parameters"):
        kids[tbl] = [dict(r) for r in conn.execute(f"SELECT * FROM {tbl} WHERE query_id=?", (query_id,)).fetchall()]
    return {"query": dict(q), **kids}


@router.get("/projects/{project_id}/tables/{table}/usage")
def table_usage(project_id: int, table: str) -> list[dict]:
    _project_or_404(project_id)
    return S.find_table_usage(project_id, table)


@router.get("/projects/{project_id}/dynamic-sql")
def dynamic_sql(project_id: int) -> list[dict]:
    """Every reconstructed dynamic-SQL construction site with its dependency chain."""
    _project_or_404(project_id)
    return S.list_dynamic_sql(project_id)


@router.post("/projects/{project_id}/dynamic-sql/trace")
def trace_dynamic_sql(project_id: int, body: TraceRequest) -> dict:
    """`trace_dynamic_sql(query_or_method)` (build plan §28): deterministic
    evidence for how a dynamic query is assembled — template, dependencies
    (CONSTANT / METADATA_QUERY / METHOD_RETURN / PARAMETER / ...), tables and an
    overall RESOLVED / PARTIALLY_RESOLVED / UNRESOLVED status."""
    _project_or_404(project_id)
    return S.trace_dynamic_sql(project_id, body.selector)


# --------------------------------------------------------------------------- #
# Search / references
# --------------------------------------------------------------------------- #
@router.post("/projects/{project_id}/search")
def search(project_id: int, body: SearchRequest) -> list[dict]:
    _project_or_404(project_id)
    mode = body.mode.lower()
    if mode == "symbol":
        return S.search_symbols(project_id, body.query, body.limit)
    if mode == "sql":
        return S.search_sql(project_id, body.query, body.limit)
    if mode == "file":
        return S.search_files(project_id, body.query, body.limit)
    return S.search_keyword(project_id, body.query, body.limit)


@router.get("/projects/{project_id}/references")
def references(project_id: int, symbol: str) -> dict:
    _project_or_404(project_id)
    return {"callers": S.find_callers(project_id, symbol)}


@router.get("/llm/health")
def llm_health() -> dict:
    """Whether the configured local model backend is reachable (build plan §32)."""
    from backend.app.llm.client import AskService
    return AskService().health()


@router.post("/projects/{project_id}/ask")
def ask(project_id: int, body: AskRequest) -> dict:
    """Answer a technical question about the project using retrieved deterministic
    evidence + the local LLM (build plan §25, §59, §72). Returns the answer, the
    FACT/INFERENCE/UNKNOWN breakdown, the structured evidence list and timings.

    503 if no local model is available — CodeXray never falls back to the cloud."""
    _project_or_404(project_id)
    from backend.app.llm.client import AskService
    from backend.app.llm.provider import LLMUnavailable
    try:
        return AskService().ask(project_id, body.question).as_dict()
    except LLMUnavailable as exc:
        raise HTTPException(
            503,
            detail={
                "error": "local LLM unavailable",
                "message": str(exc),
                "hint": "Start Ollama (`ollama serve`) and pull a model, or set "
                        "CODEXRAY_LLM_PROVIDER=echo for an offline evidence-only response.",
            },
        )


@router.post("/projects/{project_id}/impact-analysis")
def impact_analysis(project_id: int) -> dict:
    raise HTTPException(501, "Impact analysis lands in Sprint 4 (needs the dependency graph).")


@router.get("/projects/{project_id}/architecture")
def architecture(project_id: int) -> dict:
    raise HTTPException(501, "Architecture extraction lands in Sprint 4/7.")
