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


class ImpactRequest(BaseModel):
    symbol: str = Field(..., description="Class, Class.method, method name, or TABLE")
    depth: int | None = Field(None, description="caller BFS depth (defaults to graph.max_impact_depth)")


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
        "graph_edges": conn.execute("SELECT COUNT(*) FROM dependencies WHERE project_id=?", (project_id,)).fetchone()[0],
        "semantic_chunks": conn.execute("SELECT COUNT(*) FROM embeddings WHERE project_id=?", (project_id,)).fetchone()[0],
        "architecture_components": conn.execute("SELECT COUNT(*) FROM architecture_components WHERE project_id=?", (project_id,)).fetchone()[0],
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
    """mode: keyword (default) | symbol | sql | file | semantic | hybrid."""
    _project_or_404(project_id)
    mode = body.mode.lower()
    if mode == "symbol":
        return S.search_symbols(project_id, body.query, body.limit)
    if mode == "sql":
        return S.search_sql(project_id, body.query, body.limit)
    if mode == "file":
        return S.search_files(project_id, body.query, body.limit)
    if mode == "semantic":
        from backend.app.retrieval.semantic import SemanticIndex
        return SemanticIndex().search(project_id, body.query, k=body.limit)
    if mode == "hybrid":
        from backend.app.retrieval.hybrid import hybrid_search
        return hybrid_search(project_id, body.query, k=body.limit)
    return S.search_keyword(project_id, body.query, body.limit)


@router.get("/projects/{project_id}/semantic/status")
def semantic_status(project_id: int) -> dict:
    _project_or_404(project_id)
    from backend.app.retrieval.semantic import SemanticIndex
    return SemanticIndex().status(project_id)


# --------------------------------------------------------------------------- #
# Dependency graph (build plan §19, §41, §57)
# --------------------------------------------------------------------------- #
@router.get("/projects/{project_id}/graph")
def graph_neighbors(project_id: int, node: str, limit: int = 60) -> dict:
    """Adjacency (in + out edges) for a node — Class, Class.method, TABLE, file."""
    _project_or_404(project_id)
    from backend.app.graph.service import GraphService
    return GraphService(project_id).neighbors(node, limit)


@router.get("/projects/{project_id}/graph/callers")
def graph_callers(project_id: int, symbol: str) -> dict:
    _project_or_404(project_id)
    from backend.app.graph.service import GraphService
    gs = GraphService(project_id)
    return {"symbol": symbol, "callers": gs.callers(symbol), "callees": gs.callees(symbol)}


@router.get("/projects/{project_id}/graph/path")
def graph_path(project_id: int, src: str, dst: str) -> dict:
    _project_or_404(project_id)
    from backend.app.graph.service import GraphService
    return {"src": src, "dst": dst, "path": GraphService(project_id).call_path(src, dst)}


@router.get("/projects/{project_id}/flow")
def flow_tree(project_id: int, method: str, depth: int | None = None, max_methods: int | None = None) -> dict:
    """Recursive interprocedural call tree from a `Class.method` entry point, with
    a one-line purpose + SQL/tables per step (build plan §43, §83). Powers the
    Flow tab's tree view; the same data the LLM narrates in flow mode."""
    _project_or_404(project_id)
    from backend.app.config.settings import get_settings
    from backend.app.graph.service import GraphService
    cfg = get_settings().flow
    return GraphService(project_id).call_tree(
        method, max_depth=depth or cfg.max_depth, max_nodes=max_methods or cfg.max_methods
    )


@router.get("/projects/{project_id}/methods-by-name/{name}/summary")
def method_summary(project_id: int, name: str) -> dict:
    _project_or_404(project_id)
    conn = get_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT qualified, summary, source FROM method_summaries WHERE project_id=? AND "
        "(qualified=? OR qualified LIKE ?)", (project_id, name, f"%.{name}"))]
    return {"query": name, "summaries": rows}


@router.get("/projects/{project_id}/classes-by-name/{class_name}/dependencies")
def class_deps(project_id: int, class_name: str) -> dict:
    _project_or_404(project_id)
    from backend.app.graph.service import GraphService
    return GraphService(project_id).class_dependencies(class_name)


@router.get("/projects/{project_id}/graph/export")
def graph_export(project_id: int, format: str = "cypher"):
    """Export the whole graph (modules / packages / classes / methods / tables /
    SQL) for Neo4j (`cypher`), Gephi/yEd/Cytoscape (`graphml`), Graphviz (`dot`),
    or cytoscape.js (`json`). Build plan §19 — Neo4j is an optional target."""
    _project_or_404(project_id)
    from fastapi.responses import JSONResponse, PlainTextResponse

    from backend.app.graph.export import GraphExporter
    exp = GraphExporter(project_id)
    fmt = format.lower()
    if fmt == "cypher":
        return PlainTextResponse(exp.to_cypher(), media_type="text/plain",
                                 headers={"Content-Disposition": "attachment; filename=codexray-graph.cypher"})
    if fmt == "graphml":
        return PlainTextResponse(exp.to_graphml(), media_type="application/xml",
                                 headers={"Content-Disposition": "attachment; filename=codexray-graph.graphml"})
    if fmt == "dot":
        return PlainTextResponse(exp.to_dot(), media_type="text/vnd.graphviz",
                                 headers={"Content-Disposition": "attachment; filename=codexray-graph.dot"})
    if fmt == "json":
        return JSONResponse(exp.to_cytoscape_json())
    raise HTTPException(400, "format must be one of: cypher | graphml | dot | json")


class Neo4jLoad(BaseModel):
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str


@router.post("/projects/{project_id}/graph/export/neo4j")
def graph_export_neo4j(project_id: int, body: Neo4jLoad) -> dict:
    """Push the graph straight into a running Neo4j instance (needs `pip install neo4j`)."""
    _project_or_404(project_id)
    from backend.app.graph.export import GraphExporter
    try:
        return GraphExporter(project_id).push_to_neo4j(body.uri, body.user, body.password)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # connection refused, auth, …
        raise HTTPException(502, f"Neo4j load failed: {exc}")


@router.get("/projects/{project_id}/references")
def references(project_id: int, symbol: str) -> dict:
    _project_or_404(project_id)
    return {"callers": S.find_callers(project_id, symbol)}


class InvestigateRequest(BaseModel):
    question: str
    llm_planning: bool | None = Field(None, description="override agent.enable_llm_planning for this call")


@router.get("/agent/tools")
def agent_tools() -> list[dict]:
    """The read-only tool catalogue the investigation agent can call (build plan §26)."""
    from backend.app.agents.tools import ToolRegistry
    return ToolRegistry(0).catalogue()


@router.post("/projects/{project_id}/investigate")
def investigate(project_id: int, body: InvestigateRequest) -> dict:
    """Multi-step tool-calling investigation (build plan §60): classify → plan →
    call read-only tools → (optional LLM follow-ups) → synthesise an
    evidence-labelled answer. Returns the answer, the plan, the full tool trace
    and aggregated evidence. 503 if no local model is available."""
    _project_or_404(project_id)
    from backend.app.agents.agent import InvestigationAgent
    from backend.app.llm.provider import LLMUnavailable

    try:
        agent = InvestigationAgent(project_id, enable_llm_planning=body.llm_planning)
        return agent.investigate(body.question).as_dict()
    except LLMUnavailable as exc:
        raise HTTPException(503, detail={"error": "local LLM unavailable", "message": str(exc)})


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
def impact_analysis(project_id: int, body: ImpactRequest) -> dict:
    """What could be impacted if `symbol` changes (build plan §41): direct +
    indirect callers, SQL + tables reached, likely tests, and explicit unknowns."""
    _project_or_404(project_id)
    from backend.app.graph.service import GraphService
    return GraphService(project_id).impact_analysis(body.symbol, body.depth).as_dict()


@router.get("/projects/{project_id}/architecture")
def architecture(project_id: int, format: str = "json") -> dict | str:
    """Roles, layers, entry points and data-access classes (build plan §39).
    `?format=md` returns the Markdown rendering."""
    _project_or_404(project_id)
    from fastapi.responses import PlainTextResponse

    from backend.app.analyzers.architecture.extractor import ArchitectureExtractor
    arch = ArchitectureExtractor().extract(project_id)
    if format == "md":
        return PlainTextResponse(arch.to_markdown())
    return arch.as_dict()


@router.get("/projects/{project_id}/documentation")
def project_documentation(project_id: int, format: str = "md", llm: bool = False, package: str | None = None):
    """Auto-generated project (or, with `?package=`, single-module) summary
    document (build plan §37/§38/§61): purpose, architecture, key classes with
    their one-line purposes, SQL/dynamic-SQL picture, configuration, external
    dependencies, and known hotspots — all from the deterministic index. Every
    list that could be huge is capped with an honest total alongside it.
    `?llm=true` asks the local model for a short purpose paragraph (falls back
    to a heuristic one if no model is available). `?format=json` returns the
    structured form instead of Markdown."""
    _project_or_404(project_id)
    from fastapi.responses import PlainTextResponse

    from backend.app.analyzers.documentation.generator import ProjectDocGenerator
    doc = ProjectDocGenerator(project_id).generate(use_llm=llm, package_prefix=package)
    if format == "json":
        return doc.as_dict()
    stem = f"{doc.project}-{package}" if package else f"{doc.project}-summary"
    return PlainTextResponse(
        doc.to_markdown(),
        headers={"Content-Disposition": f"attachment; filename={stem}.md"},
    )


@router.get("/projects/{project_id}/documentation/modules")
def project_documentation_modules(project_id: int, depth: int = 2) -> list[dict]:
    """List modules (package prefixes) with class counts — pick one for
    `?package=` on /documentation, or use for --all-modules."""
    _project_or_404(project_id)
    from backend.app.analyzers.documentation.generator import ProjectDocGenerator
    return ProjectDocGenerator(project_id).list_modules(depth=depth)
