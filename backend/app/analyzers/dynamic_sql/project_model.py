"""
backend/app/analyzers/dynamic_sql/project_model.py

Purpose
-------
A lightweight, in-memory cross-file model of a Java project's constants and
methods, used by the dynamic-SQL analyzer for interprocedural resolution.

Responsibility
--------------
- Parse every `.java` file in the project once with tree-sitter and keep the
  parse trees alive (nodes are only valid while their Tree is referenced).
- Index:
    * string constants  ->  ``ClassName.FIELD`` and bare ``FIELD``  -> value + location
    * methods            ->  ``ClassName.method``                    -> body node, params
    * field types        ->  per class, ``fieldName -> TypeName`` (to resolve
                              ``this.metadataService.getX(...)`` receivers)
- Provide lookups the resolvers need without re-reading the DB or re-parsing.

If tree-sitter or the Java grammar is unavailable this model is empty and the
analyzer degrades to "no dynamic SQL detected" (logged), never a crash.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("codexray.dynamic_sql")

_SQL_HINT = ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "WITH ")


def _get_parser():
    from backend.app.analyzers.tree_sitter_setup import get_java_parser
    return get_java_parser()


@dataclass
class ConstInfo:
    qualified: str            # ClassName.FIELD
    value: str
    file: str
    line: int
    looks_like_sql: bool


@dataclass
class MethodInfo:
    qualified: str            # ClassName.method
    class_name: str
    name: str
    params: list[str]         # parameter names in order
    file: str
    body_node: object         # tree-sitter node (kept alive via _trees)
    src: bytes


@dataclass
class ParsedJava:
    path: str
    tree: object
    src: bytes


class JavaProjectModel:
    def __init__(self) -> None:
        self.constants: dict[str, ConstInfo] = {}          # "Class.FIELD"
        self.constants_by_simple: dict[str, list[ConstInfo]] = {}
        self.methods: dict[str, MethodInfo] = {}           # "Class.method"
        self.methods_by_simple: dict[str, list[MethodInfo]] = {}
        self.field_types: dict[str, dict[str, str]] = {}   # class -> {field: type}
        self._trees: list[ParsedJava] = []                 # keep trees referenced

    # ------------------------------------------------------------------ build
    @classmethod
    def build(cls, java_files: list[tuple[str, str]]) -> "JavaProjectModel":
        """java_files: list of (relative_path, absolute_path)."""
        model = cls()
        parser = _get_parser()
        if parser is None:
            log.warning("tree-sitter Java grammar unavailable; dynamic-SQL analysis disabled")
            return model

        for rel_path, abs_path in java_files:
            try:
                src = Path(abs_path).read_bytes()
                tree = parser.parse(src)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("dynamic-SQL: could not parse %s: %s", rel_path, exc)
                continue
            pj = ParsedJava(path=rel_path, tree=tree, src=src)
            model._trees.append(pj)
            model._ingest(pj)
        return model

    # ------------------------------------------------------------- ingestion
    def _text(self, node, src: bytes) -> str:
        return src[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def _string_value(self, node, src: bytes) -> str | None:
        if node is None or node.type != "string_literal":
            return None
        raw = self._text(node, src).strip()
        if raw.startswith('"""'):
            raw = raw[3:-3] if raw.endswith('"""') else raw[3:]
        elif raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]
        # Keep significant leading/trailing spaces (e.g. "SELECT ") — they matter
        # when the constant is concatenated into a larger SQL string.
        return raw.replace('\\"', '"').replace("\\n", " ").replace("\\t", " ").replace("\\r", " ").strip("\n")

    def _ingest(self, pj: ParsedJava) -> None:
        src = pj.src
        for type_node in self._iter_types(pj.tree.root_node):
            name_node = type_node.child_by_field_name("name")
            if name_node is None:
                continue
            class_name = self._text(name_node, src)
            body = type_node.child_by_field_name("body")
            if body is None:
                continue
            self.field_types.setdefault(class_name, {})

            for member in body.children:
                if member.type == "field_declaration":
                    self._ingest_field(member, class_name, pj)
                elif member.type in ("method_declaration", "constructor_declaration"):
                    self._ingest_method(member, class_name, pj)

    def _ingest_field(self, member, class_name: str, pj: ParsedJava) -> None:
        src = pj.src
        type_node = member.child_by_field_name("type")
        type_name = self._text(type_node, src) if type_node else ""
        for decl in member.children:
            if decl.type != "variable_declarator":
                continue
            fname_node = decl.child_by_field_name("name")
            if fname_node is None:
                continue
            fname = self._text(fname_node, src)
            self.field_types[class_name][fname] = type_name

            value_node = decl.child_by_field_name("value")
            val = self._string_value(value_node, src)
            if val is None:
                continue
            info = ConstInfo(
                qualified=f"{class_name}.{fname}",
                value=val,
                file=pj.path,
                line=decl.start_point[0] + 1,
                looks_like_sql=any(val.lstrip().upper().startswith(k) for k in _SQL_HINT),
            )
            self.constants[info.qualified] = info
            self.constants_by_simple.setdefault(fname, []).append(info)

    def _ingest_method(self, member, class_name: str, pj: ParsedJava) -> None:
        src = pj.src
        name_node = member.child_by_field_name("name")
        name = self._text(name_node, src) if name_node else "<init>"
        params: list[str] = []
        params_node = member.child_by_field_name("parameters")
        if params_node is not None:
            for p in params_node.children:
                if p.type in ("formal_parameter", "spread_parameter"):
                    pn = p.child_by_field_name("name")
                    if pn is not None:
                        params.append(self._text(pn, src))
        body = member.child_by_field_name("body")
        if body is None:
            return
        info = MethodInfo(
            qualified=f"{class_name}.{name}",
            class_name=class_name,
            name=name,
            params=params,
            file=pj.path,
            body_node=body,
            src=src,
        )
        self.methods[info.qualified] = info
        self.methods_by_simple.setdefault(name, []).append(info)

    def _iter_types(self, node):
        for child in node.children:
            if child.type in ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration"):
                yield child
            else:
                yield from self._iter_types(child)

    # ------------------------------------------------------------- lookups
    def resolve_constant(self, qualifier: str | None, name: str) -> ConstInfo | None:
        if qualifier and f"{qualifier}.{name}" in self.constants:
            return self.constants[f"{qualifier}.{name}"]
        candidates = self.constants_by_simple.get(name, [])
        return candidates[0] if len(candidates) == 1 else (candidates[0] if candidates else None)

    def resolve_method(self, class_hint: str | None, name: str) -> MethodInfo | None:
        if class_hint and f"{class_hint}.{name}" in self.methods:
            return self.methods[f"{class_hint}.{name}"]
        candidates = self.methods_by_simple.get(name, [])
        return candidates[0] if candidates else None

    def type_of_receiver(self, class_name: str, receiver: str) -> str | None:
        """Best-effort: map a field receiver (`metadataService`) to its type."""
        receiver = receiver.replace("this.", "").strip()
        return self.field_types.get(class_name, {}).get(receiver)
