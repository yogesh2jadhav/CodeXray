"""
backend/app/models/database.py

Purpose
-------
Own the SQLite connection lifecycle and schema bootstrap for the CodeXray index.

Responsibility
--------------
- Create the DB file + parent directory on first use.
- Apply `schema.sql` idempotently (all statements are ``IF NOT EXISTS``).
- Hand out connections with sane pragmas (foreign keys on, WAL, row factory).
- Provide a tiny context-manager transaction helper used by the index writer.

This module knows nothing about Java, SQL parsing or HTTP — it is pure storage.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from backend.app.config.settings import get_settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _db_path() -> Path:
    return get_settings().index.database


# Additive migrations for columns introduced after a DB may have been created.
# Each entry: (table, column, column_definition). Applied best-effort; a
# "duplicate column" error simply means the DB is already current.
_MIGRATIONS: list[tuple[str, str, str]] = [
    ("dynamic_sql", "source_class", "TEXT"),
    ("dynamic_sql", "source_method", "TEXT"),
    ("dynamic_sql", "source_var", "TEXT"),
    ("dynamic_sql", "line_number", "INTEGER"),
    ("dynamic_sql", "tables_json", "TEXT"),
    ("dynamic_sql", "columns_json", "TEXT"),
    ("dynamic_sql_dependencies", "ordinal", "INTEGER NOT NULL DEFAULT 0"),
    ("dynamic_sql_dependencies", "evidence_json", "TEXT"),
    ("embeddings", "project_id", "INTEGER"),
    ("embeddings", "norm", "REAL NOT NULL DEFAULT 1.0"),
]


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, ddl in _MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        except sqlite3.OperationalError:
            pass  # column already present (or table not yet created)
    conn.commit()


def init_db(path: Path | None = None) -> Path:
    """Create the database file, apply the schema, then run additive migrations."""
    db_path = path or _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        _apply_migrations(conn)
        conn.commit()
    return db_path


# Cache: schema already ensured for these DB paths this process.
_SCHEMA_READY: set[str] = set()


def get_connection(path: Path | None = None) -> sqlite3.Connection:
    """Return a configured connection. The schema (all `IF NOT EXISTS`) + additive
    migrations are applied the first time this process touches a given DB file, so
    tables added in a later release appear in an existing index without a manual
    migration step."""
    db_path = path or _db_path()
    key = str(db_path)
    if key not in _SCHEMA_READY:
        init_db(db_path)
        _SCHEMA_READY.add(key)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Cursor]:
    """Commit on success, roll back on exception."""
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
