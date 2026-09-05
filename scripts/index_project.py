#!/usr/bin/env python3
"""
scripts/index_project.py

Purpose
-------
Command-line entry point to index a project without starting the API server.

Responsibility
--------------
- Resolve `--root` (defaults to `project.root` from config) and `--name`.
- Run `ProjectIndexer.index_project` and pretty-print the `IndexReport`
  (indexed / skipped / failed counts, first failures).
- `--force` re-parses every file, ignoring content hashes.

Usage:
    python scripts/index_project.py --root projects/synthetic-test-project --name synthetic
    python scripts/index_project.py --name synthetic --force
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make `backend` importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config.settings import get_settings          # noqa: E402
from backend.app.indexing.indexer import ProjectIndexer       # noqa: E402


def main() -> int:
    settings = get_settings()
    ap = argparse.ArgumentParser(description="Index a Java+SQL project into the CodeXray index.")
    ap.add_argument("--root", default=str(settings.project_root), help="Path to the project source tree")
    ap.add_argument("--name", required=True, help="Logical project name (unique key in the index)")
    ap.add_argument("--force", action="store_true", help="Re-parse every file, ignoring content hashes")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        ap.error(f"--root is not a directory: {root}")

    print(f"Indexing '{args.name}' from {root} ...")
    report = ProjectIndexer().index_project(args.name, root, force=args.force)
    print(json.dumps(report.as_dict(), indent=2))
    print(
        f"\nDone. indexed={report.files_indexed} "
        f"skipped={report.files_skipped} failed={report.files_failed} "
        f"total={report.files_total}"
    )
    return 0 if report.files_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
