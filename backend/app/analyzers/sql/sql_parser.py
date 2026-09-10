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

    # The AST walk below can hit malformed / dialect-specific nodes (e.g. a Table
    # expression whose `.this` is None, so `.name` raises). Any such failure must
    # degrade to "partial parse", never crash the indexer.
    try:
        _walk_sqlglot(tree, exp, result)
    except Exception as exc:  # pragma: no cover - defensive
        result.parse_ok = False
        result.error = f"sqlglot AST walk failed ({exc}); partial result"
        # keep whatever we collected before the failure
    return result


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _walk_sqlglot(tree, exp, result: ParsedSql) -> None:
    write_type = result.query_type in _WRITE_TYPES
    target = tree.find(exp.Insert) or tree.find(exp.Update) or tree.find(exp.Delete) or tree.find(exp.Merge)

    for tbl in tree.find_all(exp.Table):
        name = _safe(lambda: tbl.name)
        if not name:
            continue
        alias = _safe(lambda: tbl.alias_or_name if tbl.alias else None)
        is_target = write_type and target is not None and tbl is _safe(lambda: getattr(target, "this", None))
        access = "write" if is_target else ("write" if write_type and not result.tables else "read")
        result.tables.append(SqlTableRef(name=name, alias=alias, access=access))

    for col in tree.find_all(exp.Column):
        name = _safe(lambda: col.name)
        if name:
            result.columns.append(SqlColumnRef(name=name, table_ref=_safe(lambda: col.table or None)))

    for join in tree.find_all(exp.Join):
        jt = (_safe(lambda: join.args.get("kind")) or _safe(lambda: join.args.get("side")) or "").upper() or "INNER"
        on = _safe(lambda: join.args.get("on"))
        this = _safe(lambda: join.this)
        target_tbl = _safe(lambda: this.name) if isinstance(this, exp.Table) else _safe(lambda: this.sql() if this else None)
        result.joins.append(SqlJoin(join_type=jt, target=target_tbl, on_expr=_safe(lambda: on.sql() if on else None)))

    where = tree.find(exp.Where)
    if where and where.this:
        preds = _safe(lambda: list(where.this.flatten()), None) if hasattr(where.this, "flatten") else [where.this]
        for pred in (preds or []):
            s = _safe(lambda: pred.sql())
            if s:
                result.conditions.append(s)

    result.columns = _dedupe(result.columns, key=lambda c: (c.table_ref, c.name))
    result.tables = _dedupe(result.tables, key=lambda t: (t.name.lower(), t.access))


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
    """Parse one SQL statement. NEVER raises — a bad statement must not stop
    indexing a file."""
    sql = (sql or "").strip()
    if not sql:
        return ParsedSql(raw_sql="", parse_ok=False, error="empty SQL")
    try:
        parsed = _parse_with_sqlglot(sql)
        if parsed is not None:
            return parsed
    except Exception as exc:  # pragma: no cover - defensive backstop
        pass
    try:
        return _parse_with_regex(sql)
    except Exception as exc:  # pragma: no cover
        return ParsedSql(raw_sql=sql, normalized_sql=_normalize(sql), query_type=_detect_type(sql),
                         parse_ok=False, error=f"parse failed: {exc}")
