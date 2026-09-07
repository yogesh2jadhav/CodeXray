"""
backend/app/agents/tools.py

Purpose
-------
The tool catalogue the investigation agent can call (build plan §26, §28).

Responsibility
--------------
- Define every tool as a `Tool` (name, description, JSON-ish arg schema, `run`).
- Each `run(project_id, **args)` wraps an existing deterministic service
  (search / graph / dynamic-SQL / architecture / semantic) and returns a small
  JSON-serialisable dict that also carries an `evidence` list of
  {kind,file,line,detail} so the agent can cite sources.
- `ToolRegistry` executes by name, validates required args, truncates oversized
  results, and records failures instead of raising.

Tools are READ-ONLY (build plan §48). Nothing here modifies source, runs
destructive SQL, or touches the network beyond the local model host.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from backend.app.config.settings import get_settings
from backend.app.models.database import get_connection
from backend.app.retrieval import search as S

log = logging.getLogger("codexray.agent.tools")


@dataclass
class Tool:
    name: str
    description: str
    args: dict[str, str]                       # arg_name -> "type (required|optional): description"
    run: Callable[..., dict]

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "args": self.args}


@dataclass
class ToolResult:
    tool: str
    args: dict
    ok: bool
    data: Any = None
    evidence: list[dict] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict:
        return {"tool": self.tool, "args": self.args, "ok": self.ok,
                "data": self.data, "evidence": self.evidence, "error": self.error}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _conn():
    return get_connection()


def _project_root(project_id: int) -> Path | None:
    row = _conn().execute("SELECT root_path FROM projects WHERE id=?", (project_id,)).fetchone()
    return Path(row["root_path"]) if row else None


def _read_lines(project_id: int, rel_path: str, start: int | None, end: int | None, pad: int = 0) -> str:
    root = _project_root(project_id)
    if not root:
        return ""
    p = root / rel_path
    if not p.is_file():
        return ""
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if start is None:
        return "\n".join(lines[:200])
    s = max(start - 1 - pad, 0)
    e = min((end or start) + pad, len(lines))
    return "\n".join(f"{s + i + 1:>5}  {ln}" for i, ln in enumerate(lines[s:e]))


def _ev(kind: str, detail: str, file: str | None = None, line: int | None = None) -> dict:
    return {"kind": kind, "detail": detail, "file": file, "line": line}


# --------------------------------------------------------------------------- #
# tool implementations
# --------------------------------------------------------------------------- #
def _search_code(project_id: int, query: str, limit: int = 8) -> dict:
    from backend.app.retrieval.hybrid import hybrid_search
    hits = hybrid_search(project_id, query, k=limit)
    return {
        "results": [{"symbol": h.get("symbol"), "file": h.get("file"), "line": h.get("line"),
                     "score": h.get("score"), "signals": list(h.get("signals", {}))} for h in hits],
        "evidence": [_ev("code", h.get("symbol") or h.get("file"), h.get("file"), h.get("line")) for h in hits[:5]],
    }


def _search_symbol(project_id: int, name: str, limit: int = 10) -> dict:
    hits = S.search_symbols(project_id, name, limit)
    return {"results": hits, "evidence": [_ev(h["kind"], h["qualified"], h["file"], h["line_number"]) for h in hits[:6]]}


def _get_file(project_id: int, path: str) -> dict:
    root = _project_root(project_id)
    row = _conn().execute("SELECT path, language FROM files WHERE project_id=? AND path=?", (project_id, path)).fetchone()
    if row is None:
        # tolerant match on basename
        row = _conn().execute("SELECT path, language FROM files WHERE project_id=? AND path LIKE ? LIMIT 1",
                              (project_id, f"%{path}")).fetchone()
    if row is None:
        return {"error": f"file not found: {path}", "evidence": []}
    content = _read_lines(project_id, row["path"], None, None)
    return {"path": row["path"], "language": row["language"], "content": content[:8000],
            "evidence": [_ev("file", row["path"], row["path"])]}


def _get_class(project_id: int, name: str) -> dict:
    c = _conn()
    row = c.execute(
        "SELECT c.*, f.path AS fpath FROM classes c JOIN files f ON f.id=c.file_id "
        "WHERE c.project_id=? AND (c.name=? OR c.fully_qualified_name=?) LIMIT 1",
        (project_id, name, name)).fetchone()
    if row is None:
        return {"error": f"class not found: {name}", "evidence": []}
    methods = [dict(m) for m in c.execute(
        "SELECT name, signature, return_type, visibility, line_start FROM methods WHERE class_id=?", (row["id"],))]
    fields = [dict(m) for m in c.execute(
        "SELECT name, type_name, looks_like_sql, string_value FROM fields WHERE class_id=?", (row["id"],))]
    src = _read_lines(project_id, row["fpath"], row["line_start"], min((row["line_end"] or row["line_start"]),
                                                                      (row["line_start"] or 0) + 40))
    return {"name": row["name"], "package": row["package"], "kind": row["kind"], "file": row["fpath"],
            "extends": row["extends_name"], "implements": row["implements_names"],
            "methods": methods, "fields": fields, "source_head": src,
            "evidence": [_ev("class", row["name"], row["fpath"], row["line_start"])]}


def _get_method(project_id: int, name: str) -> dict:
    c = _conn()
    cls_name, _, m_name = name.rpartition(".")
    q = ("SELECT m.*, c.name AS cname, f.path AS fpath FROM methods m LEFT JOIN classes c ON c.id=m.class_id "
         "JOIN files f ON f.id=m.file_id WHERE m.project_id=? AND m.name=?")
    params: list[Any] = [project_id, m_name or name]
    if cls_name:
        q += " AND c.name=?"
        params.append(cls_name)
    rows = [dict(r) for r in c.execute(q + " LIMIT 5", params)]
    if not rows:
        return {"error": f"method not found: {name}", "evidence": []}
    out = []
    for r in rows:
        out.append({
            "qualified": f'{r["cname"]}.{r["name"]}', "signature": r["signature"],
            "return_type": r["return_type"], "file": r["fpath"],
            "line_start": r["line_start"], "line_end": r["line_end"],
            "source": _read_lines(project_id, r["fpath"], r["line_start"], r["line_end"]),
        })
    return {"methods": out, "evidence": [_ev("method", m["qualified"], m["file"], m["line_start"]) for m in out]}


def _find_callers(project_id: int, symbol: str) -> dict:
    from backend.app.graph.service import GraphService
    gs = GraphService(project_id)
    callers = gs.callers(symbol)
    raw = S.find_callers(project_id, symbol.split(".")[-1])
    return {"symbol": symbol, "callers": callers,
            "call_sites": [{"caller": f'{r.get("caller_class")}.{r.get("caller_method")}',
                            "file": r["file"], "line": r["line_number"]} for r in raw],
            "evidence": [_ev("call", f'{r.get("caller_class")}.{r.get("caller_method")} -> {symbol}',
                             r["file"], r["line_number"]) for r in raw[:8]]}


def _find_callees(project_id: int, symbol: str) -> dict:
    from backend.app.graph.service import GraphService
    return {"symbol": symbol, "callees": GraphService(project_id).callees(symbol), "evidence": []}


def _trace_call_path(project_id: int, src: str, dst: str) -> dict:
    from backend.app.graph.service import GraphService
    path = GraphService(project_id).call_path(src, dst)
    return {"src": src, "dst": dst, "path": path,
            "evidence": [_ev("path", " -> ".join(path))] if path else []}


def _trace_flow(project_id: int, method: str, max_depth: int | None = None) -> dict:
    """Recursive end-to-end call tree from `method`: every nested callee (bounded),
    plus the SQL / tables / metadata each step touches."""
    from backend.app.config.settings import get_settings
    from backend.app.graph.service import GraphService
    cfg = get_settings().flow
    tree = GraphService(project_id).call_tree(
        method, max_depth=max_depth or cfg.max_depth, max_nodes=cfg.max_methods
    )
    ev = [_ev("flow", f'{s["method"]} (depth {s["depth"]})', s["file"], s["line"]) for s in tree.get("steps", [])[:12]]
    return tree | {"evidence": ev}


def _find_references(project_id: int, symbol: str) -> dict:
    return _find_callers(project_id, symbol)


def _get_class_dependencies(project_id: int, name: str) -> dict:
    from backend.app.graph.service import GraphService
    return GraphService(project_id).class_dependencies(name) | {"evidence": []}


def _search_sql(project_id: int, query: str, limit: int = 8) -> dict:
    hits = S.search_sql(project_id, query, limit)
    return {"results": hits, "evidence": [_ev("sql", (h["raw_sql"] or "")[:100], h["file"], h["line_number"])
                                          for h in hits[:6]]}


def _get_sql_query(project_id: int, query_id: int) -> dict:
    c = _conn()
    q = c.execute("SELECT * FROM sql_queries WHERE id=? AND project_id=?", (query_id, project_id)).fetchone()
    if q is None:
        return {"error": f"sql query {query_id} not found", "evidence": []}
    kids = {t: [dict(r) for r in c.execute(f"SELECT * FROM {t} WHERE query_id=?", (query_id,))]
            for t in ("sql_tables", "sql_columns", "sql_joins", "sql_conditions", "sql_parameters")}
    return {"query": dict(q), **kids, "evidence": [_ev("sql", (q["raw_sql"] or "")[:120])]}


def _find_table_usage(project_id: int, table: str) -> dict:
    rows = S.find_table_usage(project_id, table)
    return {"table": table.upper(), "usage": rows,
            "evidence": [_ev("table_use", f'{r["table_name"]} {r["access"]} in {r.get("source_class")}.{r.get("source_method")}',
                             r["file"], r["line_number"]) for r in rows[:8]]}


def _find_column_usage(project_id: int, column: str) -> dict:
    rows = S.find_column_usage(project_id, column)
    return {"column": column, "usage": rows, "evidence": [_ev("column_use", column, r["file"]) for r in rows[:8]]}


def _trace_dynamic_sql(project_id: int, selector: str) -> dict:
    res = S.trace_dynamic_sql(project_id, selector)
    ev = []
    for site in res.get("sites", []):
        ev.append(_ev("dynamic_sql", f'{site["source_class"]}.{site["source_method"]} [{site["status"]}]',
                      site["file"], site["line"]))
    return res | {"evidence": ev}


def _trace_metadata_dependency(project_id: int, selector: str) -> dict:
    res = S.trace_dynamic_sql(project_id, selector)
    chains = []
    for site in res.get("sites", []):
        for dep in site["dependencies"]:
            if dep["dependency_type"] != "METADATA_QUERY":
                continue
            for e in dep.get("evidence", []):
                chains.append({
                    "site": f'{site["source_class"]}.{site["source_method"]}',
                    "resolves": dep["value"],
                    "metadata_sql": e.get("metadata_sql"),
                    "metadata_tables": e.get("metadata_tables", []),
                    "status": dep["resolution_status"],
                })
    return {"selector": selector, "metadata_chains": chains,
            "evidence": [_ev("metadata", f'{c["resolves"]} <- {",".join(c["metadata_tables"])}') for c in chains]}


def _impact_analysis(project_id: int, symbol: str, depth: int | None = None) -> dict:
    from backend.app.graph.service import GraphService
    return GraphService(project_id).impact_analysis(symbol, depth).as_dict() | {"evidence": []}


def _get_project_architecture(project_id: int) -> dict:
    from backend.app.analyzers.architecture.extractor import ArchitectureExtractor
    arch = ArchitectureExtractor().extract(project_id).as_dict()
    return {"layers": arch["layers"], "entry_points": arch["entry_points"],
            "data_access": arch["data_access"], "external_integrations": arch["external_integrations"],
            "evidence": [_ev("architecture", f'{c["name"]}: {c["role"]}/{c["layer"]}', c["file"])
                         for c in arch["components"][:12]]}


def _search_documents(project_id: int, query: str, limit: int = 4) -> dict:
    from backend.app.retrieval.semantic import SemanticIndex
    hits = [h for h in SemanticIndex().search(project_id, query, k=limit * 2) if h["chunk_type"] in ("doc", "config")]
    return {"results": hits[:limit],
            "evidence": [_ev(h["chunk_type"], h["symbol"], h["file"], h.get("line_start")) for h in hits[:limit]]}


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
_TOOLS: list[Tool] = [
    Tool("search_code", "Hybrid (symbol+keyword+semantic) code search for a concept or phrase.",
         {"query": "string (required)", "limit": "int (optional, default 8)"}, _search_code),
    Tool("search_symbol", "Find classes/methods/fields by (partial) name.",
         {"name": "string (required)", "limit": "int (optional)"}, _search_symbol),
    Tool("get_file", "Return the source of a file by path or basename.",
         {"path": "string (required)"}, _get_file),
    Tool("get_class", "Class detail: package, methods, fields, source head.",
         {"name": "string (required)"}, _get_class),
    Tool("get_method", "Method detail incl. full source. Accepts 'name' or 'Class.name'.",
         {"name": "string (required)"}, _get_method),
    Tool("find_callers", "Who calls this method (graph + call sites with file:line).",
         {"symbol": "string (required)"}, _find_callers),
    Tool("find_callees", "What this method calls.",
         {"symbol": "string (required)"}, _find_callees),
    Tool("find_references", "Alias of find_callers (call-site level references).",
         {"symbol": "string (required)"}, _find_references),
    Tool("trace_call_path", "Shortest call path between two methods.",
         {"src": "string (required)", "dst": "string (required)"}, _trace_call_path),
    Tool("trace_flow", "Recursive end-to-end call tree from a starting method: every nested "
                       "callee (bounded by flow.max_depth/max_methods) with the SQL, tables and "
                       "metadata lookups each step touches.",
         {"method": "string (required): Class.method or method name", "max_depth": "int (optional)"}, _trace_flow),
    Tool("get_class_dependencies", "What a class depends on and what depends on it.",
         {"name": "string (required)"}, _get_class_dependencies),
    Tool("search_sql", "Search SQL queries by text / table / column.",
         {"query": "string (required)", "limit": "int (optional)"}, _search_sql),
    Tool("get_sql_query", "Full detail of one parsed SQL query by id.",
         {"query_id": "int (required)"}, _get_sql_query),
    Tool("find_table_usage", "Every query (and Java method) that reads or writes a table.",
         {"table": "string (required)"}, _find_table_usage),
    Tool("find_column_usage", "Every query that references a column.",
         {"column": "string (required)"}, _find_column_usage),
    Tool("trace_dynamic_sql", "Reconstruct a dynamically-built SQL string: template, dependencies, status.",
         {"selector": "string (required): Class.method | table | SQL fragment"}, _trace_dynamic_sql),
    Tool("trace_metadata_dependency", "Show the metadata-lookup chain that supplies a dynamic table/column.",
         {"selector": "string (required)"}, _trace_metadata_dependency),
    Tool("impact_analysis", "Direct+indirect callers, reachable SQL/tables, tests, unknowns for a symbol.",
         {"symbol": "string (required)", "depth": "int (optional)"}, _impact_analysis),
    Tool("get_project_architecture", "Roles, layers, entry points, data-access classes.",
         {}, _get_project_architecture),
    Tool("search_documents", "Semantic search restricted to docs + configuration.",
         {"query": "string (required)", "limit": "int (optional)"}, _search_documents),
]

REGISTRY: dict[str, Tool] = {t.name: t for t in _TOOLS}


class ToolRegistry:
    def __init__(self, project_id: int) -> None:
        self.project_id = project_id
        self.limit = get_settings().agent.tool_result_char_limit

    def catalogue(self) -> list[dict]:
        return [t.spec() for t in _TOOLS]

    def run(self, name: str, args: dict | None = None) -> ToolResult:
        args = args or {}
        tool = REGISTRY.get(name)
        if tool is None:
            return ToolResult(name, args, ok=False, error=f"unknown tool '{name}'")
        try:
            clean = {k: v for k, v in args.items() if k in tool.args or k in ("depth", "limit")}
            data = tool.run(self.project_id, **clean)
            evidence = data.pop("evidence", []) if isinstance(data, dict) else []
            blob = json.dumps(data, default=str)
            if len(blob) > self.limit:
                data = {"_truncated": True, "preview": blob[: self.limit]}
            return ToolResult(name, args, ok="error" not in (data or {}), data=data,
                              evidence=evidence, error=(data or {}).get("error") if isinstance(data, dict) else None)
        except TypeError as exc:
            return ToolResult(name, args, ok=False, error=f"bad arguments: {exc}")
        except Exception as exc:  # never raise into the agent loop
            log.exception("tool %s failed", name)
            return ToolResult(name, args, ok=False, error=repr(exc))
