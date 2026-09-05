"""
backend/app/analyzers/dynamic_sql/sql_template_resolver.py

Purpose
-------
Turn a reconstructed `StringExpr` into a `DynamicSqlRecord`: the SQL template,
the resolved SQL (iff fully known), tables/columns, an overall status and a
confidence score (build plan §15, §21, §29).

Responsibility
--------------
- Render `template()` and `resolved_sql()` from the expression.
- Parse whichever of the two is available to pull out table/column names
  (placeholders like ``{table}`` are ignored by the SQL parser, so
  PARTIALLY_RESOLVED templates still yield the *static* tables).
- Compute a confidence: 1.0 when RESOLVED, a fraction of resolved parts when
  PARTIALLY_RESOLVED, 0.0 when UNRESOLVED.
- NEVER promote status: an unresolved slot keeps the record PARTIALLY_RESOLVED
  and the resolved_sql stays ``None`` (§29 — no invented tables).
"""
from __future__ import annotations

from backend.app.analyzers.dynamic_sql.expression import PartKind, StringExpr
from backend.app.analyzers.dynamic_sql.records import DynamicSqlDependency, DynamicSqlRecord
from backend.app.analyzers.dynamic_sql.expression import DEPENDENCY_TYPE
from backend.app.analyzers.sql.sql_parser import parse_sql
from backend.app.models.records import ResolutionStatus


class SQLTemplateResolver:
    def build_record(
        self,
        *,
        source_class: str,
        source_method: str,
        source_var: str,
        file: str,
        line: int,
        expr: StringExpr,
    ) -> DynamicSqlRecord:
        status = expr.status()
        template = expr.template()
        resolved = expr.resolved_sql()

        parse_target = resolved or template
        parsed = parse_sql(parse_target) if parse_target else None
        # Drop identifiers that are actually unresolved-slot placeholders
        # (e.g. the parser reads "{table}" as the identifier "table").
        placeholders = {p.label.lower() for p in expr.dynamic_parts}
        placeholders |= {p.slot.strip("{}").lower() for p in expr.dynamic_parts}
        tables = sorted({t.name for t in parsed.tables if t.name.lower() not in placeholders}) if parsed else []
        columns = sorted({c.name for c in parsed.columns if c.name.lower() not in placeholders}) if parsed else []

        total = max(len(expr.parts), 1)
        resolved_count = sum(1 for p in expr.parts if p.status is ResolutionStatus.RESOLVED)
        if status is ResolutionStatus.RESOLVED:
            confidence = 1.0
        elif status is ResolutionStatus.PARTIALLY_RESOLVED:
            confidence = round(0.4 + 0.5 * resolved_count / total, 2)
        else:
            confidence = 0.0

        deps: list[DynamicSqlDependency] = []
        for p in expr.parts:
            if p.kind is PartKind.LITERAL:
                continue
            deps.append(DynamicSqlDependency(
                dependency_type=DEPENDENCY_TYPE[p.kind],
                source_type=p.kind.value,
                value=p.text if p.status is ResolutionStatus.RESOLVED else p.label,
                resolution_status=p.status.value,
                evidence=[e.as_dict() for e in p.evidence],
            ))

        expression_desc = f'{source_var} = ' + " + ".join(
            (repr(p.text) if p.kind is PartKind.LITERAL and p.text else f"<{p.kind.value}:{p.label}>")
            for p in expr.parts
        )

        return DynamicSqlRecord(
            source_class=source_class,
            source_method=source_method,
            source_var=source_var,
            file=file,
            line=line,
            expression=expression_desc[:1000],
            sql_template=template,
            resolved_sql=resolved,
            resolution_status=status,
            confidence=confidence,
            tables=tables,
            columns=columns,
            dependencies=deps,
        )
