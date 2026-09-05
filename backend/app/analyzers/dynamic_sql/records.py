"""
backend/app/analyzers/dynamic_sql/records.py

Purpose
-------
Output records of the dynamic-SQL analyzer, ready for persistence and for the
`trace_dynamic_sql` API/tool response (build plan §21, §28).

Responsibility
--------------
- `DynamicSqlDependency`: one entry in `dynamic_sql_dependencies`
  (CONSTANT / VARIABLE / METHOD_RETURN / METADATA_QUERY / CONFIGURATION /
  PARAMETER / UNKNOWN) with its resolution status and evidence.
- `DynamicSqlRecord`: one SQL construction site — the source method/file, the
  reconstructed template, the resolved SQL (only when fully known), the overall
  status, the extracted tables/columns (from parsing the template), and the
  ordered dependency list.

No behaviour beyond serialisation helpers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.models.records import ResolutionStatus


@dataclass
class DynamicSqlDependency:
    dependency_type: str
    source_type: str | None
    value: str | None
    resolution_status: str
    evidence: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "dependency_type": self.dependency_type,
            "source_type": self.source_type,
            "value": self.value,
            "resolution_status": self.resolution_status,
            "evidence": self.evidence,
        }


@dataclass
class DynamicSqlRecord:
    source_class: str
    source_method: str
    source_var: str
    file: str
    line: int
    expression: str                     # short Java-ish description of the site
    sql_template: str
    resolved_sql: str | None
    resolution_status: ResolutionStatus
    confidence: float
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    dependencies: list[DynamicSqlDependency] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "source_class": self.source_class,
            "source_method": self.source_method,
            "source_var": self.source_var,
            "file": self.file,
            "line": self.line,
            "expression": self.expression,
            "sql_template": self.sql_template,
            "resolved_sql": self.resolved_sql,
            "status": self.resolution_status.value,
            "confidence": self.confidence,
            "tables": self.tables,
            "columns": self.columns,
            "dependencies": [d.as_dict() for d in self.dependencies],
        }
