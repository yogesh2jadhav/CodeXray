"""
eval/report.py

Purpose
-------
Render `RunReport`s as JSON + Markdown, and compare several model runs side by
side (build plan §68).

Responsibility
--------------
- `to_markdown(report)`: aggregate table + per-category table + failing cases.
- `compare_markdown(reports)`: one row per model, columns for the headline
  metrics, so "the best model on your benchmark within your hardware limits" is
  a glance away.
- `save(report, out_dir)`: writes `<model>-<mode>.json` and `.md`.
"""
from __future__ import annotations

import json
from pathlib import Path

from eval.schema import RunReport

_HEADLINE = [
    "pass_rate", "retrieval_accuracy", "evidence_accuracy", "sql_resolution_accuracy",
    "dependency_accuracy", "answer_correctness", "label_adherence", "hallucination_rate", "latency_s",
]


def _fmt(v: float | None) -> str:
    return "—" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))


def to_markdown(report: RunReport) -> str:
    agg = report.aggregate()
    lines = [
        f"# Eval — {report.project} · model `{report.model}` · mode `{report.mode}`",
        "",
        f"**{agg.get('n', 0)} cases · pass rate {agg.get('pass_rate', 0):.0%}**",
        "",
        "| metric | value |",
        "|--------|-------|",
    ]
    for k in _HEADLINE:
        if k in agg:
            lines.append(f"| {k} | {_fmt(agg[k])} |")
    lines += ["", "## By category", "", "| category | n | pass | retrieval | sql_res | dep | answer | halluc |",
              "|----------|---|------|-----------|--------|-----|--------|--------|"]
    for cat, m in report.by_category().items():
        lines.append(
            f"| {cat} | {m['n']} | {m['pass_rate']:.0%} | {_fmt(m.get('retrieval_accuracy'))} | "
            f"{_fmt(m.get('sql_resolution_accuracy'))} | {_fmt(m.get('dependency_accuracy'))} | "
            f"{_fmt(m.get('answer_correctness'))} | {_fmt(m.get('hallucination_rate'))} |"
        )

    fails = [c for c in report.cases if not c.passed]
    if fails:
        lines += ["", "## Failing cases", ""]
        for c in fails:
            lines.append(f"- **{c.case_id}** ({c.category}) — "
                         + ", ".join(f"{k}={v:.2f}" for k, v in c.metrics.items() if k != "latency_s")
                         + (f"  · {'; '.join(c.notes)}" if c.notes else ""))
    return "\n".join(lines)


def compare_markdown(reports: list[RunReport]) -> str:
    lines = [
        "# Model comparison",
        "",
        "| model | mode | pass | retrieval | sql_res | dep | answer | label | halluc | latency |",
        "|-------|------|------|-----------|--------|-----|--------|-------|--------|---------|",
    ]
    for r in reports:
        a = r.aggregate()
        lines.append(
            f"| `{r.model}` | {r.mode} | {a.get('pass_rate', 0):.0%} | {_fmt(a.get('retrieval_accuracy'))} | "
            f"{_fmt(a.get('sql_resolution_accuracy'))} | {_fmt(a.get('dependency_accuracy'))} | "
            f"{_fmt(a.get('answer_correctness'))} | {_fmt(a.get('label_adherence'))} | "
            f"{_fmt(a.get('hallucination_rate'))} | {_fmt(a.get('latency_s'))}s |"
        )
    lines += ["", "_Best model = highest pass rate + accuracy with lowest hallucination, "
              "within your latency/memory budget (build plan §68)._"]
    return "\n".join(lines)


def save(report: RunReport, out_dir: str | Path) -> tuple[Path, Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{report.model.replace(':', '_').replace('/', '_')}-{report.mode}"
    j = d / f"{stem}.json"
    m = d / f"{stem}.md"
    j.write_text(json.dumps(report.as_dict(), indent=2))
    m.write_text(to_markdown(report))
    return j, m
