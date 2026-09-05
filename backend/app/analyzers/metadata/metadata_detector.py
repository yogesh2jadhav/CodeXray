"""
backend/app/analyzers/metadata/metadata_detector.py

Purpose
-------
Recognise a "metadata query": a method whose job is to run a small SQL statement
against a registry/metadata table and return a value (a physical table name, a
column list, a flag) that then feeds dynamic SQL (build plan Patterns C & D, §22).

Responsibility
--------------
- Given a `MethodInfo` from the project model, scan its body for an executed SQL
  string (constant reference or literal passed to prepareStatement / createQuery
  / executeQuery / query / jdbcTemplate calls).
- Parse that SQL (via the real SQL parser) to get the metadata table(s)/column(s).
- Classify what the method *returns* from its name:
    contains "table"  -> TABLE      (result is a table name)
    contains "column" -> COLUMN     (result is a column list)
    else              -> VALUE
- Return a `MetadataLookup` with full evidence, or ``None`` if the method does
  not look like a metadata accessor.

Deterministic. It reports the metadata SQL it actually found; it does not run it
and never fabricates the returned value (that is runtime-only).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.app.analyzers.sql.sql_parser import parse_sql

_EXECUTORS = ("prepareStatement", "createQuery", "executeQuery", "query", "queryForObject",
              "queryForList", "prepareCall", "nativeQuery")
_SQL_HINT = re.compile(r"^\s*\(?\s*(SELECT|WITH)\b", re.I)


def _is_real_query(sql: str) -> bool:
    """A metadata query must be a SELECT that actually reads from a table —
    not a bare keyword constant like "SELECT " used for string assembly."""
    return bool(_SQL_HINT.match(sql)) and re.search(r"\bFROM\s+[A-Za-z_]", sql, re.I) is not None


@dataclass
class MetadataLookup:
    method_qualified: str
    result_kind: str                      # TABLE | COLUMN | VALUE
    metadata_sql: str
    metadata_tables: list[str] = field(default_factory=list)
    metadata_columns: list[str] = field(default_factory=list)
    file: str | None = None
    line: int | None = None

    def as_dict(self) -> dict:
        return {
            "method": self.method_qualified,
            "result_kind": self.result_kind,
            "metadata_sql": self.metadata_sql,
            "metadata_tables": self.metadata_tables,
            "metadata_columns": self.metadata_columns,
            "file": self.file,
            "line": self.line,
        }


class MetadataQueryDetector:
    def __init__(self, project_model) -> None:
        self.model = project_model

    def _text(self, node, src: bytes) -> str:
        return src[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def _result_kind(self, method_name: str) -> str:
        low = method_name.lower()
        if "column" in low:
            return "COLUMN"
        if "table" in low:
            return "TABLE"
        return "VALUE"

    def _find_executed_sql(self, method) -> tuple[str, int] | None:
        """Walk the method body for a SQL string handed to an executor call."""
        src = method.src
        found: list[tuple[str, int]] = []

        def visit(node):
            if node.type == "method_invocation":
                name_node = node.child_by_field_name("name")
                nm = self._text(name_node, src) if name_node else ""
                if nm in _EXECUTORS:
                    args = node.child_by_field_name("arguments")
                    if args is not None:
                        for arg in args.children:
                            sql = self._arg_to_sql(arg, method)
                            if sql and _is_real_query(sql):
                                found.append((sql, node.start_point[0] + 1))
            for c in node.children:
                visit(c)

        visit(method.body_node)

        # Fallback: any SQL-looking constant referenced anywhere in the body.
        if not found:
            def visit2(node):
                if node.type in ("field_access", "identifier"):
                    txt = self._text(node, src)
                    qual, _, nm = txt.rpartition(".")
                    info = self.model.resolve_constant(qual or None, nm or txt)
                    if info and info.looks_like_sql and _is_real_query(info.value):
                        found.append((info.value, node.start_point[0] + 1))
                for c in node.children:
                    visit2(c)
            visit2(method.body_node)

        return found[0] if found else None

    def _arg_to_sql(self, arg, method) -> str | None:
        src = method.src
        if arg.type == "string_literal":
            raw = self._text(arg, src).strip().strip('"')
            return raw
        if arg.type in ("field_access", "identifier"):
            txt = self._text(arg, src)
            qual, _, nm = txt.rpartition(".")
            info = self.model.resolve_constant(qual or None, nm or txt)
            if info:
                return info.value
        return None

    def detect(self, method) -> MetadataLookup | None:
        hit = self._find_executed_sql(method)
        if hit is None:
            return None
        sql, line = hit
        parsed = parse_sql(sql)
        return MetadataLookup(
            method_qualified=method.qualified,
            result_kind=self._result_kind(method.name),
            metadata_sql=sql,
            metadata_tables=[t.name for t in parsed.tables],
            metadata_columns=[c.name for c in parsed.columns],
            file=method.file,
            line=line,
        )
