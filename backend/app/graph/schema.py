"""
backend/app/graph/schema.py

Purpose
-------
Vocabulary and id helpers for the project dependency graph (build plan §19, §57).

Responsibility
--------------
- `EdgeType`: the relationship set the graph supports
  (CONTAINS / IMPORTS / EXTENDS / IMPLEMENTS / CALLS / REFERENCES /
   GENERATES_SQL / READS_TABLE / WRITES_TABLE / METADATA_LOOKUP / CONFIGURED_BY).
- `NodeKind`: what a node represents (package / class / method / file / table /
  sql / dynamic_sql / config).
- `node_id()` / `parse_node_id()`: stable `"kind:label"` string ids used both in
  the persisted `dependencies` table and the in-memory NetworkX graph.

Pure constants + string helpers.
"""
from __future__ import annotations

from enum import Enum


class EdgeType(str, Enum):
    CONTAINS = "CONTAINS"
    IMPORTS = "IMPORTS"
    EXTENDS = "EXTENDS"
    IMPLEMENTS = "IMPLEMENTS"
    CALLS = "CALLS"
    REFERENCES = "REFERENCES"
    GENERATES_SQL = "GENERATES_SQL"
    READS_TABLE = "READS_TABLE"
    WRITES_TABLE = "WRITES_TABLE"
    METADATA_LOOKUP = "METADATA_LOOKUP"
    CONFIGURED_BY = "CONFIGURED_BY"


class NodeKind(str, Enum):
    PACKAGE = "package"
    CLASS = "class"
    METHOD = "method"
    FILE = "file"
    TABLE = "table"
    SQL = "sql"
    DYNAMIC_SQL = "dynamic_sql"
    CONFIG = "config"


CALL_EDGES = {EdgeType.CALLS}
SQL_EDGES = {EdgeType.GENERATES_SQL, EdgeType.READS_TABLE, EdgeType.WRITES_TABLE, EdgeType.METADATA_LOOKUP}
TYPE_EDGES = {EdgeType.EXTENDS, EdgeType.IMPLEMENTS, EdgeType.IMPORTS}


def node_id(kind: NodeKind | str, label: str) -> str:
    k = kind.value if isinstance(kind, NodeKind) else str(kind)
    return f"{k}:{label}"


def parse_node_id(nid: str) -> tuple[str, str]:
    kind, _, label = nid.partition(":")
    return kind, label
