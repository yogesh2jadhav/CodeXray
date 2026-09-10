"""
backend/app/graph/export.py

Purpose
-------
Export the project dependency graph for external visualisation / querying —
Neo4j (Cypher), GraphML (Gephi / yEd / Cytoscape), Graphviz DOT, and
Cytoscape-style JSON (build plan §19: SQLite stays the source of truth, Neo4j is
an *optional* target).

Responsibility
--------------
- Read the persisted `dependencies` edges and enrich each node with the facts
  CodeXray already has: kind (package/class/interface/method/table/sql/…),
  file, line, package, architecture role + layer, one-line method purpose,
  dynamic-SQL resolution status.
- Emit any of the formats above from that single enriched model.
- `push_to_neo4j()` (optional): if the `neo4j` driver is installed and a bolt
  URL is configured, MERGE the nodes/edges straight into a running instance.

Deterministic; no analysis here — it just reshapes the existing graph.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import escape

from backend.app.graph.schema import NodeKind, parse_node_id
from backend.app.models.database import get_connection

# NodeKind -> Neo4j label (PascalCase) and GraphML/DOT group.
_LABELS = {
    "package": "Package",
    "class": "Class",
    "interface": "Interface",
    "method": "Method",
    "file": "File",
    "table": "Table",
    "sql": "SqlQuery",
    "dynamic_sql": "DynamicSql",
    "config": "Config",
    "module": "Module",
}
_COLORS = {
    "Module": "#6f42c1", "Package": "#4c9aff", "Class": "#2ea043", "Interface": "#3fb950",
    "Method": "#8b97a5", "File": "#586069", "Table": "#d29922", "SqlQuery": "#f0883e",
    "DynamicSql": "#f85149", "Config": "#a371f7",
}
_IDENT = re.compile(r"[^A-Za-z0-9_]")


@dataclass
class GNode:
    id: str
    label: str            # display name
    kind: str             # Neo4j label
    props: dict = field(default_factory=dict)


@dataclass
class GEdge:
    src: str
    dst: str
    type: str
    props: dict = field(default_factory=dict)


class GraphExporter:
    def __init__(self, project_id: int, conn=None, *, include_external: bool = False) -> None:
        self.pid = project_id
        self.conn = conn or get_connection()
        self.include_external = include_external   # keep JDBC/stdlib class & method nodes?
        self.nodes: dict[str, GNode] = {}
        self.edges: list[GEdge] = []
        self._build()

    # ------------------------------------------------------------------ build
    def _enrich(self):
        c = self.conn
        classes = {
            r["name"]: dict(r)
            for r in c.execute(
                "SELECT name, package, kind, visibility, fully_qualified_name FROM classes WHERE project_id=?",
                (self.pid,),
            )
        }
        roles = {
            r["class_name"]: (r["role"], r["layer"])
            for r in c.execute(
                "SELECT class_name, role, layer FROM architecture_components WHERE project_id=?", (self.pid,)
            )
        }
        summaries = {
            r["qualified"]: r["summary"]
            for r in c.execute("SELECT qualified, summary FROM method_summaries WHERE project_id=?", (self.pid,))
        }
        method_loc = {
            f'{r["cn"]}.{r["mn"]}': (r["fp"], r["ls"])
            for r in c.execute(
                "SELECT c.name AS cn, m.name AS mn, f.path AS fp, m.line_start AS ls "
                "FROM methods m LEFT JOIN classes c ON c.id=m.class_id JOIN files f ON f.id=m.file_id "
                "WHERE m.project_id=?", (self.pid,),
            )
        }
        dyn_status = {
            f'd{r["id"]}': r["resolution_status"]
            for r in c.execute("SELECT id, resolution_status FROM dynamic_sql WHERE project_id=?", (self.pid,))
        }
        return classes, roles, summaries, method_loc, dyn_status

    def _node(self, kind_raw: str, label: str, **props) -> str:
        nid = f"{kind_raw}:{label}"
        kind = _LABELS.get(kind_raw, kind_raw.capitalize())
        if nid not in self.nodes:
            self.nodes[nid] = GNode(id=nid, label=label, kind=kind, props={k: v for k, v in props.items() if v is not None})
        else:
            self.nodes[nid].props.update({k: v for k, v in props.items() if v is not None})
        return nid

    def _build(self) -> None:
        classes, roles, summaries, method_loc, dyn_status = self._enrich()
        known_methods = set(method_loc) | {  # constructors have no summary/loc row
            f'{r["cn"]}.{r["mn"]}' for r in self.conn.execute(
                "SELECT c.name AS cn, m.name AS mn FROM methods m LEFT JOIN classes c ON c.id=m.class_id "
                "WHERE m.project_id=?", (self.pid,))
        }
        rows = self.conn.execute(
            "SELECT src_kind, src_label, edge_type, dst_kind, dst_label FROM dependencies WHERE project_id=?",
            (self.pid,),
        ).fetchall()

        def is_external(kind: str, label: str) -> bool:
            if self.include_external:
                return False
            if kind in ("class", "interface"):
                return label not in classes
            if kind == "method":
                return label not in known_methods            # bare name or unresolved => stdlib/framework
            return False

        for r in rows:
            sk, sl, et, dk, dl = r["src_kind"], r["src_label"], r["edge_type"], r["dst_kind"], r["dst_label"]
            if is_external(sk, sl) or is_external(dk, dl):
                continue
            s = self._node(sk, sl, **self._props_for(sk, sl, classes, roles, summaries, method_loc, dyn_status))
            d = self._node(dk, dl, **self._props_for(dk, dl, classes, roles, summaries, method_loc, dyn_status))
            self.edges.append(GEdge(src=s, dst=d, type=et))

        # Module layer: group packages by their first 2 segments.
        for n in list(self.nodes.values()):
            if n.kind == "Package":
                mod = ".".join(n.label.split(".")[:2]) or n.label
                mid = self._node("module", mod)
                self.edges.append(GEdge(src=mid, dst=n.id, type="CONTAINS"))

    def _props_for(self, kind, label, classes, roles, summaries, method_loc, dyn_status) -> dict:
        p: dict = {}
        if kind == NodeKind.CLASS.value and label in classes:
            cl = classes[label]
            p.update(package=cl["package"], visibility=cl["visibility"], fqn=cl["fully_qualified_name"])
            if label in roles:
                p["role"], p["layer"] = roles[label]
        elif kind == NodeKind.METHOD.value:
            p["purpose"] = summaries.get(label)
            if label in method_loc:
                p["file"], p["line"] = method_loc[label]
            p["class"] = label.split(".")[0]
        elif kind == NodeKind.DYNAMIC_SQL.value:
            p["status"] = dyn_status.get(label)
        elif kind == NodeKind.TABLE.value:
            p["name"] = label
        return p

    # --------------------------------------------------------------- formats
    def to_cypher(self, database_hint: str | None = None) -> str:
        out: list[str] = [
            "// CodeXray graph export — load in Neo4j Browser or `cypher-shell -f graph.cypher`",
            "// Safe to re-run: every node/edge is MERGE'd on a stable id.",
            "",
        ]
        by_kind: dict[str, list[GNode]] = {}
        for n in self.nodes.values():
            by_kind.setdefault(n.kind, []).append(n)
        for kind, group in by_kind.items():
            out.append(f"// --- {kind} ({len(group)}) ---")
            for n in group:
                props = {"id": n.id, "name": n.label, **n.props}
                out.append(f"MERGE (n:{kind} {{id:{_cy(n.id)}}}) SET n += {_cy_map(props)};")
            out.append("")
        out.append("// --- relationships ---")
        for e in self.edges:
            out.append(
                f"MATCH (a {{id:{_cy(e.src)}}}), (b {{id:{_cy(e.dst)}}}) "
                f"MERGE (a)-[:{e.type}]->(b);"
            )
        return "\n".join(out) + "\n"

    def to_graphml(self) -> str:
        keys = {
            "label": "string", "kind": "string", "package": "string", "role": "string",
            "layer": "string", "purpose": "string", "file": "string", "status": "string", "color": "string",
        }
        head = ['<?xml version="1.0" encoding="UTF-8"?>',
                '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">']
        for k, t in keys.items():
            head.append(f'<key id="{k}" for="node" attr.name="{k}" attr.type="{t}"/>')
        head.append('<key id="type" for="edge" attr.name="type" attr.type="string"/>')
        head.append('<graph edgedefault="directed">')
        body: list[str] = []
        for n in self.nodes.values():
            body.append(f'<node id="{escape(n.id)}">')
            body.append(f'<data key="label">{escape(n.label)}</data>')
            body.append(f'<data key="kind">{escape(n.kind)}</data>')
            body.append(f'<data key="color">{_COLORS.get(n.kind, "#888")}</data>')
            for k in ("package", "role", "layer", "purpose", "file", "status"):
                if n.props.get(k):
                    body.append(f'<data key="{k}">{escape(str(n.props[k]))}</data>')
            body.append("</node>")
        for i, e in enumerate(self.edges):
            body.append(f'<edge id="e{i}" source="{escape(e.src)}" target="{escape(e.dst)}">'
                        f'<data key="type">{escape(e.type)}</data></edge>')
        return "\n".join(head + body + ["</graph>", "</graphml>"]) + "\n"

    def to_dot(self) -> str:
        lines = ["digraph codexray {", '  rankdir=LR;', '  node [style=filled, fontname="Helvetica"];']
        for n in self.nodes.values():
            lbl = n.label if len(n.label) < 40 else n.label[:37] + "…"
            lines.append(f'  "{n.id}" [label="{_dot(lbl)}", fillcolor="{_COLORS.get(n.kind, "#dddddd")}"];')
        for e in self.edges:
            lines.append(f'  "{e.src}" -> "{e.dst}" [label="{e.type}"];')
        lines.append("}")
        return "\n".join(lines) + "\n"

    def to_cytoscape_json(self) -> dict:
        return {
            "elements": {
                "nodes": [
                    {"data": {"id": n.id, "label": n.label, "kind": n.kind, **n.props}} for n in self.nodes.values()
                ],
                "edges": [
                    {"data": {"id": f"e{i}", "source": e.src, "target": e.dst, "type": e.type}}
                    for i, e in enumerate(self.edges)
                ],
            },
            "stats": {"nodes": len(self.nodes), "edges": len(self.edges)},
        }

    # ------------------------------------------------------------ direct load
    def push_to_neo4j(self, uri: str, user: str, password: str, wipe: bool = True) -> dict:
        try:
            from neo4j import GraphDatabase
            from neo4j.exceptions import ServiceUnavailable
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("pip install neo4j to load directly, or use --format cypher and cypher-shell") from exc

        # `neo4j://` forces a cluster routing-table lookup that a single Desktop /
        # Community instance does not serve — retry once over a direct `bolt://`.
        candidates = [uri]
        if uri.startswith("neo4j://"):
            candidates.append("bolt://" + uri[len("neo4j://"):])
        elif uri.startswith("neo4j+s://"):
            candidates.append("bolt+s://" + uri[len("neo4j+s://"):])

        last_err: Exception | None = None
        for u in candidates:
            driver = GraphDatabase.driver(u, auth=(user, password))
            try:
                driver.verify_connectivity()
                with driver.session() as session:
                    if wipe:
                        session.run("MATCH (n) WHERE n.codexray_project = $p DETACH DELETE n", p=self.pid)
                    for n in self.nodes.values():
                        session.run(
                            f"MERGE (x:`{n.kind}` {{id:$id}}) SET x += $props, x.codexray_project=$p",
                            id=n.id, props={"name": n.label, **n.props}, p=self.pid,
                        )
                    for e in self.edges:
                        session.run(
                            f"MATCH (a {{id:$s}}),(b {{id:$d}}) MERGE (a)-[:`{e.type}`]->(b)",
                            s=e.src, d=e.dst,
                        )
                driver.close()
                return {"loaded": True, "nodes": len(self.nodes), "edges": len(self.edges), "uri": u}
            except Exception as exc:  # ServiceUnavailable, ConfigurationError, auth…
                last_err = exc
                driver.close()

        raise RuntimeError(
            f"could not connect to Neo4j at {uri}"
            + (f" (also tried {candidates[1]})" if len(candidates) > 1 else "")
            + f": {last_err}. Is the instance started? Check the port, and prefer bolt://<host>:7687 "
            f"for a single Desktop/Community instance."
        )


# --------------------------------------------------------------------- helpers
def _cy(s: str) -> str:
    return json.dumps(s)


def _cy_map(d: dict) -> str:
    return "{" + ", ".join(f"{_IDENT.sub('_', k)}: {json.dumps(v)}" for k, v in d.items()) + "}"


def _dot(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')
