"""
backend/app/models/records.py

Purpose
-------
Plain in-memory dataclasses that flow between the analyzers and the index writer.

Responsibility
--------------
- Give every parser a stable, typed output shape (no dicts-of-dicts).
- Decouple the SQLite schema from the parser code: writers translate these
  records into rows, so a schema change touches one module, not five.

These types carry *facts extracted from source* only. Resolution state for
dynamic SQL lives here too (RESOLVED / PARTIALLY_RESOLVED / UNRESOLVED) but is
populated by the Sprint 2 analyzer, not by the Sprint 1 parsers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Language(str, Enum):
    JAVA = "java"
    SQL = "sql"
    CONFIG = "config"
    DOC = "doc"
    OTHER = "other"


class ResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    PARTIALLY_RESOLVED = "PARTIALLY_RESOLVED"
    UNRESOLVED = "UNRESOLVED"


# --------------------------------------------------------------------------- #
# Scanner output
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ScannedFile:
    """One file discovered on disk, classified and hashed."""
    rel_path: str
    abs_path: str
    language: Language
    extension: str
    size_bytes: int
    content_hash: str


# --------------------------------------------------------------------------- #
# Java parser output
# --------------------------------------------------------------------------- #
@dataclass
class JavaImport:
    imported: str
    is_static: bool = False
    is_wildcard: bool = False


@dataclass
class JavaField:
    name: str
    type_name: str | None = None
    visibility: str | None = None
    is_static: bool = False
    is_final: bool = False
    string_value: str | None = None       # literal value if statically known
    looks_like_sql: bool = False
    line_start: int | None = None


@dataclass
class JavaCall:
    callee_name: str
    callee_qualifier: str | None = None   # receiver expression / type as written
    arg_count: int | None = None
    line_number: int | None = None
    enclosing_method: str | None = None


@dataclass
class JavaMethod:
    name: str
    signature: str | None = None
    return_type: str | None = None
    parameters: str | None = None
    visibility: str | None = None
    is_static: bool = False
    line_start: int | None = None
    line_end: int | None = None


@dataclass
class JavaClass:
    name: str
    kind: str = "class"                   # class | interface | enum | record
    package: str | None = None
    fully_qualified_name: str | None = None
    visibility: str | None = None
    extends_name: str | None = None
    implements_names: list[str] = field(default_factory=list)
    line_start: int | None = None
    line_end: int | None = None
    methods: list[JavaMethod] = field(default_factory=list)
    fields: list[JavaField] = field(default_factory=list)


@dataclass
class JavaFileParse:
    package: str | None = None
    imports: list[JavaImport] = field(default_factory=list)
    classes: list[JavaClass] = field(default_factory=list)
    calls: list[JavaCall] = field(default_factory=list)
    parser: str = "unknown"               # tree-sitter | regex
    error: str | None = None


# --------------------------------------------------------------------------- #
# SQL parser output
# --------------------------------------------------------------------------- #
@dataclass
class SqlTableRef:
    name: str
    alias: str | None = None
    access: str = "read"                  # read | write


@dataclass
class SqlColumnRef:
    name: str
    table_ref: str | None = None


@dataclass
class SqlJoin:
    join_type: str | None = None
    target: str | None = None
    on_expr: str | None = None


@dataclass
class ParsedSql:
    raw_sql: str
    normalized_sql: str | None = None
    query_type: str = "UNKNOWN"           # SELECT | INSERT | UPDATE | DELETE | MERGE | DDL | UNKNOWN
    tables: list[SqlTableRef] = field(default_factory=list)
    columns: list[SqlColumnRef] = field(default_factory=list)
    joins: list[SqlJoin] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    parameters: list[str] = field(default_factory=list)
    parse_ok: bool = True
    error: str | None = None


@dataclass
class SqlOccurrence:
    """A SQL string found somewhere in the project, plus its parse result."""
    raw_sql: str
    origin: str                           # java_literal | java_constant | sql_file
    line_number: int | None = None
    source_class: str | None = None
    source_method: str | None = None
    resolution_status: ResolutionStatus = ResolutionStatus.RESOLVED
    parsed: ParsedSql | None = None


@dataclass
class ConfigEntry:
    key: str
    value: str | None
    is_secret: bool = False
