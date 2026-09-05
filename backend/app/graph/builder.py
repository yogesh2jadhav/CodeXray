"""
backend/app/graph/builder.py

Purpose
-------
Materialise the project dependency graph from the SQLite index (build plan §57).

Responsibility
--------------
- Read classes / methods / imports / inheritance / calls / SQL / dynamic SQL from
  the index and emit typed edges into the `dependencies` table
  (`src_kind, src_label, dst_kind, dst_label, edge_type`).
- Resolve call targets to concrete method nodes when the callee's simple name is
  unique in the project; otherwise keep a name-only `method:<name>` node so
  callers are still discoverable.
- Rebuilt wholesale per project on each index run (the graph is a derived view).

No querying here — that is `graph/service.py`. Deterministic; every edge traces
to a row in the index.
"""
from __future__ import annotations

import json
import logging

from backend.app.graph.schema import EdgeType, NodeKind
from backend.app.models.database import get_connection, transaction

log = logging.getLogger("codexray.graph")


class GraphBuilder:
    def __init__(self, conn=None) -> None:
        self.conn = conn or get_connection()

    def build(self, project_id: int) -> int:
        """(Re)build the graph for a project. Returns the edge count."""
        c = self.conn
        edges: list[tuple] = []

        def add(sk: NodeKind, sl: str, et: EdgeType, dk: NodeKind, dl: str) -> None:
            edges.append((sk.value, sl, et.value, dk.value, dl))

        # --- containment: file -> class -> method
        classes = {r["id"]: dict(r) for r in c.execute(
            "SELECT * FROM classes WHERE project_id=?", (project_id,))}
        files = {r["id"]: r["path"] for r in c.execute(
            "SELECT id, path FROM files WHERE project_id=?", (project_id,))}
        methods = [dict(r) for r in c.execute(
            "SELECT * FROM methods WHERE project_id=?", (project_id,))]

        method_name_index: dict[str, list[str]] = {}
        for cls in classes.values():
            fpath = files.get(cls["file_id"], "?")
            add(NodeKind.FILE, fpath, EdgeType.CONTAINS, NodeKind.CLASS, cls["name"])
            if cls["package"]:
                add(NodeKind.PACKAGE, cls["package"], EdgeType.CONTAINS, NodeKind.CLASS, cls["name"])
            if cls["extends_name"]:
                add(NodeKind.CLASS, cls["name"], EdgeType.EXTENDS, NodeKind.CLASS, cls["extends_name"])
            for impl in (cls["implements_names"] or "").split(","):
                impl = impl.strip()
                if impl:
                    add(NodeKind.CLASS, cls["name"], EdgeType.IMPLEMENTS, NodeKind.CLASS, impl)

        for m in methods:
            cls = classes.get(m["class_id"])
            cname = cls["name"] if cls else "?"
            qualified = f"{cname}.{m['name']}"
            add(NodeKind.CLASS, cname, EdgeType.CONTAINS, NodeKind.METHOD, qualified)
            method_name_index.setdefault(m["name"], []).append(qualified)

        # --- imports
        for r in c.execute(
            "SELECT f.path AS path, i.imported FROM imports i JOIN files f ON f.id=i.file_id "
            "WHERE i.project_id=?", (project_id,)):
            add(NodeKind.FILE, r["path"], EdgeType.IMPORTS, NodeKind.CLASS, r["imported"].split(".")[-1])

        # --- calls: caller method (or file) -> callee method/name
        for r in c.execute(
            "SELECT c.callee_name, c.line_number, m.name AS mname, cl.name AS cname, f.path AS fpath "
            "FROM calls c LEFT JOIN methods m ON m.id=c.caller_method_id "
            "LEFT JOIN classes cl ON cl.id=m.class_id JOIN files f ON f.id=c.caller_file_id "
            "WHERE c.project_id=?", (project_id,)):
            callee = r["callee_name"]
            targets = method_name_index.get(callee, [])
            dst_label = targets[0] if len(targets) == 1 else callee
            if r["mname"] and r["cname"]:
                add(NodeKind.METHOD, f'{r["cname"]}.{r["mname"]}', EdgeType.CALLS, NodeKind.METHOD, dst_label)
            else:
                add(NodeKind.FILE, r["fpath"], EdgeType.CALLS, NodeKind.METHOD, dst_label)

        # --- static SQL: method/class -> sql -> table
        for q in c.execute("SELECT * FROM sql_queries WHERE project_id=?", (project_id,)):
            sql_node = f'q{q["id"]}'
            src_label = None
            if q["source_class"] and q["source_method"]:
                src_label = f'{q["source_class"]}.{q["source_method"]}'
                add(NodeKind.METHOD, src_label, EdgeType.GENERATES_SQL, NodeKind.SQL, sql_node)
            elif q["source_class"]:
                add(NodeKind.CLASS, q["source_class"], EdgeType.GENERATES_SQL, NodeKind.SQL, sql_node)
            for t in c.execute("SELECT name, access FROM sql_tables WHERE query_id=?", (q["id"],)):
                et = EdgeType.WRITES_TABLE if t["access"] == "write" else EdgeType.READS_TABLE
                add(NodeKind.SQL, sql_node, et, NodeKind.TABLE, t["name"].upper())

        # --- dynamic SQL: method -> dynamic_sql -> (metadata) table
        for d in c.execute("SELECT * FROM dynamic_sql WHERE project_id=?", (project_id,)):
            dn = f'd{d["id"]}'
            if d["source_class"] and d["source_method"]:
                add(NodeKind.METHOD, f'{d["source_class"]}.{d["source_method"]}',
                    EdgeType.GENERATES_SQL, NodeKind.DYNAMIC_SQL, dn)
            for tbl in json.loads(d["tables_json"] or "[]"):
                add(NodeKind.DYNAMIC_SQL, dn, EdgeType.READS_TABLE, NodeKind.TABLE, tbl.upper())
            for dep in c.execute(
                "SELECT dependency_type, evidence_json FROM dynamic_sql_dependencies WHERE dynamic_sql_id=?",
                (d["id"],)):
                if dep["dependency_type"] != "METADATA_QUERY":
                    continue
                for e in json.loads(dep["evidence_json"] or "[]"):
                    for mt in e.get("metadata_tables", []):
                        add(NodeKind.DYNAMIC_SQL, dn, EdgeType.METADATA_LOOKUP, NodeKind.TABLE, mt.upper())

        # --- config: class -> configured_by -> config key (loose, name match)
        for r in c.execute(
            "SELECT DISTINCT key FROM config_entries WHERE project_id=? AND is_secret=0", (project_id,)):
            pass  # config edges are added on demand by the context builder; skipped here to keep the graph lean

        with transaction(self.conn) as cur:
            cur.execute("DELETE FROM dependencies WHERE project_id=?", (project_id,))
            cur.executemany(
                "INSERT INTO dependencies(project_id, src_kind, src_label, edge_type, dst_kind, dst_label) "
                "VALUES(?,?,?,?,?,?)",
                [(project_id, sk, sl, et, dk, dl) for (sk, sl, et, dk, dl) in _dedupe(edges)],
            )
        count = self.conn.execute(
            "SELECT COUNT(*) FROM dependencies WHERE project_id=?", (project_id,)).fetchone()[0]
        log.info("graph for project %s: %d edges", project_id, count)
        return count


def _dedupe(edges: list[tuple]) -> list[tuple]:
    seen, out = set(), []
    for e in edges:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out
