"""
tests/test_sprint5.py

Purpose
-------
Regression tests for the Sprint 5 tool-calling investigation agent
(build plan §26, §27, §28, §60).

Responsibility
--------------
Deterministically (echo provider — the LLM tool loop can't plan, so the agent
falls back to the seed plan, which is exactly what we assert on):
  * the tool registry runs each read-only tool and returns evidence;
  * the planner produces the §27-style tool sequence per question type;
  * the agent executes the plan, aggregates evidence, and never raises;
  * `trace_metadata_dependency` surfaces the META_* chain;
  * `/investigate` and `/agent/tools` respond; `/investigate` is 503 when the
    model is down.
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


# ------------------------------------------------------------------- tools
def test_tool_registry_runs_readonly_tools(indexed):
    from backend.app.agents.tools import ToolRegistry
    reg = ToolRegistry(indexed.project_id)

    names = {t["name"] for t in reg.catalogue()}
    assert {"search_code", "trace_dynamic_sql", "impact_analysis", "find_table_usage",
            "get_project_architecture", "trace_metadata_dependency"} <= names

    r = reg.run("trace_dynamic_sql", {"selector": "loadEligibleCustomers"})
    assert r.ok and r.data["status"] == "PARTIALLY_RESOLVED"

    r = reg.run("find_table_usage", {"table": "CUSTOMER"})
    assert r.ok and r.data["usage"]

    r = reg.run("get_method", {"name": "CustomerService.loadEligibleCustomers"})
    assert r.ok and "getPhysicalTable" in r.data["methods"][0]["source"]

    r = reg.run("does_not_exist", {})
    assert not r.ok and "unknown tool" in r.error


def test_trace_metadata_dependency_tool(indexed):
    from backend.app.agents.tools import ToolRegistry
    r = ToolRegistry(indexed.project_id).run("trace_metadata_dependency", {"selector": "loadEligibleCustomers"})
    assert r.ok
    tables = {t for ch in r.data["metadata_chains"] for t in ch["metadata_tables"]}
    assert {"META_TABLE_REGISTRY", "META_COLUMN_REGISTRY"} <= tables


# ----------------------------------------------------------------- planner
def test_planner_sequences_per_type():
    from backend.app.agents.planner import plan
    from backend.app.llm.question_classifier import classify

    p = [c.tool for c in plan(classify("How is the table name for loadEligibleCustomers determined?"))]
    assert "trace_dynamic_sql" in p and "trace_metadata_dependency" in p

    p2 = [c.tool for c in plan(classify("What could be impacted if I change loadEligibleCustomers?"))]
    assert "impact_analysis" in p2

    p3 = [c.tool for c in plan(classify("Explain this project architecture."))]
    assert "get_project_architecture" in p3


# ------------------------------------------------------------------- agent
def test_agent_investigation_deterministic_fallback(indexed):
    from backend.app.agents.agent import InvestigationAgent
    res = InvestigationAgent(indexed.project_id).investigate(
        "How is the table name determined in loadEligibleCustomers?").as_dict()

    assert res["classification"]["type"] == "DYNAMIC_SQL"
    assert res["iterations"] == 0                       # echo can't drive the loop
    assert [c["tool"] for c in res["plan"]][0] == "search_symbol"
    assert all(t["ok"] for t in res["tool_trace"] if t["tool"] != "search_symbol" or True)
    assert res["evidence"]
    # evidence traces back to the metadata chain
    details = " ".join(e["detail"] for e in res["evidence"])
    assert "META_TABLE_REGISTRY" in details or any(
        t["tool"] == "trace_metadata_dependency" and t["ok"] for t in res["tool_trace"])


def test_agent_impact_question(indexed):
    from backend.app.agents.agent import InvestigationAgent
    res = InvestigationAgent(indexed.project_id).investigate(
        "If I change loadEligibleCustomers what could be impacted?").as_dict()
    assert res["classification"]["type"] == "IMPACT"
    impact = next(t for t in res["tool_trace"] if t["tool"] == "impact_analysis")
    assert impact["ok"]
    assert "CustomerService.processCustomer" in impact["data"]["direct_impact"]


# --------------------------------------------------------------------- API
def test_investigate_endpoint_and_catalogue(indexed):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    c = TestClient(app)

    cat = c.get("/api/agent/tools").json()
    assert any(t["name"] == "trace_dynamic_sql" for t in cat)

    r = c.post(f"/api/projects/{indexed.project_id}/investigate",
               json={"question": "How is the table name determined in loadEligibleCustomers?"})
    assert r.status_code == 200
    body = r.json()
    assert body["plan"] and body["tool_trace"] and body["evidence"]
    assert body["classification"]["type"] == "DYNAMIC_SQL"


def test_investigate_503_when_model_down(indexed, monkeypatch):
    monkeypatch.setenv("CODEXRAY_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("CODEXRAY_LLM_HOST", "http://127.0.0.1:59998")
    from backend.app.config.settings import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from backend.app.main import app

    c = TestClient(app)
    r = c.post(f"/api/projects/{indexed.project_id}/investigate", json={"question": "Explain CustomerService."})
    assert r.status_code == 503


# ------------------------------------------------------- recursive flow trace
def test_trace_flow_tool_and_classifier(indexed):
    from backend.app.llm.question_classifier import classify
    c = classify("Trace the full flow of processCustomer end to end from the entry point to the database")
    assert c.flow and "processCustomer" in c.symbols

    from backend.app.agents.tools import ToolRegistry
    r = ToolRegistry(indexed.project_id).run("trace_flow", {"method": "processCustomer"})
    assert r.ok and r.data["found"]
    methods = {s["method"] for s in r.data["steps"]}
    # recursive descent reaches the metadata + dynamic-SQL layer
    assert {"CustomerService.processCustomer", "CustomerService.loadEligibleCustomers",
            "MetadataService.getPhysicalTable", "DynamicQueryBuilder.buildWithConcat"} <= methods
    # JDBC / stdlib calls are filtered out of the project call tree
    assert "prepareStatement" not in methods and "executeQuery" not in methods
    # metadata tables are attached to the dynamic-SQL step
    load = next(s for s in r.data["steps"] if s["method"] == "CustomerService.loadEligibleCustomers")
    assert set(load["sql"]["metadata_tables"]) == {"META_TABLE_REGISTRY", "META_COLUMN_REGISTRY"}


def test_flow_context_section(indexed):
    from backend.app.llm.context_builder import ContextBuilder
    from backend.app.llm.question_classifier import classify
    ctx = ContextBuilder(indexed.project_id).build(
        classify("trace the process flow from processCustomer to the database"))
    rendered = ctx.render()
    assert "EXECUTION FLOW" in rendered
    assert "CustomerService.loadEligibleCustomers" in rendered
    assert "META_TABLE_REGISTRY" in rendered
