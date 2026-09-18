#!/usr/bin/env python3
"""
scripts/generate_docs.py

Purpose
-------
Generate project (or per-module) summary documents (build plan §37/§38/§61).

Usage
-----
  # whole project — fine up to a few hundred classes; beyond that it warns
  # and tells you to use --all-modules instead
  python scripts/generate_docs.py --project myapp --out project_summary.md

  # + a local-LLM purpose paragraph
  python scripts/generate_docs.py --project myapp --llm

  # one module only (a Java package prefix)
  python scripts/generate_docs.py --project myapp --package com.acme.billing

  # see what modules exist before picking one
  python scripts/generate_docs.py --project myapp --list-modules

  # a large codebase: one doc per top-level module, written into a directory
  python scripts/generate_docs.py --project myapp --all-modules --out docs/modules
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.analyzers.documentation.generator import (   # noqa: E402
    LARGE_PROJECT_CLASS_THRESHOLD,
    ProjectDocGenerator,
)
from backend.app.models.database import get_connection         # noqa: E402


def _project_id(name: str) -> int:
    row = get_connection().execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
    if row is None:
        sys.exit(f"project {name!r} not indexed — run scripts/index_project.py first")
    return int(row["id"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a project or per-module summary document.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", help="output file (single doc) or directory (--all-modules)")
    ap.add_argument("--llm", action="store_true", help="ask the local model for the purpose paragraph")
    ap.add_argument("--package", help="scope the document to one module (a Java package prefix)")
    ap.add_argument("--list-modules", action="store_true", help="list modules and exit")
    ap.add_argument("--all-modules", action="store_true", help="write one doc per top-level module")
    args = ap.parse_args()

    gen = ProjectDocGenerator(_project_id(args.project))

    if args.list_modules:
        for m in gen.list_modules():
            print(f"{m['class_count']:>6}  {m['module']}")
        return 0

    if args.all_modules:
        out_dir = args.out or f"docs/{args.project}-modules"
        paths = gen.generate_all_modules(out_dir, use_llm=args.llm)
        print(f"wrote {len(paths)} module docs to {out_dir}/")
        return 0

    doc = gen.generate(use_llm=args.llm, package_prefix=args.package)
    if doc.is_large:
        print(f"warning: {doc.counts['classes']} classes (> {LARGE_PROJECT_CLASS_THRESHOLD}) — "
              f"this single document is necessarily incomplete. Consider --all-modules or "
              f"--package <prefix>. Continuing anyway.", file=sys.stderr)

    stem = f"{doc.project}-{args.package}" if args.package else f"{doc.project}-summary"
    out = Path(args.out or f"{stem}.md")
    out.write_text(doc.to_markdown())
    print(f"wrote {out}  (purpose: {doc.purpose_source}"
          f"{', scope=' + args.package if args.package else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
