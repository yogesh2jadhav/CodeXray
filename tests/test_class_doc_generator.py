"""
tests/test_class_doc_generator.py

Purpose
-------
Regression tests for the per-class technical design document generator
(backend/app/analyzers/documentation/class_doc_generator.py) against the
bundled synthetic project (offline echo LLM, hashing embedder).
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


def test_schema_has_class_docs_table(indexed):
    from backend.app.models.database import get_connection
    conn = get_connection()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(class_docs)")}
    assert {"project_id", "class_id", "package", "markdown", "purpose_source", "generated_at"} <= cols


def test_heuristic_generation_all_sections(indexed):
    from backend.app.analyzers.documentation.class_doc_generator import ClassDocGenerator
    gen = ClassDocGenerator(indexed.project_id)
    gen.precompute()

    repo_cls = next(cl for cl in gen.target_classes(None) if cl["name"] == "CustomerRepository")
    doc = gen.build_one(repo_cls, use_llm=False)

    assert doc.purpose_source == "heuristic"
    assert doc.purpose and doc.overview and doc.responsibilities

    md = doc.to_markdown()
    for header in ("# CustomerRepository", "## Purpose", "## Overview", "## Architecture role",
                   "## Key responsibilities", "## Class variables / static variables",
                   "## Key data structures", "## Constructors", "## Methods",
                   "## Method call hierarchy", "## SQL queries", "## Data flow (tables)",
                   "## Imports", "## Package structure / sibling classes",
                   "## High-level workflow", "## Not yet extracted"):
        assert header in md, header

    assert doc.arch_role == "REPOSITORY"


def test_constructors_split_from_methods(indexed):
    from backend.app.analyzers.documentation.class_doc_generator import ClassDocGenerator
    gen = ClassDocGenerator(indexed.project_id)
    gen.precompute()

    cls = next(cl for cl in gen.target_classes(None) if cl["name"] == "CustomerRepository")
    doc = gen.build_one(cls, use_llm=False)

    ctor_names = {c["name"] for c in doc.constructors}
    method_names = {m["name"] for m in doc.methods}
    assert "CustomerRepository" in ctor_names
    assert "CustomerRepository" not in method_names
    assert method_names   # regular methods still present


def test_spark_section_present_only_for_spark_classes(indexed):
    from backend.app.analyzers.documentation.class_doc_generator import ClassDocGenerator
    gen = ClassDocGenerator(indexed.project_id)
    gen.precompute()

    # the synthetic project is plain Java — no class should get a Spark section
    for cl in gen.target_classes(None):
        doc = gen.build_one(cl, use_llm=False)
        assert doc.spark_section is None
        assert "## Spark usage" not in doc.to_markdown()


def test_spark_detection_heuristic_directly():
    from backend.app.analyzers.documentation.class_doc_generator import (
        _SPARK_TRANSFORMATIONS, ClassDocGenerator,
    )
    assert "map" in _SPARK_TRANSFORMATIONS

    gen = ClassDocGenerator.__new__(ClassDocGenerator)   # bypass __init__, pure unit test
    gen.calls_by_caller_method_id = {1: [{"callee_name": "map", "callee_qualifier": "ds"}]}
    section = gen._spark_section([{"id": 1}], [])
    assert section is not None
    assert "map" in section["transformations"]


def test_resumability_skip_then_force(indexed):
    from backend.app.analyzers.documentation.class_doc_generator import ClassDocGenerator
    from backend.app.models.database import get_connection
    conn = get_connection()
    gen = ClassDocGenerator(indexed.project_id, conn=conn)

    docs = list(gen.generate_all(use_llm=False))
    assert docs
    for d in docs:
        gen.persist(d)

    row = conn.execute(
        "SELECT generated_at FROM class_docs WHERE project_id=? AND class_id=?",
        (indexed.project_id, docs[0].class_id),
    ).fetchone()
    first_ts = row["generated_at"]

    # second run with default flags: nothing left to generate
    second = list(gen.generate_all(use_llm=False))
    assert second == []

    unchanged = conn.execute(
        "SELECT generated_at FROM class_docs WHERE project_id=? AND class_id=?",
        (indexed.project_id, docs[0].class_id),
    ).fetchone()
    assert unchanged["generated_at"] == first_ts

    # --force regenerates
    forced = list(gen.generate_all(use_llm=False, force=True))
    assert len(forced) == len(docs)
    for d in forced:
        gen.persist(d)


def test_chunker_picks_up_class_docs(indexed):
    from backend.app.analyzers.documentation.class_doc_generator import ClassDocGenerator
    from backend.app.models.database import get_connection
    from backend.app.retrieval.chunker import Chunker

    conn = get_connection()
    gen = ClassDocGenerator(indexed.project_id, conn=conn)
    doc = next(gen.generate_all(use_llm=False, limit=1))
    gen.persist(doc)

    chunks = Chunker(conn)._class_docs(indexed.project_id)
    assert chunks
    assert all(c.chunk_type == "class_doc" for c in chunks)
    assert any(doc.name in c.symbol for c in chunks)
