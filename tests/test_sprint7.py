"""
tests/test_sprint7.py

Purpose
-------
Regression tests for the Sprint 7 evaluation framework (build plan §66-68).

Responsibility
--------------
Deterministically (retrieval mode — no LLM; and the echo provider for the ask
path):
  * the dataset loads and covers every category;
  * `EvalRunner` in "retrieval" mode scores the deterministic metrics and the
    synthetic project passes at ~100% (retrieval / evidence / sql-resolution /
    dependency);
  * dynamic-SQL cases get sql_resolution_accuracy == 1.0 with status
    PARTIALLY_RESOLVED (never a hallucinated table);
  * the anti-hallucination metric fires on a forbidden phrase;
  * report rendering + save produce Markdown + JSON.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SYNTH = REPO / "projects" / "synthetic-test-project"
DATASET = REPO / "eval" / "dataset" / "synthetic.yaml"


@pytest.fixture()
def indexed(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEXRAY_INDEX_DB", str(tmp_path / "idx.db"))
    monkeypatch.setenv("CODEXRAY_EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("CODEXRAY_LLM_PROVIDER", "echo")
    from backend.app.config.settings import get_settings
    get_settings.cache_clear()
    from backend.app.indexing.indexer import ProjectIndexer
    return ProjectIndexer().index_project("synthetic", SYNTH, force=True)


def test_dataset_covers_all_categories():
    from eval.runner import load_cases
    from eval.schema import Category
    cases = load_cases(DATASET)
    assert len(cases) >= 40
    seen = {c.category for c in cases}
    assert seen == set(Category)


def test_retrieval_mode_scores_high(indexed):
    from eval.runner import EvalRunner, load_cases
    cases = load_cases(DATASET)
    report = EvalRunner(indexed.project_id, mode="retrieval").run(cases, model="none")
    agg = report.aggregate()

    assert agg["n"] == len(cases)
    assert agg["pass_rate"] >= 0.9
    assert agg["retrieval_accuracy"] >= 0.9
    assert agg["sql_resolution_accuracy"] == 1.0
    assert agg["dependency_accuracy"] >= 0.9

    dsql = report.by_category()["dynamic_sql"]
    assert dsql["sql_resolution_accuracy"] == 1.0


def test_dynamic_sql_status_is_partially_resolved(indexed):
    from eval.runner import EvalRunner, load_cases
    from eval.schema import Category
    cases = [c for c in load_cases(DATASET) if c.category is Category.DYNAMIC_SQL and c.expect_dynamic_status]
    runner = EvalRunner(indexed.project_id, mode="retrieval")
    for case in cases:
        out = runner.run_case(case)
        assert out.dynamic_status == "PARTIALLY_RESOLVED", case.id


def test_hallucination_metric_fires():
    from eval.metrics import RunOutput, score_case
    from eval.schema import Category, EvalCase

    case = EvalCase(
        id="x", category=Category.DYNAMIC_SQL, question="q",
        forbid_answer_substrings=["CUSTOMER_HISTORY"], expect_answer_substrings=["metadata"],
    )
    bad = RunOutput(answer="The table is probably CUSTOMER_HISTORY.", used_llm=True)
    good = RunOutput(answer="It comes from a metadata lookup; runtime value unknown.", used_llm=True)
    assert score_case(case, bad).metrics["hallucination_rate"] == 1.0
    assert score_case(case, good).metrics["hallucination_rate"] == 0.0
    assert score_case(case, bad).passed is False


def test_report_render_and_save(indexed, tmp_path):
    from eval.report import save, to_markdown
    from eval.runner import EvalRunner, load_cases

    cases = load_cases(DATASET)[:8]
    report = EvalRunner(indexed.project_id, mode="retrieval").run(cases, model="none")
    md = to_markdown(report)
    assert "# Eval" in md and "By category" in md
    j, m = save(report, tmp_path / "out")
    assert j.exists() and m.exists()
