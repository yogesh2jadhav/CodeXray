#!/usr/bin/env python3
"""
scripts/generate_docs.py

Purpose
-------
Generate the project summary document (build plan §37 "Project Summary Memory")
from the command line.

Usage
-----
  python scripts/generate_docs.py --project synthetic --out project_summary.md
  python scripts/generate_docs.py --project synthetic --llm            # ask the
                                                                        # local model
                                                                        # for the
                                                                        # purpose
                                                                        # paragraph
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.analyzers.documentation.generator import ProjectDocGenerator  # noqa: E402
from backend.app.models.database import get_connection                        # noqa: E402


def _project_id(name: str) -> int:
    row = get_connection().execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
    if row is None:
        sys.exit(f"project {name!r} not indexed — run scripts/index_project.py first")
    return int(row["id"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a project summary document.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", help="output file (default: <project>-summary.md)")
    ap.add_argument("--llm", action="store_true", help="ask the local model for the purpose paragraph")
    args = ap.parse_args()

    doc = ProjectDocGenerator(_project_id(args.project)).generate(use_llm=args.llm)
    out = Path(args.out or f"{doc.project}-summary.md")
    out.write_text(doc.to_markdown())
    print(f"wrote {out}  (purpose: {doc.purpose_source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
