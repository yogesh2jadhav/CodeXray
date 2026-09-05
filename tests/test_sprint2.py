"""
tests/test_sprint2.py

Purpose
-------
Regression tests for the Sprint 2 dynamic-SQL analyzer (build plan §56, §66, §83).

Responsibility
--------------
Against the bundled synthetic project, assert that:
  * StringBuilder and string-concatenation construction sites are found.
  * The interprocedural site in `CustomerService.loadEligibleCustomers`
    reconstructs a template and traces its table/columns to metadata queries.
  * Status is PARTIALLY_RESOLVED and no concrete table name is invented (§29).
  * Dependencies use the plan's vocabulary (CONSTANT / METADATA_QUERY / PARAMETER).
  * `trace_dynamic_sql` returns evidence for a Class.method selector.
  * The whole thing is persisted and reachable via the search helpers.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SYNTH = REPO / "projects" / "synthetic-test-project"


@pytest.fixture()
def indexed(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEXRAY_INDEX_DB", str(tmp_path / "idx.db"))
    from backend.app.config.settings import get_settings
    get_settings.cache_clear()
    from backend.app.indexing.indexer import ProjectIndexer
    from backend.app.models.database import get_connection
    report = ProjectIndexer().index_project("synthetic", SYNTH, force=True)
    return report, get_connection()


def _java_files():
    return [
        (str(p.relative_to(SYNTH)), str(p))
        for p in SYNTH.rglob("*.java")
    ]


def test_analyzer_finds_construction_sites():
    from backend.app.analyzers.dynamic_sql.analyzer import DynamicSqlAnalyzer
    recs = DynamicSqlAnalyzer().analyze_project(_java_files())
    by_key = {(r.source_class, r.source_method): r for r in recs}

    assert ("DynamicQueryBuilder", "buildWithConcat") in by_key
    assert ("DynamicQueryBuilder", "buildWithBuilder") in by_key
    assert ("CustomerService", "loadEligibleCustomers") in by_key


def test_interprocedural_metadata_trace():
    from backend.app.analyzers.dynamic_sql.analyzer import DynamicSqlAnalyzer
    from backend.app.models.records import ResolutionStatus

    recs = DynamicSqlAnalyzer().analyze_project(_java_files())
    site = next(r for r in recs if (r.source_class, r.source_method) == ("CustomerService", "loadEligibleCustomers"))

    assert site.resolution_status is ResolutionStatus.PARTIALLY_RESOLVED
    assert site.resolved_sql is None                       # §29 — nothing invented
    assert "{table}" in site.sql_template and "{columns}" in site.sql_template

    dep_types = {d.dependency_type for d in site.dependencies}
    assert "METADATA_QUERY" in dep_types
    assert "CONSTANT" in dep_types

    meta_tables = {
        t
        for d in site.dependencies if d.dependency_type == "METADATA_QUERY"
        for e in d.evidence for t in e.get("metadata_tables", [])
    }
    assert {"META_TABLE_REGISTRY", "META_COLUMN_REGISTRY"} <= meta_tables

    # The physical CUSTOMER table must NOT appear as a resolved table.
    assert "CUSTOMER" not in [t.upper() for t in site.tables]


def test_unresolved_parameter_is_not_resolved():
    from backend.app.analyzers.dynamic_sql.analyzer import DynamicSqlAnalyzer
    from backend.app.models.records import ResolutionStatus

    recs = DynamicSqlAnalyzer().analyze_project(_java_files())
    concat = next(r for r in recs if (r.source_class, r.source_method) == ("DynamicQueryBuilder", "buildWithConcat"))
    assert concat.resolution_status is ResolutionStatus.PARTIALLY_RESOLVED
    param_deps = [d for d in concat.dependencies if d.dependency_type == "PARAMETER"]
    assert {d.value for d in param_deps} == {"table", "columns"}
    assert all(d.resolution_status == "UNRESOLVED" for d in param_deps)


def test_persisted_and_traceable(indexed):
    report, conn = indexed
    assert report.dynamic_sql_sites >= 3

    from backend.app.retrieval import search as S
    sites = S.list_dynamic_sql(report.project_id)
    assert any(s["source_method"] == "loadEligibleCustomers" for s in sites)

    trace = S.trace_dynamic_sql(report.project_id, "CustomerService.loadEligibleCustomers")
    assert trace["status"] == "PARTIALLY_RESOLVED"
    assert trace["match_count"] >= 1
    site = trace["sites"][0]
    assert any(d["dependency_type"] == "METADATA_QUERY" for d in site["dependencies"])


def test_api_trace_endpoint(indexed):
    report, _ = indexed
    from fastapi.testclient import TestClient
    from backend.app.main import app

    c = TestClient(app)
    r = c.post(f"/api/projects/{report.project_id}/dynamic-sql/trace",
               json={"selector": "META_TABLE_REGISTRY"})
    assert r.status_code == 200
    body = r.json()
    assert body["match_count"] >= 1
    assert body["status"] in {"PARTIALLY_RESOLVED", "RESOLVED"}
