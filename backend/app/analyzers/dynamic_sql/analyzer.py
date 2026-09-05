"""
backend/app/analyzers/dynamic_sql/analyzer.py

Purpose
-------
Top-level driver for Sprint 2: find every dynamic-SQL construction site in a
project and produce fully-evidenced `DynamicSqlRecord`s (build plan §56, §83).

Responsibility
--------------
- Build the cross-file `JavaProjectModel` (constants + methods + field types).
- For each method, run statement-level analysis to find SQL construction sites
  (local vars / return values whose reconstructed expression looks like SQL and
  has at least one dynamic part).
- For each site, use `SQLTemplateResolver` to render the template, the resolved
  SQL (only if fully known), the tables/columns and the dependency chain.
- De-duplicate: if an inner builder site is fully subsumed by a richer
  interprocedural site in a caller, keep both but they are distinguishable by
  (class, method, var).

`analyze_query_or_method(selector)` backs the `trace_dynamic_sql` tool: given a
class, `Class.method`, table name or raw SQL fragment, return the matching
records.
"""
from __future__ import annotations

import logging

from backend.app.analyzers.dynamic_sql.evaluator import EvalContext, ExpressionEvaluator
from backend.app.analyzers.dynamic_sql.project_model import JavaProjectModel
from backend.app.analyzers.dynamic_sql.records import DynamicSqlRecord
from backend.app.analyzers.dynamic_sql.sql_template_resolver import SQLTemplateResolver
from backend.app.config.settings import get_settings

log = logging.getLogger("codexray.dynamic_sql")


class DynamicSqlAnalyzer:
    def __init__(self, max_depth: int | None = None) -> None:
        self.max_depth = max_depth if max_depth is not None else get_settings().dynamic_sql.max_depth
        self.templater = SQLTemplateResolver()

    # ------------------------------------------------------------------ build
    def analyze_project(self, java_files: list[tuple[str, str]]) -> list[DynamicSqlRecord]:
        """java_files: list of (relative_path, absolute_path)."""
        model = JavaProjectModel.build(java_files)
        if not model.methods:
            return []
        evaluator = ExpressionEvaluator(model, self.max_depth)
        records: list[DynamicSqlRecord] = []
        seen: set[tuple[str, str, str]] = set()

        for qualified, method in model.methods.items():
            try:
                ctx = EvalContext(class_name=method.class_name, src=method.src, params=set(method.params))
                sites = evaluator.analyze_method_body(method, ctx)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("dynamic-SQL analysis failed for %s: %s", qualified, exc)
                continue

            for site in sites:
                if not site.expr.looks_like_sql() or not site.expr.dynamic_parts:
                    continue
                key = (method.class_name, method.name, site.name)
                if key in seen:
                    continue
                seen.add(key)
                records.append(self.templater.build_record(
                    source_class=method.class_name,
                    source_method=method.name,
                    source_var=site.name,
                    file=method.file,
                    line=site.line,
                    expr=site.expr,
                ))
        return records

    # --------------------------------------------------------------- tracing
    def trace(self, records: list[DynamicSqlRecord], selector: str) -> list[DynamicSqlRecord]:
        sel = selector.strip().lower()
        hits: list[DynamicSqlRecord] = []
        for r in records:
            haystack = " ".join([
                r.source_class, r.source_method, f"{r.source_class}.{r.source_method}",
                r.sql_template, r.resolved_sql or "", " ".join(r.tables), " ".join(r.columns),
            ]).lower()
            if sel in haystack:
                hits.append(r)
        return hits
