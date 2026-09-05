"""
backend/app/indexing/sql_extractor.py

Purpose
-------
Locate SQL statements inside the project and attach a parse result to each.

Responsibility
--------------
- `.sql` files: split on `;` into individual statements.
- `.java` files: pull SQL out of string literals — both inline literals and
  `static final String` constants (the latter recorded as origin
  ``java_constant`` so Sprint 2 can resolve `QueryConstants.X` indirection).
- Best-effort association of each Java SQL literal with its enclosing class /
  method using line ranges from the Java parse.
- Hand every candidate to `analyzers.sql.sql_parser.parse_sql`.

Resolution status here is always RESOLVED (the SQL text is fully known). Dynamic
SQL assembled from variables is *not* reconstructed in Sprint 1 — that is the
job of the Sprint 2 dynamic-SQL analyzer.
"""
from __future__ import annotations

import re

from backend.app.analyzers.java.java_parser import looks_like_sql
from backend.app.analyzers.sql.sql_parser import parse_sql
from backend.app.models.records import (
    JavaFileParse,
    ResolutionStatus,
    SqlOccurrence,
)

# Java string literal: normal "..." or text block """...""".
_JAVA_STRING_RE = re.compile(r'"""(?P<block>[\s\S]*?)"""|"(?P<line>(?:[^"\\]|\\.)*)"')


def _unescape(text: str) -> str:
    return text.replace('\\"', '"').replace("\\n", " ").replace("\\t", " ").replace("\\r", " ").strip()


def _split_sql_file(text: str) -> list[tuple[int, str]]:
    """Return (line_number, statement) pairs, ignoring blank / comment-only chunks."""
    out: list[tuple[int, str]] = []
    line = 1
    buf: list[str] = []
    start_line = 1
    for ch in text:
        buf.append(ch)
        if ch == "\n":
            line += 1
        if ch == ";":
            stmt = "".join(buf).strip()
            stmt = re.sub(r"--[^\n]*", "", stmt).strip().rstrip(";").strip()
            if stmt:
                out.append((start_line, stmt))
            buf = []
            start_line = line
    tail = re.sub(r"--[^\n]*", "", "".join(buf)).strip()
    if tail:
        out.append((start_line, tail))
    return out


def extract_from_sql_file(text: str) -> list[SqlOccurrence]:
    occurrences: list[SqlOccurrence] = []
    for line_no, stmt in _split_sql_file(text):
        occurrences.append(
            SqlOccurrence(
                raw_sql=stmt,
                origin="sql_file",
                line_number=line_no,
                resolution_status=ResolutionStatus.RESOLVED,
                parsed=parse_sql(stmt),
            )
        )
    return occurrences


def _enclosing(java: JavaFileParse, line: int) -> tuple[str | None, str | None]:
    cls_name = mth_name = None
    for c in java.classes:
        if c.line_start and c.line_start <= line and (c.line_end or 10**9) >= line:
            cls_name = c.name
            for m in c.methods:
                if m.line_start and m.line_start <= line and (m.line_end or (m.line_start + 200)) >= line:
                    mth_name = m.name
    return cls_name, mth_name


def extract_from_java_file(source: str, java: JavaFileParse) -> list[SqlOccurrence]:
    occurrences: list[SqlOccurrence] = []

    # 1) String constants already identified by the Java parser.
    const_values: set[str] = set()
    for c in java.classes:
        for f in c.fields:
            if f.looks_like_sql and f.string_value:
                const_values.add(f.string_value)
                occurrences.append(
                    SqlOccurrence(
                        raw_sql=f.string_value,
                        origin="java_constant",
                        line_number=f.line_start,
                        source_class=c.name,
                        source_method=None,
                        resolution_status=ResolutionStatus.RESOLVED,
                        parsed=parse_sql(f.string_value),
                    )
                )

    # 2) Any other inline literal that looks like SQL.
    for m in _JAVA_STRING_RE.finditer(source):
        raw = _unescape(m.group("block") if m.group("block") is not None else m.group("line") or "")
        if not looks_like_sql(raw) or raw in const_values:
            continue
        line_no = source.count("\n", 0, m.start()) + 1
        cls_name, mth_name = _enclosing(java, line_no)
        occurrences.append(
            SqlOccurrence(
                raw_sql=raw,
                origin="java_literal",
                line_number=line_no,
                source_class=cls_name,
                source_method=mth_name,
                resolution_status=ResolutionStatus.RESOLVED,
                parsed=parse_sql(raw),
            )
        )
    return occurrences
