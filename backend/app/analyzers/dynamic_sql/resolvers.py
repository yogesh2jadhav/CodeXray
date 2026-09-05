"""
backend/app/analyzers/dynamic_sql/resolvers.py

Purpose
-------
Named resolver facades listed in the build plan (§56). Each is a thin, documented
wrapper over `ExpressionEvaluator` / `SQLTemplateResolver` so the pipeline stages
are individually discoverable and unit-testable. The heavy lifting lives in
`evaluator.py`; this module keeps the public vocabulary the plan asks for.

Stages
------
- ConstantResolver           — `Class.CONST` / bare `CONST` -> literal value
- VariableResolver           — local variable -> its tracked `StringExpr`
- MethodReturnResolver       — call -> inlined callee return, bounded by max_depth
- StringConcatenationResolver— `a + b + c` -> ordered parts
- StringBuilderResolver      — `new StringBuilder().append(x).append(y)` -> parts
- SQLTemplateResolver        — parts -> template + status + tables (in its own module)
- DynamicSQLDependencyBuilder— parts -> `dynamic_sql_dependencies` rows

All resolvers are deterministic and never invent a value.
"""
from __future__ import annotations

from backend.app.analyzers.dynamic_sql.evaluator import EvalContext, ExpressionEvaluator
from backend.app.analyzers.dynamic_sql.expression import DEPENDENCY_TYPE, PartKind, StringExpr
from backend.app.analyzers.dynamic_sql.records import DynamicSqlDependency
from backend.app.analyzers.dynamic_sql.sql_template_resolver import SQLTemplateResolver  # re-export
from backend.app.models.records import ResolutionStatus

__all__ = [
    "ConstantResolver", "VariableResolver", "MethodReturnResolver",
    "StringConcatenationResolver", "StringBuilderResolver",
    "SQLTemplateResolver", "DynamicSQLDependencyBuilder",
]


class _EvaluatorBacked:
    def __init__(self, model, max_depth: int = 5) -> None:
        self.evaluator = ExpressionEvaluator(model, max_depth)

    def _ctx(self, class_name: str, src: bytes, params: set[str] | None = None) -> EvalContext:
        return EvalContext(class_name=class_name, src=src, params=params or set())


class ConstantResolver(_EvaluatorBacked):
    """Resolve a constant reference node to its string value (Pattern A/B)."""

    def resolve(self, node, class_name: str, src: bytes) -> StringExpr:
        return self.evaluator.eval(node, self._ctx(class_name, src))


class VariableResolver(_EvaluatorBacked):
    """Resolve an identifier against a scope of tracked local variables (Pattern E)."""

    def resolve(self, node, ctx: EvalContext) -> StringExpr:
        return self.evaluator.eval(node, ctx)


class MethodReturnResolver(_EvaluatorBacked):
    """Resolve a method call by inlining the callee's return expression (Patterns H/I)."""

    def resolve(self, call_node, ctx: EvalContext) -> StringExpr:
        return self.evaluator.eval(call_node, ctx)


class StringConcatenationResolver(_EvaluatorBacked):
    """Resolve `a + b + c` into ordered parts (Pattern F)."""

    def resolve(self, binary_node, ctx: EvalContext) -> StringExpr:
        return self.evaluator.eval(binary_node, ctx)


class StringBuilderResolver(_EvaluatorBacked):
    """Reconstruct a StringBuilder append-chain within a method body (Pattern G)."""

    def resolve_method(self, method) -> list:
        return self.evaluator.analyze_method_body(method)


class DynamicSQLDependencyBuilder:
    """Translate the parts of a reconstructed expression into dependency rows (§21)."""

    def build(self, expr: StringExpr) -> list[DynamicSqlDependency]:
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
        return deps
