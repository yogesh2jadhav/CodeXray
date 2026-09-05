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


def get_connection(path: Path | None = None) -> sqlite3.Connection:
    """Return a configured connection, initialising the schema if needed."""
    db_path = path or _db_path()
    if not db_path.exists():
        init_db(db_path)
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
