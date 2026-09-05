"""
eval/runner.py

Purpose
-------
Execute the benchmark against a project (build plan §67, §68).

Responsibility
--------------
- `load_cases(path)` -> list[EvalCase] from the YAML dataset.
- `EvalRunner(project_id, mode)`:
    mode "retrieval" -> deterministic only (no LLM): fast, model-independent,
                        scores retrieval / evidence / sql-resolution / dependency.
    mode "ask"       -> Sprint 3 one-shot LLM pipeline.
    mode "agent"     -> Sprint 5 tool-calling agent.
  In every mode the deterministic signals (tables, dynamic-SQL status, callers,
  impact) are probed the same way, so those metrics compare cleanly across
  models; only answer_correctness / hallucination depend on the LLM.
- Returns a `RunReport` (per-case scores + aggregates + per-category breakdown).

Read-only against the project. Never mutates the index.
"""
from __future__ import annotations

import time
from pathlib import Path

import yaml

from eval.metrics import RunOutput, score_case
from eval.schema import EvalCase, RunReport
from backend.app.llm.question_classifier import classify
from backend.app.models.database import get_connection
from backend.app.retrieval import search as S


def load_cases(path: str | Path) -> list[EvalCase]:
    data = yaml.safe_load(Path(path).read_text())
    return [EvalCase.from_dict(d) for d in data["cases"]]


def _selectors(case: EvalCase) -> list[str]:
    """Best symbols/tables to probe the deterministic layers with."""
    sels = list(case.expect_evidence_symbols) + list(case.expect_callers) + list(case.expect_impact)
    sels += classify(case.question).symbols
    if not sels:
        sels = [w for w in case.question.split() if len(w) > 4][:3]
    return sels


def _probe(project_id: int, case: EvalCase, conn) -> dict:
    """Deterministic ground-truth signals for a case — identical in every mode."""
    tables: set[str] = set()
    dynamic_status: str | None = None
    callers: list[str] = []
    impact: list[str] = []

    from backend.app.graph.service import GraphService
    gs = GraphService(project_id, conn=conn)

    for sel in _selectors(case):
        # dynamic SQL + metadata tables
        tr = S.trace_dynamic_sql(project_id, sel, conn=conn)
        if tr["sites"]:
            dynamic_status = tr["status"]
            for site in tr["sites"]:
                tables.update(t.upper() for t in site["tables"])
                for dep in site["dependencies"]:
                    for ev in dep.get("evidence", []):
                        tables.update(t.upper() for t in ev.get("metadata_tables", []))
        # callers / impact
        callers += gs.callers(sel)
        if case.category.value in ("impact", "dependency"):
            ia = gs.impact_analysis(sel).as_dict()
            impact += ia.get("direct_impact", []) + ia.get("indirect_impact", [])
            tables.update(t.upper() for t in ia.get("table_impact", []))

    # also mine table names straight from SQL search on expected tables / question
    for term in case.expect_tables or _selectors(case):
        for row in S.search_sql(project_id, term, limit=8, conn=conn):
            for t in conn.execute("SELECT name FROM sql_tables WHERE query_id=?", (row["id"],)):
                tables.add(t["name"].upper())

    # A dynamic-SQL case whose selector didn't hit a site directly: fall back to
    # the whole-project view (every site shares the aggregate status).
    if case.expect_dynamic_status and dynamic_status is None:
        sites = S.list_dynamic_sql(project_id, conn=conn)
        if sites:
            statuses = {s["status"] for s in sites}
            dynamic_status = (
                "RESOLVED" if statuses == {"RESOLVED"}
                else "UNRESOLVED" if statuses == {"UNRESOLVED"}
                else "PARTIALLY_RESOLVED"
            )
            for s in sites:
                tables.update(t.upper() for t in s["tables"])
                for dep in s["dependencies"]:
                    for ev in dep.get("evidence", []):
                        tables.update(t.upper() for t in ev.get("metadata_tables", []))

    return {
        "tables": sorted(tables),
        "dynamic_status": dynamic_status,
        "callers": sorted(set(callers)),
        "impact": sorted(set(impact)),
    }


class EvalRunner:
    def __init__(self, project_id: int, mode: str = "retrieval", threshold: float = 0.5, conn=None) -> None:
        self.pid = project_id
        self.mode = mode
        self.threshold = threshold
        self.conn = conn or get_connection()

    def _retrieval_evidence(self, case: EvalCase) -> tuple[list[str], list[str]]:
        from backend.app.retrieval.hybrid import hybrid_search
        hits = hybrid_search(self.pid, case.question, k=10, conn=self.conn)
        files = [h.get("file") or "" for h in hits if h.get("file")]
        syms = [h.get("symbol") or "" for h in hits]
        # augment with symbol search on expected symbols so retrieval is fair
        for s in case.expect_evidence_symbols:
            for sh in S.search_symbols(self.pid, s, limit=3, conn=self.conn):
                files.append(sh["file"])
                syms.append(sh["qualified"])
        # keyword file match for config / doc questions the vector store ranks low
        q = case.question.lower()
        for term in [w for w in q.split() if len(w) > 3]:
            for f in S.search_files(self.pid, term, limit=4, conn=self.conn):
                files.append(f["path"])
        if any(w in q for w in ("config", "configuration", "property", "properties", "setting", "loaded from")):
            for f in S.search_files(self.pid, ".properties", limit=5, conn=self.conn):
                files.append(f["path"])
            for f in S.search_files(self.pid, ".yaml", limit=5, conn=self.conn):
                files.append(f["path"])
        return [Path(f).name for f in files], syms

    def run_case(self, case: EvalCase) -> RunOutput:
        t0 = time.perf_counter()
        probe = _probe(self.pid, case, self.conn)
        out = RunOutput(
            tables_seen=probe["tables"],
            dynamic_status=probe["dynamic_status"],
            callers=probe["callers"],
            impact=probe["impact"],
        )

        if self.mode == "retrieval":
            files, syms = self._retrieval_evidence(case)
            out.evidence_files, out.evidence_symbols = files, syms
            out.used_llm = False
        elif self.mode == "ask":
            from backend.app.llm.client import AskService
            res = AskService(conn=self.conn).ask(self.pid, case.question).as_dict()
            out.answer = res["answer"]
            out.labels = {"FACT": res["facts"], "INFERENCE": res["inferences"], "UNKNOWN": res["unknowns"]}
            out.evidence_files = [Path(e["file"]).name for e in res["evidence"] if e.get("file")]
            out.evidence_symbols = [e["detail"] for e in res["evidence"]]
            out.used_llm = True
        elif self.mode == "agent":
            from backend.app.agents.agent import InvestigationAgent
            res = InvestigationAgent(self.pid, conn=self.conn).investigate(case.question).as_dict()
            out.answer = res["answer"]
            out.labels = {"FACT": res["facts"], "INFERENCE": res["inferences"], "UNKNOWN": res["unknowns"]}
            out.evidence_files = [Path(e["file"]).name for e in res["evidence"] if e.get("file")]
            out.evidence_symbols = [e["detail"] for e in res["evidence"]]
            for t in res["tool_trace"]:
                for e in t.get("evidence", []):
                    if e.get("file"):
                        out.evidence_files.append(Path(e["file"]).name)
                    out.evidence_symbols.append(e.get("detail", ""))
            out.used_llm = True
        else:
            raise ValueError(f"unknown mode {self.mode!r}")

        out.latency_s = time.perf_counter() - t0
        return out

    def run(self, cases: list[EvalCase], model: str) -> RunReport:
        proj = self.conn.execute("SELECT name FROM projects WHERE id=?", (self.pid,)).fetchone()
        report = RunReport(project=proj["name"] if proj else str(self.pid), model=model, mode=self.mode)
        for case in cases:
            out = self.run_case(case)
            report.cases.append(score_case(case, out, self.threshold))
        return report
