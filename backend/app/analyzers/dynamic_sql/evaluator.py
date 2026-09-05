"""
backend/app/analyzers/dynamic_sql/evaluator.py

Purpose
-------
The recursive engine that evaluates a Java expression node into a `StringExpr`
(the reconstructed-SQL IR). This is the shared core behind every named resolver
in `resolvers.py`.

Responsibility
--------------
- `ExpressionEvaluator.eval(node, ctx)` handles: string literals, `+`
  concatenation, parenthesised expressions, identifiers (local var / parameter /
  constant), `Class.CONST` field access, `String.join(...)`, `StringBuilder`
  `.toString()`, and arbitrary method calls.
- Method calls are routed:
    1. metadata query?  -> a METADATA_QUERY part (PARTIALLY_RESOLVED) with the
       metadata SQL + tables as evidence.
    2. in-project method within depth budget?  -> inline the callee's returned
       expression, binding its parameters to the call-site argument expressions
       (this is what makes the §83 end-to-end trace work).
    3. otherwise -> a METHOD_RETURN part (PARTIALLY_RESOLVED).
- `analyze_method_body(method, ...)` does statement-level tracking (local vars,
  assignments, `StringBuilder.append(...)`, `return`) and returns the SQL
  construction *sites* found in that method.

No guessing: an unresolved argument stays a placeholder in the template.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.analyzers.dynamic_sql.expression import (
    Evidence,
    Part,
    PartKind,
    StringExpr,
    literal,
)
from backend.app.analyzers.metadata.metadata_detector import MetadataQueryDetector
from backend.app.models.records import ResolutionStatus


@dataclass
class EvalContext:
    class_name: str
    src: bytes
    params: set[str] = field(default_factory=set)
    scope: dict[str, StringExpr] = field(default_factory=dict)   # local var -> expr
    sb_vars: set[str] = field(default_factory=set)               # StringBuilder locals
    depth: int = 0
    visited: frozenset[str] = frozenset()                        # method keys being inlined


@dataclass
class Site:
    name: str                 # local var name or "<return>"
    expr: StringExpr
    line: int


class ExpressionEvaluator:
    def __init__(self, model, max_depth: int) -> None:
        self.model = model
        self.max_depth = max_depth
        self.metadata = MetadataQueryDetector(model)

    # ------------------------------------------------------------------ utils
    def _text(self, node, src: bytes) -> str:
        return src[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def _string_value(self, node, src: bytes) -> str:
        raw = self._text(node, src).strip()
        if raw.startswith('"""'):
            raw = raw[3:-3] if raw.endswith('"""') else raw[3:]
        elif raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]
        return raw.replace('\\"', '"').replace("\\n", " ").replace("\\t", " ").replace("\\r", " ")

    # --------------------------------------------------------------- eval core
    def eval(self, node, ctx: EvalContext) -> StringExpr:
        if node is None:
            return StringExpr([Part(PartKind.UNKNOWN, label="null")])

        t = node.type
        if t == "string_literal":
            return StringExpr([literal(self._string_value(node, ctx.src))])

        if t in ("parenthesized_expression",):
            inner = node.child_by_field_name("value") or (node.named_children[0] if node.named_children else None)
            return self.eval(inner, ctx)

        if t == "binary_expression":
            op = node.child_by_field_name("operator")
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if op is not None and self._text(op, ctx.src).strip() == "+":
                return StringExpr().extend(self.eval(left, ctx)).extend(self.eval(right, ctx))
            return StringExpr([Part(PartKind.UNKNOWN, label="expr")])

        if t == "identifier":
            return self._eval_identifier(self._text(node, ctx.src), ctx, node)

        if t == "field_access":
            return self._eval_field_access(node, ctx)

        if t == "method_invocation":
            return self._eval_call(node, ctx)

        if t in ("cast_expression",):
            return self.eval(node.child_by_field_name("value"), ctx)

        if t in ("character_literal", "decimal_integer_literal", "true", "false", "null_literal"):
            return StringExpr([Part(PartKind.UNKNOWN, label="literal")])

        return StringExpr([Part(PartKind.UNKNOWN, label=t)])

    # ------------------------------------------------------------- identifiers
    def _eval_identifier(self, name: str, ctx: EvalContext, node) -> StringExpr:
        if name in ctx.scope:
            # return a copy so callers don't mutate the stored expr
            return StringExpr(list(ctx.scope[name].parts))
        if name in ctx.params:
            return StringExpr([Part(
                PartKind.PARAMETER, label=name, status=ResolutionStatus.UNRESOLVED,
                evidence=[Evidence(detail=f"bound to method parameter '{name}' (runtime value)")],
            )])
        info = self.model.resolve_constant(None, name)
        if info is not None:
            return StringExpr([Part(
                PartKind.CONSTANT, text=info.value, label=name,
                status=ResolutionStatus.RESOLVED,
                evidence=[Evidence(detail=f"constant {info.qualified}", file=info.file, line=info.line)],
            )])
        return StringExpr([Part(PartKind.VARIABLE, label=name, status=ResolutionStatus.UNRESOLVED,
                                evidence=[Evidence(detail=f"unresolved local/field '{name}'")])])

    def _eval_field_access(self, node, ctx: EvalContext) -> StringExpr:
        obj = node.child_by_field_name("object")
        fld = node.child_by_field_name("field")
        qual = self._text(obj, ctx.src) if obj is not None else None
        name = self._text(fld, ctx.src) if fld is not None else self._text(node, ctx.src)
        info = self.model.resolve_constant(qual, name)
        if info is not None:
            return StringExpr([Part(
                PartKind.CONSTANT, text=info.value, label=name,
                status=ResolutionStatus.RESOLVED,
                evidence=[Evidence(detail=f"constant {info.qualified}", file=info.file, line=info.line)],
            )])
        return StringExpr([Part(PartKind.UNKNOWN, label=name,
                                evidence=[Evidence(detail=f"unresolved field access {qual}.{name}")])])

    # ------------------------------------------------------------------ calls
    def _eval_call(self, node, ctx: EvalContext) -> StringExpr:
        name_node = node.child_by_field_name("name")
        obj_node = node.child_by_field_name("object")
        args_node = node.child_by_field_name("arguments")
        call_name = self._text(name_node, ctx.src) if name_node else "?"
        obj_text = self._text(obj_node, ctx.src) if obj_node is not None else None
        arg_nodes = [c for c in (args_node.children if args_node else []) if c.is_named]

        # StringBuilder.toString() -> the accumulated expression for that var
        if call_name == "toString" and obj_text in ctx.scope:
            return StringExpr(list(ctx.scope[obj_text].parts))

        # String.join(sep, collection) -> evaluate the collection argument
        if call_name == "join" and obj_text == "String" and len(arg_nodes) >= 2:
            return self.eval(arg_nodes[-1], ctx)

        # String.format(fmt, ...) -> keep the format string skeleton
        if call_name == "format" and arg_nodes:
            return self.eval(arg_nodes[0], ctx)

        # Resolve the callee method within the project.
        class_hint = None
        if obj_text:
            class_hint = self.model.type_of_receiver(ctx.class_name, obj_text) or obj_text
        callee = self.model.resolve_method(class_hint, call_name)

        # 1) metadata query?
        if callee is not None:
            lookup = self.metadata.detect(callee)
            if lookup is not None:
                label = {"TABLE": "table", "COLUMN": "columns"}.get(lookup.result_kind, "metadata_value")
                return StringExpr([Part(
                    PartKind.METADATA_QUERY, label=label,
                    status=ResolutionStatus.PARTIALLY_RESOLVED,
                    evidence=[Evidence(
                        detail=(f"{lookup.method_qualified}() resolves {label} by querying "
                                f"metadata table(s) {', '.join(lookup.metadata_tables) or '?'}"),
                        file=lookup.file, line=lookup.line,
                        metadata_sql=lookup.metadata_sql,
                        metadata_tables=lookup.metadata_tables,
                    )],
                )])

        # 2) in-project method within depth budget -> inline its return expression
        if (
            callee is not None
            and ctx.depth < self.max_depth
            and callee.qualified not in ctx.visited
        ):
            arg_exprs = [self.eval(a, ctx) for a in arg_nodes]
            inlined = self._inline_return(callee, arg_exprs, ctx)
            if inlined is not None and inlined.parts:
                return inlined

        # 3) opaque method return
        return StringExpr([Part(
            PartKind.METHOD_RETURN, label=call_name,
            status=ResolutionStatus.PARTIALLY_RESOLVED,
            evidence=[Evidence(
                detail=f"value returned by {class_hint + '.' if class_hint else ''}{call_name}(...)",
            )],
        )])

    def _inline_return(self, callee, arg_exprs: list[StringExpr], ctx: EvalContext) -> StringExpr | None:
        child_scope: dict[str, StringExpr] = {}
        for pname, pexpr in zip(callee.params, arg_exprs):
            child_scope[pname] = pexpr
        child_ctx = EvalContext(
            class_name=callee.class_name,
            src=callee.src,
            params=set(callee.params),
            scope=child_scope,
            depth=ctx.depth + 1,
            visited=ctx.visited | {callee.qualified},
        )
        sites = self.analyze_method_body(callee, child_ctx, only_returns=True)
        if not sites:
            return None
        # prefer the last return statement
        return sites[-1].expr

    # ----------------------------------------------------- statement tracking
    def analyze_method_body(self, method, ctx: EvalContext | None = None, only_returns: bool = False) -> list[Site]:
        if ctx is None:
            ctx = EvalContext(class_name=method.class_name, src=method.src, params=set(method.params))
        src = ctx.src
        sites: list[Site] = []

        def line_of(node) -> int:
            return node.start_point[0] + 1

        def handle_local_decl(node) -> None:
            type_node = node.child_by_field_name("type")
            type_txt = self._text(type_node, src) if type_node else ""
            for decl in node.children:
                if decl.type != "variable_declarator":
                    continue
                nn = decl.child_by_field_name("name")
                vn = decl.child_by_field_name("value")
                if nn is None:
                    continue
                vname = self._text(nn, src)
                if vn is not None and vn.type == "object_creation_expression" and "StringBuilder" in type_txt:
                    ctx.scope[vname] = StringExpr()
                    ctx.sb_vars.add(vname)
                elif vn is not None and "StringBuilder" in type_txt and vn.type == "method_invocation":
                    ctx.scope[vname] = StringExpr()
                    ctx.sb_vars.add(vname)
                else:
                    ctx.scope[vname] = self.eval(vn, ctx)

        def handle_assignment(node) -> None:
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            op = node.child_by_field_name("operator")
            if left is None or left.type != "identifier":
                return
            lname = self._text(left, src)
            op_txt = self._text(op, src).strip() if op is not None else "="
            rexpr = self.eval(right, ctx)
            if op_txt == "+=" and lname in ctx.scope:
                ctx.scope[lname].extend(rexpr)
            else:
                ctx.scope[lname] = rexpr

        def handle_call_stmt(node) -> None:
            # sb.append(x)  /  sb.append(x).append(y)
            inv = node
            chain: list = []
            while inv is not None and inv.type == "method_invocation":
                nm_node = inv.child_by_field_name("name")
                if nm_node is not None and self._text(nm_node, src) == "append":
                    chain.append(inv)
                inv = inv.child_by_field_name("object")
            if not chain:
                return
            # base receiver identifier
            base = node
            while base.type == "method_invocation":
                o = base.child_by_field_name("object")
                if o is None:
                    break
                base = o
            recv = self._text(base, src)
            if recv not in ctx.sb_vars:
                return
            for inv in reversed(chain):
                args = inv.child_by_field_name("arguments")
                for a in (args.children if args else []):
                    if a.is_named:
                        ctx.scope[recv].extend(self.eval(a, ctx))

        def walk(node) -> None:
            for child in node.children:
                ct = child.type
                if ct == "local_variable_declaration":
                    handle_local_decl(child)
                elif ct == "expression_statement" and child.named_children:
                    inner = child.named_children[0]
                    if inner.type == "assignment_expression":
                        handle_assignment(inner)
                    elif inner.type == "method_invocation":
                        handle_call_stmt(inner)
                elif ct == "return_statement":
                    expr_node = child.named_children[0] if child.named_children else None
                    sites.append(Site("<return>", self.eval(expr_node, ctx), line_of(child)))
                # recurse into blocks / if / try / for so nested returns are seen
                if ct in ("block", "if_statement", "try_statement", "for_statement",
                          "while_statement", "enhanced_for_statement", "switch_expression",
                          "switch_block", "catch_clause", "expression_statement"):
                    walk(child)

        walk(method.body_node)

        if only_returns:
            return [s for s in sites if s.name == "<return>"]

        # also surface local String vars that ended up holding dynamic SQL
        for vname, expr in ctx.scope.items():
            if vname in ctx.sb_vars:
                continue
            if expr.looks_like_sql() and expr.dynamic_parts:
                sites.append(Site(vname, expr, line_of(method.body_node)))
        return sites
