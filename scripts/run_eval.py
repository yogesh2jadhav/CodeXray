#!/usr/bin/env python3
"""
scripts/run_eval.py

Purpose
-------
CLI for the CodeXray evaluation framework (build plan §67, §68).

Usage
-----
  # deterministic, model-independent (fast — good for CI)
  python scripts/run_eval.py --project synthetic --mode retrieval

  # score a model on the LLM pipeline
  python scripts/run_eval.py --project synthetic --mode ask   --model qwen2.5-coder:7b
  python scripts/run_eval.py --project synthetic --mode agent --model qwen2.5-coder:7b

  # compare models (sets CODEXRAY_LLM_MODEL per run)
  python scripts/run_eval.py --project synthetic --mode ask --compare qwen2.5-coder:7b,qwen3:8b

Writes JSON + Markdown to data/eval/ and prints the summary.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.report import compare_markdown, save, to_markdown          # noqa: E402
from eval.runner import EvalRunner, load_cases                       # noqa: E402
from backend.app.models.database import get_connection               # noqa: E402

DATASET = Path(__file__).resolve().parents[1] / "eval" / "dataset" / "synthetic.yaml"
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "eval"


def _project_id(name: str) -> int:
    row = get_connection().execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
    if row is None:
        sys.exit(f"project {name!r} not indexed — run scripts/index_project.py first")
    return int(row["id"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the CodeXray benchmark.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--mode", choices=["retrieval", "ask", "agent"], default="retrieval")
    ap.add_argument("--model", default=os.environ.get("CODEXRAY_LLM_MODEL", "n/a"))
    ap.add_argument("--compare", help="comma-separated model list; overrides --model")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--category", help="only run one category")
    args = ap.parse_args()

    pid = _project_id(args.project)
    cases = load_cases(args.dataset)
    if args.category:
        cases = [c for c in cases if c.category.value == args.category]
    print(f"{len(cases)} cases · mode={args.mode}")

    models = args.compare.split(",") if args.compare else [args.model]
    reports = []
    for model in models:
        if args.mode != "retrieval":
            os.environ["CODEXRAY_LLM_MODEL"] = model
            from backend.app.config.settings import get_settings
            get_settings.cache_clear()
        rep = EvalRunner(pid, mode=args.mode, threshold=args.threshold).run(cases, model=model)
        j, m = save(rep, OUT_DIR)
        reports.append(rep)
        print(f"\n{to_markdown(rep)}\n\nwritten: {j.name}, {m.name}")

    if len(reports) > 1:
        cmp = compare_markdown(reports)
        (OUT_DIR / "comparison.md").write_text(cmp)
        print(f"\n{cmp}")

    worst_halluc = max((r.aggregate().get("hallucination_rate", 0) for r in reports), default=0)
    return 1 if worst_halluc > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
