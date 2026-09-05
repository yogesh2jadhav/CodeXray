"""
tests/test_sprint4.py

Purpose
-------
Regression tests for Sprint 4 — dependency graph + semantic retrieval +
architecture extraction (build plan §19, §23, §36, §39, §41, §57, §58).

Responsibility
--------------
Against the bundled synthetic project (hashing embedder — offline, deterministic):
  * the graph builder emits typed edges; callers/callees/path/table-consumers work;
  * `impact_analysis` returns direct callers + reachable SQL/tables + unknowns for
    the partially-resolved dynamic SQL;
  * the semantic index builds and retrieves the eligibility flow without keyword
    overlap;
  * hybrid search blends signals and is deterministic;
  * architecture extraction assigns the expected roles/layers;
  * the new API endpoints respond.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SYNTH = REPO / "projects" / "synthetic-test-project"


@pytest.fixture()
def indexed(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEXRAY_INDEX_DB", str(tmp_path / "idx.db"))
    monkeypatch.setenv("CODEXRAY_EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("CODEXRAY_LLM_PROVIDER", "echo")
    from backend.app.config.settings import get_settings
    get_settings.cache_clear()
    from backend.app.indexing.indexer import ProjectIndexer
    return ProjectIndexer().index_project("synthetic", SYNTH, force=True)


# --------------------------------------------------------------------- graph
def test_graph_build_and_queries(indexed):
    assert indexed.graph_edges > 20
    from backend.app.graph.service import GraphService
    gs = GraphService(indexed.project_id)

    assert "CustomerService.loadEligibleCustomers" in gs.callers("getPhysicalTable")
    callees = gs.callees("loadEligibleCustomers")
    assert {"MetadataService.getPhysicalTable", "MetadataService.getColumnList",
            "DynamicQueryBuilder.buildQuery"} <= set(callees)

    path = gs.call_path("processCustomer", "getPhysicalTable")
    assert path == ["CustomerService.processCustomer", "CustomerService.loadEligibleCustomers",
                    "MetadataService.getPhysicalTable"]

    cons = gs.table_consumers("CUSTOMER")
    assert any("CustomerRepository" in r for r in cons["readers"])


def test_impact_analysis(indexed):
    from backend.app.graph.service import GraphService
    res = GraphService(indexed.project_id).impact_analysis("loadEligibleCustomers").as_dict()
    assert "CustomerService.processCustomer" in res["direct_impact"]
    assert {"META_TABLE_REGISTRY", "META_COLUMN_REGISTRY"} <= set(res["table_impact"])
    assert any("dynamic SQL" in u for u in res["unknowns"])


# ----------------------------------------------------------------- semantic
def test_semantic_index_and_search(indexed):
    assert indexed.semantic_chunks > 10
    from backend.app.retrieval.semantic import SemanticIndex
    si = SemanticIndex()
    hits = si.search(indexed.project_id, "determine physical table name from metadata registry", k=8)
    assert hits
    joined = " ".join(f'{h["symbol"]} {h["file"]}' for h in hits)
    assert "MetadataService" in joined or "META_TABLE_REGISTRY" in joined


def test_hybrid_search_deterministic(indexed):
    from backend.app.retrieval.hybrid import hybrid_search
    a = hybrid_search(indexed.project_id, "customer eligibility", k=6)
    b = hybrid_search(indexed.project_id, "customer eligibility", k=6)
    assert [x["symbol"] for x in a] == [x["symbol"] for x in b]
    assert a and all("signals" in x and x["score"] > 0 for x in a)


# ------------------------------------------------------------- architecture
def test_architecture_roles(indexed):
    from backend.app.analyzers.architecture.extractor import ArchitectureExtractor
    arch = ArchitectureExtractor().extract(indexed.project_id).as_dict()
    roles = {c["name"]: c["role"] for c in arch["components"]}
    assert roles["CustomerRepository"] == "REPOSITORY"
    assert roles["MetadataService"] == "SERVICE"
    assert roles["DynamicQueryBuilder"] == "FACTORY"
    assert roles["QueryConstants"] == "CONSTANTS"
    assert "CustomerRepository" in arch["data_access"]
    assert "Data Access" in arch["layers"]


# --------------------------------------------------------------------- API
def test_sprint4_endpoints(indexed):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    c = TestClient(app)
    pid = indexed.project_id

    r = c.get(f"/api/projects/{pid}/graph/callers", params={"symbol": "getPhysicalTable"})
    assert r.status_code == 200 and "CustomerService.loadEligibleCustomers" in r.json()["callers"]

    r = c.post(f"/api/projects/{pid}/impact-analysis", json={"symbol": "loadEligibleCustomers"})
    assert r.status_code == 200 and r.json()["direct_impact"]

    r = c.get(f"/api/projects/{pid}/architecture")
    assert r.status_code == 200 and "layers" in r.json()

    r = c.get(f"/api/projects/{pid}/architecture", params={"format": "md"})
    assert r.status_code == 200 and "# Architecture" in r.text

    r = c.post(f"/api/projects/{pid}/search", json={"query": "eligibility", "mode": "hybrid", "limit": 5})
    assert r.status_code == 200 and isinstance(r.json(), list)

    r = c.post(f"/api/projects/{pid}/search", json={"query": "metadata table lookup", "mode": "semantic", "limit": 5})
    assert r.status_code == 200 and r.json()
