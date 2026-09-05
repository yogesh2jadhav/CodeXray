"""
tests/test_sprint3.py

Purpose
-------
Regression tests for the Sprint 3 local-LLM pipeline (build plan §25, §30-33, §59, §72).

Responsibility
--------------
Deterministically (via the offline `echo` provider — no model server needed):
  * question classification picks the right type + retrieval modes;
  * the ContextBuilder assembles evidence-first sections within the char budget;
  * dynamic-SQL evidence (metadata chain, PARTIALLY_RESOLVED) reaches the prompt;
  * the prompt carries the FACT/INFERENCE/UNKNOWN + "never invent a table" rules;
  * the response parser splits labels and derives confidence;
  * `/ask` returns 200 with the echo provider and 503 with a broken Ollama host;
  * `/llm/health` reports provider availability.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SYNTH = REPO / "projects" / "synthetic-test-project"


@pytest.fixture()
def indexed(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEXRAY_INDEX_DB", str(tmp_path / "idx.db"))
    monkeypatch.setenv("CODEXRAY_LLM_PROVIDER", "echo")
    from backend.app.config.settings import get_settings
    get_settings.cache_clear()
    from backend.app.indexing.indexer import ProjectIndexer
    report = ProjectIndexer().index_project("synthetic", SYNTH, force=True)
    return report


# ------------------------------------------------------------- classifier
def test_classifier_types_and_modes():
    from backend.app.llm.question_classifier import QuestionType, RetrievalMode, classify

    c = classify("How is the table name for this query determined?")
    assert c.qtype is QuestionType.DYNAMIC_SQL
    assert RetrievalMode.DATA_FLOW in c.modes

    c2 = classify("Who calls CustomerRepository.findCustomer?")
    assert c2.qtype is QuestionType.DEPENDENCY
    assert "CustomerRepository.findCustomer" in c2.symbols

    c3 = classify("Explain this project architecture.")
    assert c3.qtype is QuestionType.ARCHITECTURE

    c4 = classify("What could be impacted if I change loadEligibleCustomers()?")
    assert c4.qtype is QuestionType.IMPACT
    assert "loadEligibleCustomers" in c4.symbols
    # "How"/"What" must not be treated as code symbols
    assert "What" not in c4.symbols and "How" not in classify("How does X work").symbols


# ------------------------------------------------------------- context builder
def test_context_builder_assembles_dynamic_sql_evidence(indexed):
    from backend.app.llm.context_builder import ContextBuilder
    from backend.app.llm.question_classifier import classify

    cls = classify("How is the table name determined in loadEligibleCustomers?")
    ctx = ContextBuilder(indexed.project_id).build(cls)

    rendered = ctx.render()
    assert "DYNAMIC SQL RESOLUTION" in rendered
    assert "META_TABLE_REGISTRY" in rendered
    assert "PARTIALLY_RESOLVED" in rendered
    assert len(rendered) <= ctx.char_budget + 500          # budget respected (+ truncation note)
    assert any(e["kind"] == "dynamic_sql" for e in [ev.as_dict() for ev in ctx.evidence])


def test_context_budget_drops_low_priority_sections():
    from backend.app.llm.context_builder import ContextBudgetManager, Section

    secs = [
        Section("SOURCE", 1, ["x" * 100]),
        Section("DOCUMENTATION", 4, ["y" * 100]),
        Section("ARCH", 6, []),
    ]
    kept, dropped = ContextBudgetManager(150).select(secs)
    assert [s.title for s in kept] == ["SOURCE"]
    assert "ARCH" in dropped


# ------------------------------------------------------------- prompt + parser
def test_prompt_has_evidence_rules(indexed):
    from backend.app.llm import prompt_builder
    from backend.app.llm.context_builder import ContextBuilder
    from backend.app.llm.question_classifier import classify

    cls = classify("Explain CustomerService.")
    ctx = ContextBuilder(indexed.project_id).build(cls)
    system, user = prompt_builder.build("Explain CustomerService.", ctx)
    assert "FACT" in system and "INFERENCE" in system and "UNKNOWN" in system
    assert "Never invent a concrete table" in system
    assert "QUESTION: Explain CustomerService." in user
    assert "PROJECT EVIDENCE" in user


def test_response_parser_labels_and_confidence():
    from backend.app.llm.response_parser import parse

    p = parse(
        "FACT: Method A calls Method B (Foo.java:10).\n"
        "INFERENCE: This looks like a batch job.\n"
        "UNKNOWN: The runtime table is not known.\n"
        "Evidence:\n- Foo.java:10\n- Bar.sql:3\n"
    )
    assert p.facts and p.inferences and p.unknowns
    assert {"file": "Foo.java", "line": 10} in p.cited_refs
    assert p.confidence == "MEDIUM"
    assert parse("FACT: x (A.java:1).").confidence == "HIGH"


# ------------------------------------------------------------- API
def test_ask_endpoint_echo_and_health(indexed):
    from fastapi.testclient import TestClient
    from backend.app.main import app

    c = TestClient(app)
    h = c.get("/api/llm/health").json()
    assert h["provider"] == "echo" and h["available"] is True

    r = c.post(f"/api/projects/{indexed.project_id}/ask",
               json={"question": "How is the table name determined in loadEligibleCustomers?"})
    assert r.status_code == 200
    body = r.json()
    assert body["classification"]["type"] == "DYNAMIC_SQL"
    assert body["evidence"]
    assert "META_TABLE_REGISTRY" in body["answer"]


def test_ask_endpoint_503_when_ollama_down(indexed, monkeypatch):
    monkeypatch.setenv("CODEXRAY_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("CODEXRAY_LLM_HOST", "http://127.0.0.1:59999")  # nothing listening
    from backend.app.config.settings import get_settings
    get_settings.cache_clear()

    from fastapi.testclient import TestClient
    from backend.app.main import app

    c = TestClient(app)
    r = c.post(f"/api/projects/{indexed.project_id}/ask", json={"question": "Explain CustomerService."})
    assert r.status_code == 503
    assert "unavailable" in r.json()["detail"]["error"]
