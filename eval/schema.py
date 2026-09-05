"""
eval/schema.py

Purpose
-------
Types for the CodeXray evaluation framework (build plan §66, §67, §68).

Responsibility
--------------
- `EvalCase`: one benchmark question plus its *expected evidence* — the
  deterministic ground truth the plan asks for (§67). Every field is optional so
  a case only asserts what is relevant to its category.
- `CaseScore` / `RunReport`: per-case metric breakdown and the aggregate.
- The metric vocabulary matches §67:
    retrieval_accuracy, evidence_accuracy, sql_resolution_accuracy,
    dependency_accuracy, answer_correctness, hallucination_rate, latency_s.

No behaviour — `runner.py` populates these, `report.py` renders them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Category(str, Enum):
    ARCHITECTURE = "architecture"
    JAVA = "java"
    SQL = "sql"
    DYNAMIC_SQL = "dynamic_sql"
    DEPENDENCY = "dependency"
    IMPACT = "impact"
    DEBUGGING = "debugging"


@dataclass
class EvalCase:
    id: str
    category: Category
    question: str

    # --- expected retrieved evidence (deterministic ground truth) -----------
    expect_evidence_files: list[str] = field(default_factory=list)     # basename substrings
    expect_evidence_symbols: list[str] = field(default_factory=list)   # symbol substrings
    expect_tables: list[str] = field(default_factory=list)             # SQL / metadata tables

    # --- expected structured facts ----------------------------------------
    expect_dynamic_status: str | None = None      # RESOLVED | PARTIALLY_RESOLVED | UNRESOLVED
    expect_callers: list[str] = field(default_factory=list)
    expect_impact: list[str] = field(default_factory=list)             # direct/indirect symbols

    # --- expected answer content ----------------------------------------
    expect_answer_substrings: list[str] = field(default_factory=list)  # any-of, case-insensitive
    expect_labels: list[str] = field(default_factory=list)             # FACT | INFERENCE | UNKNOWN

    # --- anti-hallucination --------------------------------------------
    forbid_answer_substrings: list[str] = field(default_factory=list)  # must NOT appear

    @classmethod
    def from_dict(cls, d: dict) -> "EvalCase":
        return cls(
            id=d["id"],
            category=Category(d["category"]),
            question=d["question"],
            expect_evidence_files=d.get("expect_evidence_files", []),
            expect_evidence_symbols=d.get("expect_evidence_symbols", []),
            expect_tables=d.get("expect_tables", []),
            expect_dynamic_status=d.get("expect_dynamic_status"),
            expect_callers=d.get("expect_callers", []),
            expect_impact=d.get("expect_impact", []),
            expect_answer_substrings=d.get("expect_answer_substrings", []),
            expect_labels=d.get("expect_labels", []),
            forbid_answer_substrings=d.get("forbid_answer_substrings", []),
        )


@dataclass
class CaseScore:
    case_id: str
    category: str
    metrics: dict[str, float]           # metric -> 0..1  (latency_s is seconds, not scaled)
    passed: bool
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "metrics": {k: round(v, 3) for k, v in self.metrics.items()},
            "passed": self.passed,
            "notes": self.notes,
        }


@dataclass
class RunReport:
    project: str
    model: str
    mode: str                          # retrieval | ask | agent
    cases: list[CaseScore] = field(default_factory=list)

    def aggregate(self) -> dict:
        if not self.cases:
            return {}
        keys = sorted({k for c in self.cases for k in c.metrics})
        agg: dict[str, float] = {}
        for k in keys:
            vals = [c.metrics[k] for c in self.cases if k in c.metrics]
            agg[k] = round(sum(vals) / len(vals), 3) if vals else 0.0
        agg["pass_rate"] = round(sum(c.passed for c in self.cases) / len(self.cases), 3)
        agg["n"] = len(self.cases)
        return agg

    def by_category(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        cats = {c.category for c in self.cases}
        for cat in sorted(cats):
            subset = [c for c in self.cases if c.category == cat]
            keys = sorted({k for c in subset for k in c.metrics})
            row: dict[str, float] = {}
            for k in keys:
                vals = [c.metrics[k] for c in subset if k in c.metrics]   # only cases that assert it
                if vals:
                    row[k] = round(sum(vals) / len(vals), 3)
            row["pass_rate"] = round(sum(c.passed for c in subset) / len(subset), 3)
            row["n"] = len(subset)
            out[cat] = row
        return out

    def as_dict(self) -> dict:
        return {
            "project": self.project,
            "model": self.model,
            "mode": self.mode,
            "aggregate": self.aggregate(),
            "by_category": self.by_category(),
            "cases": [c.as_dict() for c in self.cases],
        }
