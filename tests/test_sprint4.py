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


# --------------------------------------------------------------- graph export
def test_graph_export_formats(indexed):
    from backend.app.graph.export import GraphExporter
    exp = GraphExporter(indexed.project_id)

    kinds = {n.kind for n in exp.nodes.values()}
    assert {"Module", "Package", "Class", "Method", "Table", "SqlQuery"} <= kinds
    # JDBC / stdlib types are filtered out by default
    labels = {n.label for n in exp.nodes.values()}
    assert "ArrayList" not in labels and "Connection" not in labels
    assert {"CustomerService", "MetadataService", "DynamicQueryBuilder"} <= labels

    cy = exp.to_cypher()
    assert "MERGE (n:Method {id:" in cy and "MERGE (a)-[:CALLS]->(b)" in cy
    assert exp.to_graphml().startswith("<?xml")
    assert exp.to_dot().startswith("digraph codexray {")
    j = exp.to_cytoscape_json()
    assert j["stats"]["nodes"] == len(exp.nodes) and j["elements"]["edges"]

    # a Method node carries its one-line purpose + class
    m = next(n for n in exp.nodes.values() if n.label == "CustomerService.loadEligibleCustomers")
    assert m.props.get("class") == "CustomerService" and m.props.get("purpose")


def test_graph_export_endpoint(indexed):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    c = TestClient(app)
    r = c.get(f"/api/projects/{indexed.project_id}/graph/export", params={"format": "cypher"})
    assert r.status_code == 200 and "MERGE" in r.text
    r = c.get(f"/api/projects/{indexed.project_id}/graph/export", params={"format": "json"})
    assert r.status_code == 200 and r.json()["stats"]["nodes"] > 10


# --------------------------------------------------------- project documentation
def test_project_documentation_generator(indexed):
    from backend.app.analyzers.documentation.generator import ProjectDocGenerator
    doc = ProjectDocGenerator(indexed.project_id).generate(use_llm=False)

    assert doc.purpose_source == "heuristic" and doc.purpose
    assert doc.counts["classes"] == 5 and doc.counts["sql_queries"] > 0
    assert "com.acme.customer" in {p["package"] for p in doc.packages}

    # class-level purposes come from real javadoc, not a constructor stub
    md_repo = next(c for c in doc.key_classes["Data Access"] if c["name"] == "CustomerRepository")
    assert md_repo["purpose"] and "constructor" not in md_repo["purpose"].lower()

    # dependency grouping collapses to top-level packages (java.sql, not java.sql.Connection)
    dep_names = {d["name"] for d in doc.dependencies}
    assert "java.sql" in dep_names and not any(d.count(".") > 1 for d in dep_names)

    # hotspots never include JDBC/stdlib bare-name calls
    assert not any("executeQuery" in h or "prepareStatement" in h for h in doc.hotspots)
    assert any("metadata lookups" in h for h in doc.hotspots)

    md = doc.to_markdown()
    assert md.startswith("# synthetic — Project Summary")
    assert "## Dynamic SQL" in md and "META_TABLE_REGISTRY" in md


def test_project_documentation_endpoint(indexed):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    c = TestClient(app)
    r = c.get(f"/api/projects/{indexed.project_id}/documentation")
    assert r.status_code == 200 and r.text.startswith("# synthetic")

    r = c.get(f"/api/projects/{indexed.project_id}/documentation", params={"format": "json"})
    assert r.status_code == 200 and r.json()["counts"]["classes"] == 5


# ------------------------------------------------- scoped / large-project docs
def test_documentation_module_scoping(indexed):
    from backend.app.analyzers.documentation.generator import ProjectDocGenerator
    gen = ProjectDocGenerator(indexed.project_id)

    mods = gen.list_modules()
    assert mods and mods[0]["module"] == "com.acme" and mods[0]["class_count"] == 5

    full = gen.generate(use_llm=False)
    scoped = gen.generate(use_llm=False, package_prefix="com.acme.customer")

    # scoping narrows SQL to what's attributable to classes in that package —
    # the .sql-file-origin queries and CUSTOMER_ADDRESS table drop out
    assert scoped.counts["sql_queries"] < full.counts["sql_queries"]
    assert "CUSTOMER_ADDRESS" not in scoped.sql_summary["tables"]
    assert scoped.scope == "com.acme.customer"
    assert "com.acme.customer module" in scoped.purpose

    md = scoped.to_markdown()
    assert md.startswith("# synthetic — com.acme.customer")


def test_documentation_caps_are_honest(indexed):
    """Every list capped for a large doc must report shown vs total, never
    silently drop data."""
    from backend.app.analyzers.documentation.generator import ProjectDoc

    doc = ProjectDoc(
        project="p", generated_at="now", scope=None, purpose="x", purpose_source="heuristic",
        counts={"classes": 5000}, packages=[{"package": "a", "class_count": 1}], packages_total=500,
        is_large=True,
    )
    md = doc.to_markdown()
    assert "Large codebase" in md
    assert "499 more package(s)" in md


def test_documentation_modules_endpoint(indexed):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    c = TestClient(app)
    r = c.get(f"/api/projects/{indexed.project_id}/documentation/modules")
    assert r.status_code == 200 and r.json()[0]["module"] == "com.acme"

    r = c.get(f"/api/projects/{indexed.project_id}/documentation",
              params={"package": "com.acme.customer", "format": "json"})
    assert r.status_code == 200 and r.json()["scope"] == "com.acme.customer"
