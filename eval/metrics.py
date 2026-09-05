"""
eval/metrics.py

Purpose
-------
Score one evaluation case against a normalised run output (build plan §67).

Responsibility
--------------
- `RunOutput`: what the runner produced for a case (answer + parsed labels +
  evidence + deterministically probed facts: tables, dynamic-SQL status,
  callers, impact, latency).
- `score_case(case, out, threshold)` -> `CaseScore` with the §67 metrics:
    retrieval_accuracy   — expected symbols found in retrieved evidence
    evidence_accuracy    — expected files found in retrieved evidence
    sql_resolution_accuracy — dynamic-SQL status matches ground truth
    dependency_accuracy  — expected callers / impact found
    answer_correctness   — expected answer substrings present
    hallucination_rate   — forbidden / invented content present (lower is better)
    latency_s            — wall-clock seconds (not scaled)
- A metric is *omitted* for a case that does not assert it, so it never dilutes
  the aggregate.

Pure functions; no I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from eval.schema import CaseScore, EvalCase

# Physical table names that must never be presented as the resolved target of a
# metadata-driven query in the synthetic project (build plan §29 examples).
_INVENTED_TABLE_RE = re.compile(
    r"\b(CUSTOMER_HISTORY|CUSTOMER_ARCHIVE|CUSTOMER_\w*_(HIST|BAK|OLD))\b", re.I
)
_HEDGE_AS_FACT_RE = re.compile(r"\b(the (physical )?table is (probably|likely) [A-Z_]+)\b", re.I)


@dataclass
class RunOutput:
    answer: str = ""
    labels: dict[str, list[str]] = field(default_factory=dict)       # FACT/INFERENCE/UNKNOWN -> lines
    evidence_files: list[str] = field(default_factory=list)          # basenames
    evidence_symbols: list[str] = field(default_factory=list)        # detail strings
    tables_seen: list[str] = field(default_factory=list)             # upper-case
    dynamic_status: str | None = None
    callers: list[str] = field(default_factory=list)
    impact: list[str] = field(default_factory=list)
    latency_s: float = 0.0
    used_llm: bool = False


def _coverage(expected: list[str], haystack: str) -> float:
    if not expected:
        return -1.0  # not applicable
    hay = haystack.lower()
    hit = sum(1 for e in expected if e.lower() in hay)
    return hit / len(expected)


def _any_present(needles: list[str], text: str) -> bool:
    t = text.lower()
    return any(n.lower() in t for n in needles)


def score_case(case: EvalCase, out: RunOutput, threshold: float = 0.5) -> CaseScore:
    metrics: dict[str, float] = {}
    notes: list[str] = []

    ev_blob = " ".join(out.evidence_symbols) + " " + " ".join(out.evidence_files)

    r = _coverage(case.expect_evidence_symbols, ev_blob)
    if r >= 0:
        metrics["retrieval_accuracy"] = r

    e = _coverage(case.expect_evidence_files, " ".join(out.evidence_files))
    if e >= 0:
        metrics["evidence_accuracy"] = e

    if case.expect_dynamic_status:
        ok = (out.dynamic_status or "").upper() == case.expect_dynamic_status.upper()
        metrics["sql_resolution_accuracy"] = 1.0 if ok else 0.0
        if not ok:
            notes.append(f"dynamic status {out.dynamic_status!r} != {case.expect_dynamic_status!r}")

    dep_expect = case.expect_callers + case.expect_impact
    if dep_expect:
        pool = " ".join(out.callers + out.impact).lower()
        metrics["dependency_accuracy"] = sum(1 for d in dep_expect if d.lower() in pool) / len(dep_expect)

    if case.expect_tables:
        seen = {t.upper() for t in out.tables_seen}
        got = sum(1 for t in case.expect_tables if t.upper() in seen)
        metrics.setdefault("retrieval_accuracy", got / len(case.expect_tables))
        # blend if both symbol + table expectations exist
        if "retrieval_accuracy" in metrics and case.expect_evidence_symbols:
            metrics["retrieval_accuracy"] = (metrics["retrieval_accuracy"] + got / len(case.expect_tables)) / 2

    if out.used_llm and case.expect_answer_substrings:
        metrics["answer_correctness"] = _coverage(case.expect_answer_substrings, out.answer)

    if out.used_llm and case.expect_labels:
        present = {lbl for lbl in case.expect_labels if out.labels.get(lbl.upper())}
        metrics["label_adherence"] = len(present) / len(case.expect_labels)
        if len(present) < len(case.expect_labels):
            notes.append(f"missing labels {set(case.expect_labels) - present}")

    # hallucination: forbidden strings, invented tables, hedges-as-fact
    hall = 0.0
    if out.used_llm:
        if case.forbid_answer_substrings and _any_present(case.forbid_answer_substrings, out.answer):
            hall = 1.0
            notes.append("forbidden phrase present")
        if _INVENTED_TABLE_RE.search(out.answer) or _HEDGE_AS_FACT_RE.search(out.answer):
            hall = 1.0
            notes.append("invented/hedged table name")
        metrics["hallucination_rate"] = hall

    metrics["latency_s"] = round(out.latency_s, 2)

    # pass = every applicable accuracy metric >= threshold, and no hallucination.
    # label_adherence is reported as a soft quality signal but does not gate pass.
    acc_keys = [k for k in metrics if k.endswith("_accuracy") or k == "answer_correctness"]
    passed = all(metrics[k] >= threshold for k in acc_keys) and hall == 0.0
    return CaseScore(case_id=case.id, category=case.category.value, metrics=metrics, passed=passed, notes=notes)
