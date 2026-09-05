"""
backend/app/indexing/index_writer.py

Purpose
-------
Persist everything the analyzers produce into the SQLite index.

Responsibility
--------------
- Project + analysis-run bookkeeping.
- Incremental gate: `register_file` returns whether a file changed since last run
  (content-hash comparison) so the orchestrator can skip unchanged files.
- Idempotent writes: `clear_file_data` removes all rows derived from a file
  before it is re-parsed, so re-indexing never duplicates.
- Translate `records.*` dataclasses into rows for `schema.sql`.
- Record parse failures without aborting the run (build plan §74).

This is the only module that issues INSERT/DELETE against the index.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from backend.app.analyzers.java.java_parser import looks_like_sql
from backend.app.indexing.symbol_extractor import Symbol
from backend.app.models.database import get_connection, transaction
from backend.app.models.records import (
    ConfigEntry,
    JavaFileParse,
    ScannedFile,
    SqlOccurrence,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class IndexWriter:
    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self.conn = conn or get_connection()

    # ----------------------------------------------------------------- project
    def upsert_project(self, name: str, root_path: str) -> int:
        with transaction(self.conn) as cur:
            cur.execute(
                "INSERT INTO projects(name, root_path) VALUES(?, ?) "
                "ON CONFLICT(name) DO UPDATE SET root_path=excluded.root_path",
                (name, root_path),
            )
            cur.execute("SELECT id FROM projects WHERE name = ?", (name,))
            return int(cur.fetchone()[0])

    def mark_project_indexed(self, project_id: int) -> None:
        with transaction(self.conn) as cur:
            cur.execute("UPDATE projects SET last_indexed = ? WHERE id = ?", (_now(), project_id))

    # ------------------------------------------------------------- analysis run
    def start_run(self, project_id: int, mode: str, files_total: int) -> int:
        with transaction(self.conn) as cur:
            cur.execute(
                "INSERT INTO analysis_runs(project_id, mode, files_total) VALUES(?, ?, ?)",
                (project_id, mode, files_total),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, indexed: int, skipped: int, failed: int, status: str) -> None:
        with transaction(self.conn) as cur:
            cur.execute(
                "UPDATE analysis_runs SET finished_at=?, files_indexed=?, files_skipped=?, "
                "files_failed=?, status=? WHERE id=?",
                (_now(), indexed, skipped, failed, status, run_id),
            )

    # ------------------------------------------------------------------- files
    def register_file(self, project_id: int, sf: ScannedFile, incremental: bool) -> tuple[int, bool]:
        """Insert/update the files row. Returns (file_id, changed?)."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id, content_hash FROM files WHERE project_id=? AND path=?",
            (project_id, sf.rel_path),
        )
        row = cur.fetchone()
        if row is None:
            with transaction(self.conn) as c:
                c.execute(
                    "INSERT INTO files(project_id, path, abs_path, language, extension, size_bytes, content_hash) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (project_id, sf.rel_path, sf.abs_path, sf.language.value, sf.extension, sf.size_bytes, sf.content_hash),
                )
                return int(c.lastrowid), True

        file_id = int(row[0])
        changed = row[1] != sf.content_hash
        if changed or not incremental:
            with transaction(self.conn) as c:
                c.execute(
                    "UPDATE files SET abs_path=?, language=?, extension=?, size_bytes=?, content_hash=? WHERE id=?",
                    (sf.abs_path, sf.language.value, sf.extension, sf.size_bytes, sf.content_hash, file_id),
                )
        return file_id, (changed or not incremental)

    def clear_file_data(self, project_id: int, file_id: int) -> None:
        """Remove every derived row for a file so it can be re-parsed cleanly."""
        # (table, column) pairs — `calls` uses caller_file_id, everything else file_id.
        targets = [
            ("packages", "file_id"), ("classes", "file_id"), ("methods", "file_id"),
            ("fields", "file_id"), ("imports", "file_id"), ("calls", "caller_file_id"),
            ("symbols", "file_id"), ("config_entries", "file_id"), ("documents", "file_id"),
        ]
        with transaction(self.conn) as cur:
            for t, col in targets:
                cur.execute(f"DELETE FROM {t} WHERE {col} = ?", (file_id,))
            # sql_queries children cascade via FK when the parent is deleted
            cur.execute("DELETE FROM sql_queries WHERE file_id = ?", (file_id,))
            cur.execute("DELETE FROM parse_failures WHERE project_id=? AND file_path=(SELECT path FROM files WHERE id=?)",
                        (project_id, file_id))

    def mark_indexed(self, file_id: int) -> None:
        with transaction(self.conn) as cur:
            cur.execute("UPDATE files SET indexed_at=? WHERE id=?", (_now(), file_id))

    # ------------------------------------------------------------------ failures
    def record_failure(self, project_id: int, file_path: str, parser: str, error: str) -> None:
        with transaction(self.conn) as cur:
            cur.execute(
                "INSERT INTO parse_failures(project_id, file_path, parser, error) VALUES(?,?,?,?)",
                (project_id, file_path, parser, error[:2000]),
            )

    # --------------------------------------------------------------------- java
    def write_java(
        self,
        project_id: int,
        file_id: int,
        java: JavaFileParse,
        symbols: list[Symbol],
    ) -> None:
        with transaction(self.conn) as cur:
            if java.package:
                cur.execute(
                    "INSERT INTO packages(project_id, name, file_id) VALUES(?,?,?)",
                    (project_id, java.package, file_id),
                )

            for imp in java.imports:
                cur.execute(
                    "INSERT INTO imports(project_id, file_id, imported, is_static, is_wildcard) VALUES(?,?,?,?,?)",
                    (project_id, file_id, imp.imported, int(imp.is_static), int(imp.is_wildcard)),
                )

            class_ids: list[int] = []
            method_ids: dict[str, int] = {}   # "ci.mi" -> method_id
            field_ids: dict[str, int] = {}

            for ci, cls in enumerate(java.classes):
                cur.execute(
                    "INSERT INTO classes(project_id, file_id, name, fully_qualified_name, package, visibility, "
                    "kind, extends_name, implements_names, line_start, line_end) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        project_id, file_id, cls.name, cls.fully_qualified_name, cls.package, cls.visibility,
                        cls.kind, cls.extends_name, ",".join(cls.implements_names) or None,
                        cls.line_start, cls.line_end,
                    ),
                )
                cid = int(cur.lastrowid)
                class_ids.append(cid)

                for mi, m in enumerate(cls.methods):
                    cur.execute(
                        "INSERT INTO methods(project_id, class_id, file_id, name, signature, return_type, "
                        "parameters, visibility, is_static, line_start, line_end) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            project_id, cid, file_id, m.name, m.signature, m.return_type, m.parameters,
                            m.visibility, int(m.is_static), m.line_start, m.line_end,
                        ),
                    )
                    method_ids[f"{ci}.m{mi}"] = int(cur.lastrowid)

                for fi, f in enumerate(cls.fields):
                    cur.execute(
                        "INSERT INTO fields(project_id, class_id, file_id, name, type_name, visibility, "
                        "is_static, is_final, string_value, looks_like_sql, line_start) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            project_id, cid, file_id, f.name, f.type_name, f.visibility, int(f.is_static),
                            int(f.is_final), f.string_value, int(f.looks_like_sql or looks_like_sql(f.string_value)),
                            f.line_start,
                        ),
                    )
                    field_ids[f"{ci}.f{fi}"] = int(cur.lastrowid)

            # calls — resolve enclosing method id where we can
            method_by_name: dict[str, int] = {}
            for key, mid in method_ids.items():
                ci = int(key.split(".")[0])
                mi = int(key.split("m")[1])
                method_by_name[java.classes[ci].methods[mi].name] = mid
            for call in java.calls:
                cur.execute(
                    "INSERT INTO calls(project_id, caller_method_id, caller_file_id, callee_qualifier, "
                    "callee_name, arg_count, line_number) VALUES(?,?,?,?,?,?,?)",
                    (
                        project_id, method_by_name.get(call.enclosing_method or ""), file_id,
                        call.callee_qualifier, call.callee_name, call.arg_count, call.line_number,
                    ),
                )

            # symbols — back-link ref_id to the real row
            for sym in symbols:
                ref_id = None
                if sym.kind in ("class", "interface"):
                    idx = int(sym.locator)
                    ref_id = class_ids[idx] if idx < len(class_ids) else None
                elif sym.kind == "method":
                    ref_id = method_ids.get(sym.locator)
                elif sym.kind == "field":
                    ref_id = field_ids.get(sym.locator)
                cur.execute(
                    "INSERT INTO symbols(project_id, kind, name, qualified, file_id, line_number, ref_id) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (project_id, sym.kind, sym.name, sym.qualified, file_id, sym.line_number, ref_id),
                )

    # ---------------------------------------------------------------------- sql
    def write_sql(self, project_id: int, file_id: int, occurrences: list[SqlOccurrence]) -> None:
        with transaction(self.conn) as cur:
            for occ in occurrences:
                p = occ.parsed
                cur.execute(
                    "INSERT INTO sql_queries(project_id, file_id, source_class, source_method, origin, "
                    "query_type, raw_sql, normalized_sql, line_number, resolution_status, parse_ok) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        project_id, file_id, occ.source_class, occ.source_method, occ.origin,
                        p.query_type if p else "UNKNOWN", occ.raw_sql,
                        p.normalized_sql if p else None, occ.line_number,
                        occ.resolution_status.value, int(bool(p and p.parse_ok)),
                    ),
                )
                qid = int(cur.lastrowid)
                if not p:
                    continue
                for t in p.tables:
                    cur.execute(
                        "INSERT INTO sql_tables(query_id, name, alias, access) VALUES(?,?,?,?)",
                        (qid, t.name, t.alias, t.access),
                    )
                for c in p.columns:
                    cur.execute(
                        "INSERT INTO sql_columns(query_id, name, table_ref) VALUES(?,?,?)",
                        (qid, c.name, c.table_ref),
                    )
                for j in p.joins:
                    cur.execute(
                        "INSERT INTO sql_joins(query_id, join_type, target, on_expr) VALUES(?,?,?,?)",
                        (qid, j.join_type, j.target, j.on_expr),
                    )
                for cond in p.conditions:
                    cur.execute("INSERT INTO sql_conditions(query_id, expr) VALUES(?,?)", (qid, cond))
                for prm in p.parameters:
                    cur.execute("INSERT INTO sql_parameters(query_id, marker) VALUES(?,?)", (qid, prm))

    # ------------------------------------------------------------------- config
    def write_config(self, project_id: int, file_id: int, entries: list[ConfigEntry]) -> None:
        with transaction(self.conn) as cur:
            for e in entries:
                cur.execute(
                    "INSERT INTO config_entries(project_id, file_id, key, value, is_secret) VALUES(?,?,?,?,?)",
                    (project_id, file_id, e.key, e.value, int(e.is_secret)),
                )

    # ----------------------------------------------------------------- document
    def write_document(self, project_id: int, file_id: int, title: str, content: str) -> None:
        with transaction(self.conn) as cur:
            cur.execute(
                "INSERT INTO documents(project_id, file_id, title, content) VALUES(?,?,?,?)",
                (project_id, file_id, title, content[:200_000]),
            )

    def close(self) -> None:
        self.conn.close()
