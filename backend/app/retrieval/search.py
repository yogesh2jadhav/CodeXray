"""
backend/app/retrieval/search.py

Purpose
-------
Read-side queries over the SQLite index for the Sprint 1 search API.

Responsibility
--------------
- `search_symbols`   — exact + partial symbol lookup (class / method / field / package).
- `search_keyword`   — substring match across symbols, SQL text and doc content,
                       with a simple configurable score (build plan §36).
- `search_files`     — path / name match.
- `search_sql`       — SQL text / table / column match.
- `find_table_usage` — every query (and its file) touching a table.
- `find_callers` / `find_references` — call-site lookups by callee name.

Semantic / vector retrieval is deliberately absent here — it arrives in Sprint 4
and will be blended in via the same weight config. Everything in this module is
deterministic and comes straight from parsed facts.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from backend.app.config.settings import get_settings
from backend.app.models.database import get_connection


def _conn(conn: sqlite3.Connection | None) -> sqlite3.Connection:
    return conn or get_connection()


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


def search_symbols(project_id: int, query: str, limit: int = 50, conn=None) -> list[dict]:
    c = _conn(conn)
    like = f"%{query}%"
    cur = c.execute(
        """
        SELECT s.kind, s.name, s.qualified, s.line_number, f.path AS file,
               CASE
                 WHEN s.qualified = :q OR s.name = :q THEN 3
                 WHEN s.name LIKE :starts THEN 2
                 ELSE 1
               END AS score
        FROM symbols s JOIN files f ON f.id = s.file_id
        WHERE s.project_id = :pid AND (s.name LIKE :like OR s.qualified LIKE :like)
        ORDER BY score DESC, length(s.qualified) ASC
        LIMIT :limit
        """,
        {"pid": project_id, "q": query, "starts": f"{query}%", "like": like, "limit": limit},
    )
    return _rows(cur)


def search_files(project_id: int, query: str, limit: int = 50, conn=None) -> list[dict]:
    c = _conn(conn)
    cur = c.execute(
        "SELECT id, path, language, size_bytes FROM files "
        "WHERE project_id = ? AND path LIKE ? ORDER BY length(path) ASC LIMIT ?",
        (project_id, f"%{query}%", limit),
    )
    return _rows(cur)


def search_sql(project_id: int, query: str, limit: int = 50, conn=None) -> list[dict]:
    c = _conn(conn)
    like = f"%{query}%"
    cur = c.execute(
        """
        SELECT DISTINCT q.id, q.query_type, q.origin, q.resolution_status, q.raw_sql,
               q.line_number, f.path AS file, q.source_class, q.source_method
        FROM sql_queries q
        JOIN files f ON f.id = q.file_id
        LEFT JOIN sql_tables t ON t.query_id = q.id
        LEFT JOIN sql_columns col ON col.query_id = q.id
        WHERE q.project_id = ?
          AND (q.raw_sql LIKE ? OR t.name LIKE ? OR col.name LIKE ?)
        ORDER BY q.id LIMIT ?
        """,
        (project_id, like, like, like, limit),
    )
    return _rows(cur)


def find_table_usage(project_id: int, table: str, conn=None) -> list[dict]:
    c = _conn(conn)
    cur = c.execute(
        """
        SELECT t.name AS table_name, t.access, q.id AS query_id, q.query_type,
               q.raw_sql, q.line_number, f.path AS file, q.source_class, q.source_method
        FROM sql_tables t
        JOIN sql_queries q ON q.id = t.query_id
        JOIN files f ON f.id = q.file_id
        WHERE q.project_id = ? AND t.name = ? COLLATE NOCASE
        ORDER BY f.path
        """,
        (project_id, table),
    )
    return _rows(cur)


def find_column_usage(project_id: int, column: str, conn=None) -> list[dict]:
    c = _conn(conn)
    cur = c.execute(
        """
        SELECT col.name AS column_name, col.table_ref, q.id AS query_id,
               q.raw_sql, f.path AS file
        FROM sql_columns col
        JOIN sql_queries q ON q.id = col.query_id
        JOIN files f ON f.id = q.file_id
        WHERE q.project_id = ? AND col.name = ? COLLATE NOCASE
        """,
        (project_id, column),
    )
    return _rows(cur)


def find_callers(project_id: int, method_name: str, conn=None) -> list[dict]:
    c = _conn(conn)
    cur = c.execute(
        """
        SELECT c.callee_qualifier, c.callee_name, c.line_number, f.path AS file,
               m.name AS caller_method, cl.name AS caller_class
        FROM calls c
        JOIN files f ON f.id = c.caller_file_id
        LEFT JOIN methods m ON m.id = c.caller_method_id
        LEFT JOIN classes cl ON cl.id = m.class_id
        WHERE c.project_id = ? AND c.callee_name = ?
        ORDER BY f.path, c.line_number
        """,
        (project_id, method_name),
    )
    return _rows(cur)


# find_references is currently an alias for find_callers (call-site level);
# field/type reference tracking lands with the Sprint 3 graph.
find_references = find_callers


def search_keyword(project_id: int, query: str, limit: int = 40, conn=None) -> list[dict]:
    """Blend symbol / SQL / doc matches into one scored list."""
    w = get_settings().retrieval_weights
    results: list[dict] = []

    for s in search_symbols(project_id, query, limit, conn):
        results.append({
            "type": "symbol", "score": w.get("symbol_exact", 5.0) * s["score"] / 3.0,
            "label": s["qualified"], "file": s["file"], "line": s["line_number"], "detail": s["kind"],
        })
    for q in search_sql(project_id, query, limit, conn):
        results.append({
            "type": "sql", "score": w.get("sql", 1.5),
            "label": f"{q['query_type']} @ {q['file']}", "file": q["file"],
            "line": q["line_number"], "detail": q["raw_sql"][:200],
        })
    for f in search_files(project_id, query, limit, conn):
        results.append({
            "type": "file", "score": w.get("path_match", 2.0),
            "label": f["path"], "file": f["path"], "line": None, "detail": f["language"],
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]
