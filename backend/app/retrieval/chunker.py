"""
backend/app/retrieval/chunker.py

Purpose
-------
Split the indexed project into semantic units for embedding (build plan §35 —
do NOT chunk by fixed character windows).

Responsibility
--------------
- Emit one `Chunk` per class, method, SQL query, dynamic-SQL site, config file
  and documentation section, each carrying metadata (file, symbol, line range,
  chunk_type) so citations/evidence stay precise.
- Read method/class source from disk using the line ranges already in the index.
- Keep each chunk within a sane size (methods are truncated to ~120 lines).

Pure read + transform; the caller embeds and stores the results.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from backend.app.models.database import get_connection

_METHOD_MAX_LINES = 120
_DOC_SECTION_CHARS = 1400


@dataclass
class Chunk:
    project_id: int
    file: str
    chunk_type: str          # class | method | sql | dynamic_sql | config | doc
    symbol: str
    line_start: int | None
    line_end: int | None
    content: str


class Chunker:
    def __init__(self, conn=None) -> None:
        self.conn = conn or get_connection()

    def chunk_project(self, project_id: int) -> list[Chunk]:
        proj = self.conn.execute("SELECT root_path FROM projects WHERE id=?", (project_id,)).fetchone()
        root = Path(proj["root_path"]) if proj else None
        out: list[Chunk] = []
        out += self._classes_and_methods(project_id, root)
        out += self._sql(project_id)
        out += self._dynamic_sql(project_id)
        out += self._config(project_id)
        out += self._docs(project_id)
        return [c for c in out if c.content.strip()]

    # ------------------------------------------------------------------ code
    def _read(self, root: Path | None, rel: str) -> list[str]:
        if root is None:
            return []
        p = root / rel
        try:
            return p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []

    def _classes_and_methods(self, pid: int, root: Path | None) -> list[Chunk]:
        chunks: list[Chunk] = []
        files = {r["id"]: r["path"] for r in self.conn.execute(
            "SELECT id, path FROM files WHERE project_id=?", (pid,))}
        src_cache: dict[str, list[str]] = {}

        for cls in self.conn.execute("SELECT * FROM classes WHERE project_id=?", (pid,)):
            rel = files.get(cls["file_id"], "")
            src = src_cache.setdefault(rel, self._read(root, rel))
            header = ""
            if src and cls["line_start"]:
                header = "\n".join(src[cls["line_start"] - 1: (cls["line_start"] - 1) + 12])
            fields = [dict(r) for r in self.conn.execute(
                "SELECT name, type_name, looks_like_sql FROM fields WHERE class_id=?", (cls["id"],))]
            field_txt = "; ".join(f'{f["type_name"] or ""} {f["name"]}'.strip() for f in fields)
            chunks.append(Chunk(
                pid, rel, "class", cls["fully_qualified_name"] or cls["name"],
                cls["line_start"], cls["line_end"],
                f'{cls["kind"]} {cls["name"]} (package {cls["package"]})\n{header}\nfields: {field_txt}',
            ))

        for m in self.conn.execute(
            "SELECT m.*, c.name AS cname FROM methods m LEFT JOIN classes c ON c.id=m.class_id "
            "WHERE m.project_id=?", (pid,)):
            rel = files.get(m["file_id"], "")
            src = src_cache.setdefault(rel, self._read(root, rel))
            body = ""
            if src and m["line_start"]:
                start = m["line_start"] - 1
                end = min(start + _METHOD_MAX_LINES, m["line_end"] or (start + _METHOD_MAX_LINES), len(src))
                body = "\n".join(src[start:end])
            chunks.append(Chunk(
                pid, rel, "method", f'{m["cname"]}.{m["name"]}',
                m["line_start"], m["line_end"],
                f'{m["cname"]}.{m["name"]}{m["signature"] or ""} -> {m["return_type"] or "void"}\n{body}',
            ))
        return chunks

    # ------------------------------------------------------------------- sql
    def _sql(self, pid: int) -> list[Chunk]:
        rows = self.conn.execute(
            "SELECT q.*, f.path AS fpath FROM sql_queries q JOIN files f ON f.id=q.file_id "
            "WHERE q.project_id=?", (pid,))
        out = []
        for q in rows:
            tables = [r["name"] for r in self.conn.execute(
                "SELECT name FROM sql_tables WHERE query_id=?", (q["id"],))]
            out.append(Chunk(
                pid, q["fpath"], "sql", f'{q["source_class"] or q["fpath"]}#{q["id"]}',
                q["line_number"], q["line_number"],
                f'{q["query_type"]} ({q["resolution_status"]}) tables={",".join(tables)}\n{q["raw_sql"]}',
            ))
        return out

    def _dynamic_sql(self, pid: int) -> list[Chunk]:
        out = []
        for d in self.conn.execute(
            "SELECT ds.*, f.path AS fpath FROM dynamic_sql ds JOIN files f ON f.id=ds.source_file_id "
            "WHERE ds.project_id=?", (pid,)):
            deps = [r["dependency_type"] for r in self.conn.execute(
                "SELECT dependency_type FROM dynamic_sql_dependencies WHERE dynamic_sql_id=?", (d["id"],))]
            out.append(Chunk(
                pid, d["fpath"], "dynamic_sql", f'{d["source_class"]}.{d["source_method"]}',
                d["line_number"], d["line_number"],
                f'dynamic SQL in {d["source_class"]}.{d["source_method"]} [{d["resolution_status"]}] '
                f'deps={",".join(deps)}\ntemplate: {d["sql_template"]}\ntables: {d["tables_json"]}',
            ))
        return out

    # ---------------------------------------------------------------- config
    def _config(self, pid: int) -> list[Chunk]:
        out = []
        by_file: dict[str, list[str]] = {}
        for r in self.conn.execute(
            "SELECT ce.key, ce.value, ce.is_secret, f.path FROM config_entries ce "
            "JOIN files f ON f.id=ce.file_id WHERE ce.project_id=?", (pid,)):
            val = "***" if r["is_secret"] else r["value"]
            by_file.setdefault(r["path"], []).append(f'{r["key"]} = {val}')
        for path, lines in by_file.items():
            out.append(Chunk(pid, path, "config", path, None, None, "\n".join(lines[:200])))
        return out

    # ------------------------------------------------------------------- doc
    def _docs(self, pid: int) -> list[Chunk]:
        out = []
        for r in self.conn.execute(
            "SELECT d.title, d.content, f.path FROM documents d JOIN files f ON f.id=d.file_id "
            "WHERE d.project_id=?", (pid,)):
            text = r["content"] or ""
            for i in range(0, len(text), _DOC_SECTION_CHARS):
                section = text[i:i + _DOC_SECTION_CHARS]
                out.append(Chunk(pid, r["path"], "doc", f'{r["path"]}#{i // _DOC_SECTION_CHARS}',
                                 None, None, section))
        return out
