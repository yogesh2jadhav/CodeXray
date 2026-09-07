"""
backend/app/graph/service.py

Purpose
-------
Query the project dependency graph (build plan §19, §41, §57).

Responsibility
--------------
- Load the persisted `dependencies` rows into a NetworkX `MultiDiGraph`
  (cached per project; rebuilt when the edge count changes).
- Answer:
    * `callers(name)` / `callees(name)` / `call_path(a, b)`
    * `class_dependencies(class_name)` — what a class needs and what needs it
    * `table_consumers(table)` — methods/SQL that read or write a table
    * `neighbors(node)` — raw adjacency for the graph view
    * `impact_analysis(symbol, depth)` — reverse-reachable callers, plus the SQL
      and tables reachable forward, split into direct / indirect / data / test /
      unknown buckets (build plan §41).
- Resolve a loose selector ("loadEligibleCustomers", "CustomerService",
  "CustomerService.loadEligibleCustomers", "CUSTOMER") to a graph node.

Read-only. Everything returned carries the node ids it was derived from.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from backend.app.config.settings import get_settings
from backend.app.graph.schema import CALL_EDGES, EdgeType, NodeKind, node_id, parse_node_id
from backend.app.models.database import get_connection

_CACHE: dict[int, tuple[int, nx.MultiDiGraph]] = {}


def _load_graph(project_id: int, conn) -> nx.MultiDiGraph:
    rows = conn.execute(
        "SELECT src_kind, src_label, edge_type, dst_kind, dst_label FROM dependencies WHERE project_id=?",
        (project_id,),
    ).fetchall()
    cached = _CACHE.get(project_id)
    if cached and cached[0] == len(rows):
        return cached[1]

    g = nx.MultiDiGraph()
    for r in rows:
        s = node_id(r["src_kind"], r["src_label"])
        d = node_id(r["dst_kind"], r["dst_label"])
        g.add_node(s, kind=r["src_kind"], label=r["src_label"])
        g.add_node(d, kind=r["dst_kind"], label=r["dst_label"])
        g.add_edge(s, d, key=r["edge_type"], type=r["edge_type"])
    _CACHE[project_id] = (len(rows), g)
    return g


@dataclass
class ImpactResult:
    target: str
    direct: list[str] = field(default_factory=list)
    indirect: list[str] = field(default_factory=list)
    sql: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "target": self.target,
            "direct_impact": self.direct,
            "indirect_impact": self.indirect,
            "sql_impact": self.sql,
            "table_impact": self.tables,
            "test_impact": self.tests,
            "unknowns": self.unknowns,
        }


class GraphService:
    def __init__(self, project_id: int, conn=None) -> None:
        self.pid = project_id
        self.conn = conn or get_connection()
        self.g = _load_graph(project_id, self.conn)

    # ----------------------------------------------------------- resolution
    def resolve(self, selector: str) -> str | None:
        sel = selector.strip()
        candidates = [
            node_id(NodeKind.METHOD, sel),
            node_id(NodeKind.CLASS, sel),
            node_id(NodeKind.TABLE, sel.upper()),
            node_id(NodeKind.FILE, sel),
            node_id(NodeKind.PACKAGE, sel),
        ]
        for c in candidates:
            if self.g.has_node(c):
                return c
        # method simple-name -> unique qualified method
        matches = [
            n for n, data in self.g.nodes(data=True)
            if data.get("kind") == "method" and data.get("label", "").split(".")[-1] == sel
        ]
        if len(matches) == 1:
            return matches[0]
        # substring fallback
        subs = [n for n, d in self.g.nodes(data=True)
                if sel.lower() in d.get("label", "").lower()]
        return subs[0] if subs else None

    # ------------------------------------------------------------- queries
    def _edges_of(self, node: str, types: set[EdgeType], *, incoming: bool) -> list[str]:
        if not self.g.has_node(node):
            return []
        want = {t.value for t in types}
        it = self.g.in_edges(node, keys=True) if incoming else self.g.out_edges(node, keys=True)
        out = []
        for u, v, k in it:
            if k in want:
                out.append(u if incoming else v)
        return sorted(set(out))

    def callers(self, name: str) -> list[str]:
        node = self.resolve(name)
        return [parse_node_id(n)[1] for n in self._edges_of(node, CALL_EDGES, incoming=True)] if node else []

    def callees(self, name: str) -> list[str]:
        node = self.resolve(name)
        return [parse_node_id(n)[1] for n in self._edges_of(node, CALL_EDGES, incoming=False)] if node else []

    def call_tree(self, root: str, max_depth: int = 4, max_nodes: int = 30) -> dict:
        """Ordered interprocedural call tree from `root` (DFS over CALLS edges),
        with the SQL / tables / metadata each method touches. Recursion and the
        depth/size caps are marked so the caller (and the LLM) know the flow was
        bounded, not that it ends there."""
        start = self.resolve(root)
        if start is None:
            return {"root": root, "found": False, "steps": []}

        # method-node -> (file, line, signature)
        meta: dict[str, tuple[str | None, int | None, str | None]] = {}
        for r in self.conn.execute(
            "SELECT c.name AS cn, m.name AS mn, m.signature AS sig, m.line_start AS ls, f.path AS fp "
            "FROM methods m LEFT JOIN classes c ON c.id=m.class_id JOIN files f ON f.id=m.file_id "
            "WHERE m.project_id=?", (self.pid,),
        ):
            meta[f'{r["cn"]}.{r["mn"]}'] = (r["fp"], r["ls"], r["sig"])

        seen: set[str] = set()
        order: list[dict] = []
        truncated = {"depth": False, "size": False}

        def sql_touched(mnode: str) -> dict:
            tables_r, tables_w, meta_tables, statuses = set(), set(), set(), set()
            for _, sqln, k in self.g.out_edges(mnode, keys=True):
                if k != EdgeType.GENERATES_SQL.value:
                    continue
                statuses.add("dynamic" if parse_node_id(sqln)[1].startswith("d") else "static")
                for _, tbl, tk in self.g.out_edges(sqln, keys=True):
                    name = parse_node_id(tbl)[1]
                    if tk == EdgeType.WRITES_TABLE.value:
                        tables_w.add(name)
                    elif tk == EdgeType.METADATA_LOOKUP.value:
                        meta_tables.add(name)
                    elif tk == EdgeType.READS_TABLE.value:
                        tables_r.add(name)
            return {"reads": sorted(tables_r), "writes": sorted(tables_w),
                    "metadata_tables": sorted(meta_tables), "sql_kinds": sorted(statuses)}

        def visit(mnode: str, depth: int) -> None:
            if len(order) >= max_nodes:
                truncated["size"] = True
                return
            label = parse_node_id(mnode)[1]
            recursion = mnode in seen
            seen.add(mnode)
            fp, ls, sig = meta.get(label, (None, None, None))
            raw_callees = [v for _, v, k in self.g.out_edges(mnode, keys=True) if k == EdgeType.CALLS.value]
            # only follow callees that are methods DEFINED in this project (drop JDBC /
            # stdlib / framework calls that would flood the tree)
            callees = sorted({c for c in raw_callees if parse_node_id(c)[1] in meta})
            external = sorted({parse_node_id(c)[1] for c in raw_callees if parse_node_id(c)[1] not in meta})
            step = {
                "depth": depth,
                "method": label,
                "file": fp,
                "line": ls,
                "signature": sig,
                "recursion": recursion,
                "calls": [parse_node_id(c)[1] for c in callees],
                "external_calls": external[:12],
                "sql": sql_touched(mnode),
            }
            order.append(step)
            if recursion:
                return
            if depth >= max_depth:
                if callees:
                    truncated["depth"] = True
                return
            for c in callees:
                visit(c, depth + 1)

        visit(start, 0)
        return {
            "root": parse_node_id(start)[1],
            "found": True,
            "max_depth": max_depth,
            "truncated": truncated,
            "step_count": len(order),
            "steps": order,
        }

    def call_path(self, src: str, dst: str) -> list[str] | None:
        a, b = self.resolve(src), self.resolve(dst)
        if not a or not b:
            return None
        call_view = nx.DiGraph()
        for u, v, k in self.g.edges(keys=True):
            if k == EdgeType.CALLS.value:
                call_view.add_edge(u, v)
        try:
            return [parse_node_id(n)[1] for n in nx.shortest_path(call_view, a, b)]
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def table_consumers(self, table: str) -> dict:
        node = node_id(NodeKind.TABLE, table.upper())
        if not self.g.has_node(node):
            return {"table": table.upper(), "readers": [], "writers": []}
        readers, writers = set(), set()
        for u, v, k in self.g.in_edges(node, keys=True):
            producers = [p for p, _, _ in self.g.in_edges(u, keys=True)]  # method -> sql -> table
            bucket = writers if k == EdgeType.WRITES_TABLE.value else readers
            bucket.update(parse_node_id(p)[1] for p in producers)
            if k == EdgeType.METADATA_LOOKUP.value:
                readers.update(parse_node_id(p)[1] for p in producers)
        return {"table": table.upper(), "readers": sorted(readers), "writers": sorted(writers)}

    def class_dependencies(self, class_name: str) -> dict:
        cnode = node_id(NodeKind.CLASS, class_name)
        depends_on = set(self._edges_of(cnode, {EdgeType.EXTENDS, EdgeType.IMPLEMENTS}, incoming=False))
        used_by = set(self._edges_of(cnode, {EdgeType.EXTENDS, EdgeType.IMPLEMENTS}, incoming=True))
        # calls made by this class's methods
        for m in self._edges_of(cnode, {EdgeType.CONTAINS}, incoming=False):
            for callee in self._edges_of(m, CALL_EDGES, incoming=False):
                owner = parse_node_id(callee)[1].split(".")[0]
                if owner and owner != class_name:
                    depends_on.add(node_id(NodeKind.CLASS, owner))
            for caller in self._edges_of(m, CALL_EDGES, incoming=True):
                owner = parse_node_id(caller)[1].split(".")[0]
                if owner and owner != class_name:
                    used_by.add(node_id(NodeKind.CLASS, owner))
        return {
            "class": class_name,
            "depends_on": sorted({parse_node_id(n)[1] for n in depends_on}),
            "used_by": sorted({parse_node_id(n)[1] for n in used_by}),
        }

    def neighbors(self, selector: str, limit: int = 60) -> dict:
        node = self.resolve(selector)
        if not node:
            return {"node": None, "edges": []}
        edges = []
        for u, v, k in list(self.g.in_edges(node, keys=True))[:limit]:
            edges.append({"from": parse_node_id(u)[1], "to": parse_node_id(v)[1], "type": k})
        for u, v, k in list(self.g.out_edges(node, keys=True))[:limit]:
            edges.append({"from": parse_node_id(u)[1], "to": parse_node_id(v)[1], "type": k})
        return {"node": parse_node_id(node)[1], "kind": parse_node_id(node)[0], "edges": edges}

    # --------------------------------------------------------- impact (§41)
    def impact_analysis(self, symbol: str, depth: int | None = None) -> ImpactResult:
        depth = depth or get_settings().graph.max_impact_depth
        node = self.resolve(symbol)
        res = ImpactResult(target=symbol)
        if not node:
            res.unknowns.append(f"symbol '{symbol}' not found in the graph")
            return res

        # A class target: aggregate the impact of each of its methods.
        if parse_node_id(node)[0] == NodeKind.CLASS.value:
            methods = self._edges_of(node, {EdgeType.CONTAINS}, incoming=False)
            methods = [m for m in methods if parse_node_id(m)[0] == NodeKind.METHOD.value]
            if methods:
                for m in methods:
                    sub = self.impact_analysis(parse_node_id(m)[1], depth)
                    res.direct += sub.direct
                    res.indirect += sub.indirect
                    res.sql += sub.sql
                    res.tables += sub.tables
                    res.tests += sub.tests
                for f in (res.direct, res.indirect, res.sql, res.tables, res.tests):
                    f[:] = sorted(set(f))
                res.direct = [d for d in res.direct if d.split(".")[0] != parse_node_id(node)[1]]
                if not res.direct and not res.indirect:
                    res.unknowns.append(f"no external callers of {symbol}'s methods found")
                return res

        # reverse BFS over CALLS for caller reachability
        call_view = nx.DiGraph()
        for u, v, k in self.g.edges(keys=True):
            if k == EdgeType.CALLS.value:
                call_view.add_edge(u, v)
        level = {node}
        seen = {node}
        for d in range(depth):
            nxt = set()
            for n in level:
                if n in call_view:
                    nxt |= set(call_view.predecessors(n))
            nxt -= seen
            if not nxt:
                break
            labels = sorted(parse_node_id(n)[1] for n in nxt)
            (res.direct if d == 0 else res.indirect).extend(labels)
            seen |= nxt
            level = nxt

        # forward: SQL + tables this symbol (and its callees) touch
        fwd = {node} | ({n for n in nx.descendants(call_view, node)} if node in call_view else set())
        for n in fwd:
            for _, v, k in self.g.out_edges(n, keys=True):
                vk, vl = parse_node_id(v)
                if k == EdgeType.GENERATES_SQL.value:
                    res.sql.append(vl)
                    for _, t, tk in self.g.out_edges(v, keys=True):
                        if tk in (EdgeType.READS_TABLE.value, EdgeType.WRITES_TABLE.value, EdgeType.METADATA_LOOKUP.value):
                            res.tables.append(parse_node_id(t)[1])
        res.sql = sorted(set(res.sql))
        res.tables = sorted(set(res.tables))

        # tests: any caller whose class name looks like a test
        res.tests = sorted({c for c in (res.direct + res.indirect)
                            if "test" in c.lower() or c.lower().endswith("it")})
        if not res.direct and not res.indirect:
            res.unknowns.append("no static callers found — may be an entry point, reflective, or DI-wired")
        # dynamic-sql sites are inherently partial
        for s in res.sql:
            if s.startswith("d"):
                res.unknowns.append(f"dynamic SQL {s} is not fully resolved; downstream table set may be incomplete")
        return res
