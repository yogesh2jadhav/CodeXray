"""
tests/test_sprint1.py

Purpose
-------
Regression tests for the Sprint 1 vertical slice (scanner -> parsers -> index -> search).

Responsibility
--------------
- Prove the acceptance criteria of build-plan §55/§56/§66 against the bundled
  synthetic project:
    * Java files, classes, methods, imports and calls are extracted.
    * SQL is parsed from `.sql` files, inline literals and constants.
    * SQL tables/columns/joins/parameters are identified.
    * Config secrets are redacted.
    * Symbol / SQL / table-usage search return the expected rows.
- Each test uses an isolated temp DB via the CODEXRAY_INDEX_DB env override.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SYNTH = REPO / "projects" / "synthetic-test-project"


@pytest.fixture()
def indexed(tmp_path, monkeypatch):
    """Index the synthetic project into a throwaway SQLite DB and return helpers."""
    db = tmp_path / "idx.db"
    monkeypatch.setenv("CODEXRAY_INDEX_DB", str(db))

    # settings is lru_cached — clear it so the env override takes effect.
    from backend.app.config.settings import get_settings
    get_settings.cache_clear()

    from backend.app.indexing.indexer import ProjectIndexer
    from backend.app.models.database import get_connection

    report = ProjectIndexer().index_project("synthetic", SYNTH, force=True)
    conn = get_connection()
    return report, conn


def test_scanner_and_java_extraction(indexed):
    report, conn = indexed
    assert report.files_failed == 0
    assert report.files_indexed >= 6

    classes = {r["name"] for r in conn.execute("SELECT name FROM classes")}
    assert {"CustomerService", "CustomerRepository", "MetadataService",
            "DynamicQueryBuilder", "QueryConstants"} <= classes

    methods = {r["name"] for r in conn.execute("SELECT name FROM methods")}
    assert {"processCustomer", "loadEligibleCustomers", "getPhysicalTable",
            "getColumnList", "buildQuery"} <= methods

    imports = {r["imported"] for r in conn.execute("SELECT imported FROM imports")}
    assert any(i.startswith("java.sql") for i in imports)


def test_calls_extracted(indexed):
    _, conn = indexed
    callees = {r["callee_name"] for r in conn.execute("SELECT callee_name FROM calls")}
    # CustomerService.loadEligibleCustomers calls these:
    assert {"getPhysicalTable", "getColumnList", "buildQuery", "runResolvedQuery"} <= callees


def test_sql_from_constant_and_literal(indexed):
    _, conn = indexed
    origins = {r["origin"] for r in conn.execute("SELECT origin FROM sql_queries")}
    assert {"java_constant", "java_literal", "sql_file"} <= origins

    # GET_CUSTOMER constant -> CUSTOMER table, bind parameter '?'
    row = conn.execute(
        "SELECT id FROM sql_queries WHERE raw_sql LIKE '%FROM CUSTOMER WHERE CUST_ID%'"
    ).fetchone()
    assert row is not None
    tables = {r["name"].upper() for r in conn.execute(
        "SELECT name FROM sql_tables WHERE query_id=?", (row["id"],))}
    assert "CUSTOMER" in tables
    params = {r["marker"] for r in conn.execute(
        "SELECT marker FROM sql_parameters WHERE query_id=?", (row["id"],))}
    assert "?" in params


def test_sql_join_and_metadata_tables(indexed):
    _, conn = indexed
    join_targets = {(r["target"] or "").upper() for r in conn.execute("SELECT target FROM sql_joins")}
    assert "CUSTOMER_ADDRESS" in join_targets

    all_tables = {r["name"].upper() for r in conn.execute("SELECT name FROM sql_tables")}
    assert {"META_TABLE_REGISTRY", "META_COLUMN_REGISTRY"} <= all_tables


def test_config_secret_redaction(indexed):
    _, conn = indexed
    rows = {r["key"]: (r["value"], r["is_secret"])
            for r in conn.execute("SELECT key, value, is_secret FROM config_entries")}
    assert rows["db.password"][1] == 1
    assert rows["db.password"][0] == "***REDACTED***"
    assert rows["db.connectionString"][1] == 1
    assert rows["app.name"][1] == 0
    assert rows["app.name"][0] == "synthetic-customer-service"


def test_symbol_and_table_search(indexed):
    report, conn = indexed
    from backend.app.retrieval import search as S
    pid = report.project_id

    hits = S.search_symbols(pid, "loadEligibleCustomers")
    assert hits and hits[0]["qualified"] == "CustomerService.loadEligibleCustomers"

    usage = S.find_table_usage(pid, "CUSTOMER")
    assert len(usage) >= 2
    assert all(u["table_name"].upper() == "CUSTOMER" for u in usage)

    callers = S.find_callers(pid, "getPhysicalTable")
    assert any(c["caller_method"] == "loadEligibleCustomers" for c in callers)


def test_test_code_is_excluded(tmp_path, monkeypatch):
    """Test sources (src/test, *Test.java, *IT.java) must never enter the index."""
    monkeypatch.setenv("CODEXRAY_INDEX_DB", str(tmp_path / "idx.db"))
    from backend.app.config.settings import get_settings
    get_settings.cache_clear()

    proj = tmp_path / "proj"
    (proj / "src/main/java/com/acme").mkdir(parents=True)
    (proj / "src/test/java/com/acme").mkdir(parents=True)
    (proj / "src/main/java/com/acme/Widget.java").write_text(
        "package com.acme; public class Widget { public int calc(){ return 1; } }")
    (proj / "src/main/java/com/acme/WidgetTest.java").write_text(
        "package com.acme; public class WidgetTest { public void t(){} }")
    (proj / "src/test/java/com/acme/WidgetIT.java").write_text(
        "package com.acme; public class WidgetIT { public void it(){} }")

    from backend.app.indexing.indexer import ProjectIndexer
    from backend.app.models.database import get_connection
    ProjectIndexer().index_project("t", proj, force=True)
    conn = get_connection()

    files = {r["path"] for r in conn.execute("SELECT path FROM files")}
    classes = {r["name"] for r in conn.execute("SELECT name FROM classes")}
    assert any("Widget.java" in f for f in files)
    assert not any("Test" in f or "IT" in f for f in files)
    assert classes == {"Widget"}
