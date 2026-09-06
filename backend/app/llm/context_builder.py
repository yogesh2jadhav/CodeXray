"""
backend/app/llm/context_builder.py

Purpose
-------
Assemble a structured, evidence-first context for the LLM (build plan §25, §71).
Raw search results are NEVER sent straight to the model.

Responsibility
--------------
- Given a project and a `Classification`, gather from the deterministic index:
    PROJECT SUMMARY, RELEVANT CLASSES/METHODS, SOURCE, CALL GRAPH, SQL QUERIES,
    SQL TABLES/COLUMNS, DYNAMIC SQL RESOLUTION, METADATA DEPENDENCIES,
    CONFIGURATION (already secret-redacted), DOCUMENTATION, KNOWN UNCERTAINTIES.
- `ContextBudgetManager`: include sections by priority (exact method first, broad
  architecture last) until `llm.context_char_budget` is hit; truncate, don't
  overflow.
- Emit a `BuiltContext` with `.render()` (text for the prompt) and `.evidence`
  (structured {kind,file,line,detail} list for the clickable UI, build plan §50).

Every line the model sees is traceable to a file/line in the index.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from backend.app.config.settings import get_settings
from backend.app.llm.question_classifier import Classification, RetrievalMode
from backend.app.models.database import get_connection
from backend.app.retrieval import search as S

_MAX_SNIPPET_LINES = 50


@dataclass
class Evidence:
    kind: str
    detail: str
    file: str | None = None
    line: int | None = None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "detail": self.detail, "file": self.file, "line": self.line}


@dataclass
class Section:
    title: str
    priority: int          # 1 = most important, keep first
    lines: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    def text(self) -> str:
        return f"{self.title}:\n" + ("\n".join(self.lines) if self.lines else "(none found)")

    @property
    def size(self) -> int:
        return len(self.text())


@dataclass
class BuiltContext:
    project_name: str
    classification: Classification
    sections: list[Section]
    evidence: list[Evidence]
    dropped_sections: list[str]
    char_budget: int

    def render(self) -> str:
        return "\n\n".join(s.text() for s in self.sections)

    def as_dict(self) -> dict:
        return {
            "classification": self.classification.as_dict(),
            "sections_included": [s.title for s in self.sections],
            "sections_dropped": self.dropped_sections,
            "evidence": [e.as_dict() for e in self.evidence],
            "char_budget": self.char_budget,
        }


class ContextBudgetManager:
    """Keep sections in priority order until the character budget is exhausted."""

    def __init__(self, budget: int) -> None:
        self.budget = budget

    def select(self, sections: list[Section]) -> tuple[list[Section], list[str]]:
        kept: list[Section] = []
        dropped: list[str] = []
        used = 0
        for sec in sorted(sections, key=lambda s: s.priority):
            if not sec.lines and sec.priority > 3:
                dropped.append(sec.title)
                continue
            if used + sec.size <= self.budget:
                kept.append(sec)
                used += sec.size
            else:
                remaining = self.budget - used
                if remaining > 400 and sec.lines:
                    clipped = sec.text()[:remaining] + "\n… [truncated to fit context budget]"
                    sec.lines = [clipped.split(":\n", 1)[1]]
                    kept.append(sec)
                    used = self.budget
                else:
                    dropped.append(sec.title)
        kept.sort(key=lambda s: s.priority)
        return kept, dropped


class ContextBuilder:
    def __init__(self, project_id: int, conn=None) -> None:
        self.pid = project_id
        self.conn = conn or get_connection()

    # ------------------------------------------------------------------ build
    def build(self, cls: Classification) -> BuiltContext:
        proj = self.conn.execute("SELECT name, root_path FROM projects WHERE id=?", (self.pid,)).fetchone()
        project_name = proj["name"] if proj else str(self.pid)
        root = Path(proj["root_path"]) if proj else None

        modes = set(cls.modes)
        sections: list[Section] = [self._project_summary()]

        symbol_hits = self._symbol_hits(cls)
        if symbol_hits:
            sections.append(self._classes_methods(symbol_hits))
            if cls.line_by_line:
                sections.append(self._full_method_source(cls, symbol_hits, root))
            else:
                sections.append(self._source_snippets(symbol_hits, root))
        if RetrievalMode.GRAPH in modes or RetrievalMode.IMPACT in modes:
            sections.append(self._call_graph(cls, symbol_hits))
        if RetrievalMode.IMPACT in modes:
            sections.append(self._impact(cls))
        if RetrievalMode.SEMANTIC in modes:
            sections.append(self._semantic(cls))
        if RetrievalMode.ARCHITECTURE in modes:
            sections.append(self._architecture())
        if RetrievalMode.SQL in modes or RetrievalMode.DATA_FLOW in modes:
            sections.append(self._sql(cls))
        if RetrievalMode.DATA_FLOW in modes or cls.qtype.value in ("DYNAMIC_SQL", "SQL", "DEBUGGING"):
            sections.append(self._dynamic_sql(cls))
        sections.append(self._configuration(cls))
        if RetrievalMode.SEMANTIC in modes or RetrievalMode.ARCHITECTURE in modes:
            sections.append(self._documentation(cls))
        sections.append(self._uncertainties())

        cfg = get_settings().llm
        kept, dropped = ContextBudgetManager(cfg.context_char_budget).select(
            [s for s in sections if s is not None]
        )
        evidence: list[Evidence] = []
        for s in kept:
            evidence.extend(s.evidence)
        return BuiltContext(
            project_name=project_name,
            classification=cls,
            sections=kept,
            evidence=_dedupe_evidence(evidence),
            dropped_sections=dropped,
            char_budget=cfg.context_char_budget,
        )

    # -------------------------------------------------------------- retrieval
    def _symbol_hits(self, cls: Classification) -> list[dict]:
        seen: dict[str, dict] = {}
        queries = list(cls.symbols) or []
        if not queries:
            queries = [w for w in cls.question.replace("?", " ").split() if len(w) > 3][:4]
        for q in queries:
            for hit in S.search_symbols(self.pid, q, limit=8, conn=self.conn):
                seen.setdefault(hit["qualified"], hit)
        return list(seen.values())[:12]

    def _project_summary(self) -> Section:
        c = self.conn
        def n(t: str) -> int:
            return c.execute(f"SELECT COUNT(*) FROM {t} WHERE project_id=?", (self.pid,)).fetchone()[0]
        sec = Section("PROJECT SUMMARY", priority=5)
        sec.lines = [
            f"files={n('files')} classes={n('classes')} methods={n('methods')} "
            f"sql_queries={n('sql_queries')} dynamic_sql_sites={n('dynamic_sql')}",
            "Java + SQL project. Index is deterministic (AST + SQL parser + dynamic-SQL analyzer).",
        ]
        pkgs = [r["name"] for r in c.execute(
            "SELECT DISTINCT name FROM packages WHERE project_id=? LIMIT 10", (self.pid,))]
        if pkgs:
            sec.lines.append("packages: " + ", ".join(pkgs))
        return sec

    def _classes_methods(self, hits: list[dict]) -> Section:
        sec = Section("RELEVANT CLASSES AND METHODS", priority=2)
        for h in hits:
            loc = f'{h["file"]}:{h["line_number"]}' if h.get("line_number") else h["file"]
            sec.lines.append(f'- {h["kind"]} {h["qualified"]}  ({loc})')
            sec.evidence.append(Evidence(kind=h["kind"], detail=h["qualified"], file=h["file"], line=h.get("line_number")))
        return sec

    def _source_snippets(self, hits: list[dict], root: Path | None) -> Section:
        sec = Section("SOURCE", priority=1)
        if root is None:
            return sec
        budget_lines = _MAX_SNIPPET_LINES
        for h in hits[:4]:
            if h["kind"] not in ("method", "class", "interface"):
                continue
            path = root / h["file"]
            if not path.is_file():
                continue
            try:
                src = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            start = max((h.get("line_number") or 1) - 1, 0)
            end = min(start + budget_lines, len(src))
            snippet = "\n".join(src[start:end])
            sec.lines.append(f'// {h["qualified"]} — {h["file"]}:{start + 1}\n{snippet}')
            sec.evidence.append(Evidence(kind="source", detail=h["qualified"], file=h["file"], line=start + 1))
        return sec

    def _full_method_source(self, cls: Classification, hits: list[dict], root: Path | None) -> Section:
        """Full body of the method(s) the question names — for a line-by-line
        walkthrough the model must see every statement, numbered."""
        sec = Section("METHOD SOURCE (numbered, complete)", priority=1)
        if root is None:
            return sec

        wanted = {s.split(".")[-1].lower() for s in cls.symbols}
        conn = self.conn
        rows = []
        for name in (wanted or {h["qualified"].split(".")[-1].lower() for h in hits if h["kind"] == "method"}):
            rows += [dict(r) for r in conn.execute(
                "SELECT m.name, m.line_start, m.line_end, m.signature, c.name AS cname, f.path AS fpath "
                "FROM methods m LEFT JOIN classes c ON c.id=m.class_id JOIN files f ON f.id=m.file_id "
                "WHERE m.project_id=? AND lower(m.name)=? LIMIT 3", (self.pid, name))]

        if not rows:  # fall back to the snippet section's behaviour
            return self._source_snippets(hits, root)

        for r in rows[:2]:
            p = root / r["fpath"]
            if not p.is_file():
                continue
            src = p.read_text(encoding="utf-8", errors="replace").splitlines()
            a = max((r["line_start"] or 1) - 1, 0)
            b = min(r["line_end"] or (a + 400), len(src))
            numbered = "\n".join(f"{a + i + 1:>5}  {ln}" for i, ln in enumerate(src[a:b]))
            sec.lines.append(f'// {r["cname"]}.{r["name"]}{r["signature"] or ""} — {r["fpath"]}:{a + 1}-{b}\n{numbered}')
            sec.evidence.append(Evidence(kind="source", detail=f'{r["cname"]}.{r["name"]}',
                                         file=r["fpath"], line=r["line_start"]))
        return sec

    def _call_graph(self, cls: Classification, hits: list[dict]) -> Section:
        sec = Section("CALL GRAPH", priority=3)
        names = {h["qualified"].split(".")[-1] for h in hits if h["kind"] == "method"} | set(cls.symbols)
        for name in list(names)[:6]:
            simple = name.split(".")[-1]
            callers = S.find_callers(self.pid, simple, conn=self.conn)
            for cr in callers[:6]:
                who = f'{cr.get("caller_class") or "?"}.{cr.get("caller_method") or "?"}'
                sec.lines.append(f'- {who} -> {simple}()  ({cr["file"]}:{cr["line_number"]})')
                sec.evidence.append(Evidence(kind="call", detail=f"{who} -> {simple}()",
                                             file=cr["file"], line=cr["line_number"]))
        return sec

    def _sql(self, cls: Classification) -> Section:
        sec = Section("SQL QUERIES AND TABLES", priority=3)
        terms = list(cls.tables) + list(cls.symbols)
        if not terms:
            terms = [w for w in cls.question.split() if w.isalpha() and len(w) > 3][:3]
        seen_q: set[int] = set()
        for term in terms:
            for q in S.search_sql(self.pid, term, limit=6, conn=self.conn):
                if q["id"] in seen_q:
                    continue
                seen_q.add(q["id"])
                sec.lines.append(f'- [{q["query_type"]}/{q["resolution_status"]}] {q["file"]}:{q["line_number"]}  '
                                 f'{(q["raw_sql"] or "")[:180]}')
                sec.evidence.append(Evidence(kind="sql", detail=(q["raw_sql"] or "")[:120],
                                             file=q["file"], line=q["line_number"]))
        for tbl in cls.tables:
            for u in S.find_table_usage(self.pid, tbl, conn=self.conn)[:6]:
                sec.lines.append(f'- table {u["table_name"]} {u["access"]} by {u.get("source_class")}.'
                                 f'{u.get("source_method")}  ({u["file"]}:{u["line_number"]})')
                sec.evidence.append(Evidence(kind="table_use", detail=f'{u["table_name"]} ({u["access"]})',
                                             file=u["file"], line=u["line_number"]))
        return sec

    def _dynamic_sql(self, cls: Classification) -> Section:
        sec = Section("DYNAMIC SQL RESOLUTION AND METADATA DEPENDENCIES", priority=2)
        terms = list(cls.symbols) + list(cls.tables)
        sites = S.list_dynamic_sql(self.pid, conn=self.conn)
        if terms:
            filt = []
            for d in sites:
                hay = f'{d["source_class"]}.{d["source_method"]} {d["sql_template"]} {" ".join(d["tables"])}'.lower()
                if any(t.lower() in hay for t in terms):
                    filt.append(d)
            sites = filt or sites
        for d in sites[:5]:
            meta_tables = sorted({
                t for dep in d["dependencies"] if dep["dependency_type"] == "METADATA_QUERY"
                for e in dep.get("evidence", []) for t in (e.get("metadata_tables") or [])
            })
            suffix = f' — metadata tables: {", ".join(meta_tables)}' if meta_tables else ""
            sec.lines.append(
                f'- {d["source_class"]}.{d["source_method"]} ({d["file"]}:{d["line"]}) '
                f'[{d["status"]}, confidence {d["confidence"]}]{suffix}'
            )
            sec.lines.append(f'    template: {d["sql_template"]}')
            if d["resolved_sql"]:
                sec.lines.append(f'    resolved: {d["resolved_sql"]}')
            for dep in d["dependencies"]:
                meta = "; ".join(
                    f'metadata SQL: {e.get("metadata_sql")} -> {", ".join(e.get("metadata_tables") or [])}'
                    for e in dep.get("evidence", []) if e.get("metadata_sql")
                )
                sec.lines.append(f'    dep {dep["dependency_type"]} [{dep["resolution_status"]}] '
                                 f'{dep["value"]}{("  (" + meta + ")") if meta else ""}')
            sec.evidence.append(Evidence(kind="dynamic_sql",
                                         detail=f'{d["source_class"]}.{d["source_method"]} [{d["status"]}]',
                                         file=d["file"], line=d["line"]))
        return sec

    def _semantic(self, cls: Classification) -> Section:
        sec = Section("SEMANTICALLY RELATED CODE", priority=3)
        try:
            from backend.app.retrieval.semantic import SemanticIndex
            hits = SemanticIndex(conn=self.conn).search(self.pid, cls.question, k=6)
        except Exception:
            return sec
        for h in hits:
            if h["score"] < 0.15:
                continue
            sec.lines.append(f'- [{h["chunk_type"]}] {h["symbol"]}  ({h["file"]}:{h.get("line_start")})  '
                             f'score={round(h["score"], 3)}')
            sec.evidence.append(Evidence(kind="semantic", detail=h["symbol"],
                                         file=h["file"], line=h.get("line_start")))
        return sec

    def _impact(self, cls: Classification) -> Section:
        sec = Section("IMPACT (GRAPH)", priority=2)
        target = (cls.symbols or cls.tables or [None])[0]
        if not target:
            return sec
        try:
            from backend.app.graph.service import GraphService
            res = GraphService(self.pid, conn=self.conn).impact_analysis(target).as_dict()
        except Exception:
            return sec
        sec.lines.append(f'target: {res["target"]}')
        for label in ("direct_impact", "indirect_impact", "sql_impact", "table_impact", "test_impact", "unknowns"):
            vals = res.get(label) or []
            if vals:
                sec.lines.append(f'{label}: ' + ", ".join(vals[:20]))
        return sec

    def _architecture(self) -> Section:
        sec = Section("ARCHITECTURE", priority=3)
        rows = [dict(r) for r in self.conn.execute(
            "SELECT class_name, role, layer, file FROM architecture_components WHERE project_id=? "
            "ORDER BY layer, class_name", (self.pid,))]
        by_layer: dict[str, list[str]] = {}
        for r in rows:
            by_layer.setdefault(r["layer"], []).append(f'{r["class_name"]}({r["role"]})')
        for layer, members in by_layer.items():
            sec.lines.append(f'- {layer}: ' + ", ".join(members))
        return sec

    def _configuration(self, cls: Classification) -> Section:
        sec = Section("CONFIGURATION", priority=4)
        terms = [t.lower() for t in (list(cls.symbols) + list(cls.tables))] or \
                [w.lower() for w in cls.question.split() if len(w) > 3][:3]
        for term in terms:
            for r in self.conn.execute(
                "SELECT ce.key, ce.value, ce.is_secret, f.path FROM config_entries ce "
                "JOIN files f ON f.id = ce.file_id WHERE ce.project_id=? AND lower(ce.key) LIKE ? LIMIT 8",
                (self.pid, f"%{term}%"),
            ):
                val = "***REDACTED***" if r["is_secret"] else r["value"]
                sec.lines.append(f'- {r["key"]} = {val}   ({r["path"]})')
                sec.evidence.append(Evidence(kind="config", detail=f'{r["key"]}={val}', file=r["path"]))
        return sec

    def _documentation(self, cls: Classification) -> Section:
        sec = Section("DOCUMENTATION", priority=4)
        terms = [w for w in cls.question.split() if w.isalpha() and len(w) > 3][:4]
        for term in terms:
            for r in self.conn.execute(
                "SELECT d.title, d.content, f.path FROM documents d JOIN files f ON f.id=d.file_id "
                "WHERE d.project_id=? AND d.content LIKE ? LIMIT 2",
                (self.pid, f"%{term}%"),
            ):
                sec.lines.append(f'- {r["path"]}: {(r["content"] or "")[:400]}')
                sec.evidence.append(Evidence(kind="doc", detail=r["title"] or r["path"], file=r["path"]))
        return sec

    def _uncertainties(self) -> Section:
        sec = Section("KNOWN UNCERTAINTIES", priority=3)
        for r in self.conn.execute(
            "SELECT source_class, source_method, resolution_status FROM dynamic_sql "
            "WHERE project_id=? AND resolution_status!='RESOLVED'", (self.pid,)):
            sec.lines.append(f'- {r["source_class"]}.{r["source_method"]} dynamic SQL is {r["resolution_status"]}; '
                             f'runtime value cannot be determined statically.')
        failed = self.conn.execute(
            "SELECT COUNT(*) FROM parse_failures WHERE project_id=?", (self.pid,)).fetchone()[0]
        if failed:
            sec.lines.append(f'- {failed} file(s) failed to parse and are absent from the index.')
        return sec


def _dedupe_evidence(items: list[Evidence]) -> list[Evidence]:
    seen, out = set(), []
    for e in items:
        k = (e.kind, e.detail, e.file, e.line)
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out
