#!/usr/bin/env python3
"""
scripts/generate_class_docs.py

Purpose
-------
Generate one detailed technical-design Markdown document per Java class and
feed it into the semantic index, so Chat/Ask/Agent can retrieve and cite
class-level detail (build plan §37/§38 extension — see
backend/app/analyzers/documentation/class_doc_generator.py).

This is a separate, opt-in step from normal indexing: it can be slow (one LLM
call per class by default) on a large project, so it is resumable — progress
is committed to the database after every class, and re-running the same
command skips classes that already have a doc unless --force is given.

Usage
-----
  # whole project, using the local LLM for narrative sections (default)
  python scripts/generate_class_docs.py --project myapp

  # a dry run on a small slice first
  python scripts/generate_class_docs.py --project myapp --package com.acme.billing --limit 5

  # fast heuristic-only pass (no LLM calls)
  python scripts/generate_class_docs.py --project myapp --no-llm

  # regenerate everything, even classes that already have a doc
  python scripts/generate_class_docs.py --project myapp --force
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.analyzers.documentation.class_doc_generator import ClassDocGenerator   # noqa: E402
from backend.app.models.database import get_connection                                 # noqa: E402


def _project_id(name: str) -> int:
    row = get_connection().execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
    if row is None:
        sys.exit(f"project {name!r} not indexed — run scripts/index_project.py first")
    return int(row["id"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate one technical-design document per Java class.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--package", help="scope generation to classes under this package prefix")
    ap.add_argument("--limit", type=int, help="cap how many classes this invocation generates")
    ap.add_argument("--force", action="store_true", help="regenerate even classes that already have a doc")
    ap.add_argument("--llm", dest="llm", action="store_true", default=True,
                     help="use the local LLM for narrative sections (default)")
    ap.add_argument("--no-llm", dest="llm", action="store_false", help="heuristic-only, no LLM calls")
    ap.add_argument("--no-reembed", action="store_true",
                     help="skip rebuilding the semantic index after generating (for chained batch runs)")
    ap.add_argument("--out", help="also write each doc's Markdown under this directory (package subfolders)")
    args = ap.parse_args()

    pid = _project_id(args.project)
    conn = get_connection()
    gen = ClassDocGenerator(pid, conn=conn)
    gen.precompute()

    targets = gen.target_classes(args.package)
    already = set() if args.force else {
        r["class_id"] for r in conn.execute("SELECT class_id FROM class_docs WHERE project_id=?", (pid,))
    }
    pending = [cl for cl in targets if cl["id"] not in already]
    todo = pending if args.limit is None else pending[: args.limit]

    print(f"{len(targets)} class(es) in scope, {len(already)} already done, {len(pending)} pending"
          + (f", generating {len(todo)} this run (--limit {args.limit})" if args.limit is not None else "")
          + " — use --force to regenerate existing docs")

    out_dir = Path(args.out) if args.out else None
    llm_count = heuristic_count = 0
    done = 0
    try:
        for cl in todo:
            doc = gen.build_one(cl, use_llm=args.llm)
            gen.persist(doc)
            if out_dir:
                pkg_dir = out_dir / (doc.package or "_default").replace(".", "/")
                pkg_dir.mkdir(parents=True, exist_ok=True)
                (pkg_dir / f"{doc.name}.md").write_text(doc.to_markdown(), encoding="utf-8")
            done += 1
            if doc.purpose_source == "llm":
                llm_count += 1
            else:
                heuristic_count += 1
            print(f"[{done}/{len(todo)}] {doc.package}.{doc.name} ... {doc.purpose_source}")
    except KeyboardInterrupt:
        print(f"\nstopped at {done}/{len(todo)} — already-generated classes are saved; "
              f"rerun the same command to resume.")
        return 1

    print(f"done: {done} generated ({llm_count} llm, {heuristic_count} heuristic)")

    if not args.no_reembed and done > 0:
        from backend.app.retrieval.semantic import SemanticIndex
        n = SemanticIndex(conn=conn).build(pid)
        print(f"re-embedded project — {n} chunks now in the semantic index")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
