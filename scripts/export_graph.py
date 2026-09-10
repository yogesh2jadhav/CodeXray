#!/usr/bin/env python3
"""
scripts/export_graph.py

Purpose
-------
Export a project's dependency graph (modules / packages / classes / methods /
tables / SQL) for external visualisation or querying.

Usage
-----
  # Neo4j: write a .cypher file, then load it
  python scripts/export_graph.py --project synthetic --format cypher --out graph.cypher
  cypher-shell -u neo4j -p <pw> -f graph.cypher          # or paste into Neo4j Browser

  # Neo4j: load directly (needs `pip install neo4j` + a running instance)
  NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=codexray \
    python scripts/export_graph.py --project synthetic --format cypher --load

  # Gephi / yEd / Cytoscape desktop
  python scripts/export_graph.py --project synthetic --format graphml --out graph.graphml

  # Graphviz
  python scripts/export_graph.py --project synthetic --format dot --out graph.dot
  dot -Tsvg graph.dot -o graph.svg
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.graph.export import GraphExporter          # noqa: E402
from backend.app.models.database import get_connection      # noqa: E402


def _project_id(name: str) -> int:
    row = get_connection().execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
    if row is None:
        sys.exit(f"project {name!r} not indexed — run scripts/index_project.py first")
    return int(row["id"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Export the CodeXray dependency graph.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--format", choices=["cypher", "graphml", "dot", "json"], default="cypher")
    ap.add_argument("--out", help="output file (default: stdout, or graph.<ext>)")
    ap.add_argument("--load", action="store_true", help="push straight into Neo4j via bolt (needs `pip install neo4j`)")
    ap.add_argument("--uri", default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
                    help="Neo4j connection URI (e.g. neo4j://127.0.0.1:7687)")
    ap.add_argument("--user", default=os.environ.get("NEO4J_USER", "neo4j"))
    ap.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD", "neo4j"))
    ap.add_argument("--include-external", action="store_true",
                    help="keep JDBC / framework classes and calls in the graph")
    args = ap.parse_args()

    exp = GraphExporter(_project_id(args.project), include_external=args.include_external)

    if args.load:
        res = exp.push_to_neo4j(args.uri, args.user, args.password)
        print(json.dumps(res, indent=2))
        return 0

    if args.format == "cypher":
        text = exp.to_cypher()
    elif args.format == "graphml":
        text = exp.to_graphml()
    elif args.format == "dot":
        text = exp.to_dot()
    else:
        text = json.dumps(exp.to_cytoscape_json(), indent=2)

    ext = {"cypher": "cypher", "graphml": "graphml", "dot": "dot", "json": "json"}[args.format]
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}  ({len(exp.nodes)} nodes, {len(exp.edges)} edges)")
    elif sys.stdout.isatty():
        Path(f"graph.{ext}").write_text(text)
        print(f"wrote graph.{ext}  ({len(exp.nodes)} nodes, {len(exp.edges)} edges)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
