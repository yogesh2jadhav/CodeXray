"""
backend/app/analyzers/sql/sql_parser.py

Purpose
-------
Parse a single SQL statement into structured facts using a real SQL parser
(sqlglot), never the LLM (build plan §16).

Responsibility
--------------
- Determine the statement type (SELECT / INSERT / UPDATE / DELETE / MERGE / DDL).
- Extract tables (with read/write intent), columns, joins, WHERE conditions and
  bind parameters (`?` and `:name`).
- Produce a normalized form for de-duplication.
- Degrade gracefully: if sqlglot is missing or the SQL does not parse (common for
  partially-resolved dynamic SQL), fall back to regex and set `parse_ok=False`
  rather than raising.

This module is deterministic. It reports what the SQL says; it never invents a
table or column that is not present in the text.
"""
from __future__ import annotations

import re

from backend.app.models.records import (
    ParsedSql,
    SqlColumnRef,
    SqlJoin,
    SqlTableRef,
)

_WRITE_TYPES = {"INSERT", "UPDATE", "DELETE", "MERGE"}
_PARAM_RE = re.compile(r"(?<!:):[A-Za-z_]\w*|\?")
_FROM_RE = re.compile(r"\bFROM\s+([A-Za-z_][\w.]*)", re.IGNORECASE)
_JOIN_RE = re.compile(r"\bJOIN\s+([A-Za-z_][\w.]*)", re.IGNORECASE)
_INTO_RE = re.compile(r"\b(?:INSERT\s+INTO|UPDATE|MERGE\s+INTO)\s+([A-Za-z_][\w.]*)", re.IGNORECASE)
_TYPE_RE = re.compile(r"^\s*\(?\s*(SELECT|INSERT|UPDATE|DELETE|MERGE|WITH|CREATE|ALTER|DROP|TRUNCATE)", re.IGNORECASE)


def _detect_type(sql: str) -> str:
    m = _TYPE_RE.match(sql)
    if not m:
        return "UNKNOWN"
    kw = m.group(1).upper()
    if kw == "WITH":
        return "SELECT"
    if kw in {"CREATE", "ALTER", "DROP", "TRUNCATE"}:
        return "DDL"
    return kw


def _normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().rstrip(";")


def _extract_params(sql: str) -> list[str]:
    return sorted({m.group(0) for m in _PARAM_RE.finditer(sql)})


# --------------------------------------------------------------------------- #
# sqlglot backend
# --------------------------------------------------------------------------- #
def _parse_with_sqlglot(sql: str) -> ParsedSql | None:
    try:
        import sqlglot
        from sqlglot import exp
    except Exception:
        return None

    try:
        tree = sqlglot.parse_one(sql, error_level="ignore")
    except Exception:
        return None
    if tree is None:
        return None

    result = ParsedSql(
        raw_sql=sql,
        normalized_sql=_normalize(sql),
        query_type=_detect_type(sql),
        parameters=_extract_params(sql),
        parse_ok=True,
    )

    write_type = result.query_type in _WRITE_TYPES
    # target table of a write statement
    target = tree.find(exp.Insert) or tree.find(exp.Update) or tree.find(exp.Delete) or tree.find(exp.Merge)

    for tbl in tree.find_all(exp.Table):
        name = tbl.name
        if not name:
            continue
        alias = tbl.alias_or_name if tbl.alias else None
        is_target = write_type and target is not None and tbl is (target.this if hasattr(target, "this") else None)
        result.tables.append(
            SqlTableRef(name=name, alias=alias, access="write" if is_target else ("write" if write_type and len(result.tables) == 0 else "read"))
        )

    for col in tree.find_all(exp.Column):
        result.columns.append(SqlColumnRef(name=col.name, table_ref=col.table or None))

    for join in tree.find_all(exp.Join):
        jt = (join.args.get("kind") or join.args.get("side") or "").upper() or "INNER"
        on = join.args.get("on")
        target_tbl = join.this.name if isinstance(join.this, exp.Table) else (join.this.sql() if join.this else None)
        result.joins.append(SqlJoin(join_type=jt, target=target_tbl, on_expr=on.sql() if on else None))

    where = tree.find(exp.Where)
    if where and where.this:
        for pred in where.this.flatten() if hasattr(where.this, "flatten") else [where.this]:
            result.conditions.append(pred.sql())

    # de-dupe columns / tables
    result.columns = _dedupe(result.columns, key=lambda c: (c.table_ref, c.name))
    result.tables = _dedupe(result.tables, key=lambda t: (t.name.lower(), t.access))
    return result


def _dedupe(items, key):
    seen, out = set(), []
    for it in items:
        k = key(it)
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


# --------------------------------------------------------------------------- #
# regex fallback backend
# --------------------------------------------------------------------------- #
def _parse_with_regex(sql: str) -> ParsedSql:
    result = ParsedSql(
        raw_sql=sql,
        normalized_sql=_normalize(sql),
        query_type=_detect_type(sql),
        parameters=_extract_params(sql),
        parse_ok=False,
        error="sqlglot unavailable or statement not parseable; regex extraction used",
    )
    write = result.query_type in _WRITE_TYPES
    for m in _FROM_RE.finditer(sql):
        result.tables.append(SqlTableRef(name=m.group(1), access="read"))
    for m in _INTO_RE.finditer(sql):
        result.tables.append(SqlTableRef(name=m.group(1), access="write"))
    for m in _JOIN_RE.finditer(sql):
        result.tables.append(SqlTableRef(name=m.group(1), access="read"))
        result.joins.append(SqlJoin(join_type="JOIN", target=m.group(1)))
    result.tables = _dedupe(result.tables, key=lambda t: (t.name.lower(), t.access))
    if write and not result.tables:
        result.tables.append(SqlTableRef(name="<unknown>", access="write"))
    return result


def parse_sql(sql: str) -> ParsedSql:
    """Parse one SQL statement. Never raises."""
    sql = (sql or "").strip()
    if not sql:
        return ParsedSql(raw_sql="", parse_ok=False, error="empty SQL")
    parsed = _parse_with_sqlglot(sql)
    if parsed is not None:
        return parsed
    return _parse_with_regex(sql)
