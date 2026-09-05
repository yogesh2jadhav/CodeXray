"""
backend/app/analyzers/java/java_parser.py

Purpose
-------
Extract structural facts from a single Java source file.

Responsibility
--------------
- Primary path: tree-sitter AST walk (build plan §17 recommends AST over regex).
- Fallback path: a conservative regex scanner used only when tree-sitter or the
  Java grammar is unavailable, so indexing still works on a bare machine.
- Produce a `JavaFileParse`: package, imports, classes, methods, fields
  (including string constants that *look like SQL* — the hook Sprint 2 uses to
  follow `QueryConstants.GET_CUSTOMER` style indirection), and statically
  identifiable method calls with their enclosing method.

Non-goals
---------
No type resolution, no cross-file linking, no SQL parsing. This module only
reports what is written in one file. Deterministic facts only — never guesses.
"""
from __future__ import annotations

import re

from backend.app.models.records import (
    JavaCall,
    JavaClass,
    JavaField,
    JavaFileParse,
    JavaImport,
    JavaMethod,
)

# A string literal is treated as SQL if, once trimmed, it starts with a DML/DDL
# keyword. Kept deliberately strict to avoid false positives on ordinary text.
_SQL_HINT_RE = re.compile(
    r"^\s*\(?\s*(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO|WITH)\b",
    re.IGNORECASE,
)
_VISIBILITY = ("public", "private", "protected")


def looks_like_sql(text: str | None) -> bool:
    return bool(text) and bool(_SQL_HINT_RE.match(text))


# --------------------------------------------------------------------------- #
# tree-sitter backend
# --------------------------------------------------------------------------- #
def _get_ts_parser():
    """Return a configured tree-sitter Java parser, or None if unavailable."""
    try:
        from tree_sitter_languages import get_parser
        return get_parser("java")
    except Exception:
        return None


class _TreeSitterJava:
    """Walks a tree-sitter parse tree into a JavaFileParse."""

    def __init__(self, source: bytes) -> None:
        self.src = source

    def _text(self, node) -> str:
        return self.src[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def _line(self, node) -> int:
        return node.start_point[0] + 1

    def _child_by_field(self, node, field: str):
        return node.child_by_field_name(field)

    def _modifiers(self, node) -> tuple[str | None, bool, bool]:
        """(visibility, is_static, is_final) from a node's 'modifiers' child."""
        vis, is_static, is_final = None, False, False
        for child in node.children:
            if child.type == "modifiers":
                mods = self._text(child).split()
                for m in mods:
                    if m in _VISIBILITY:
                        vis = m
                    elif m == "static":
                        is_static = True
                    elif m == "final":
                        is_final = True
        return vis, is_static, is_final

    def parse(self) -> JavaFileParse:
        parser = _get_ts_parser()
        tree = parser.parse(self.src)
        root = tree.root_node
        result = JavaFileParse(parser="tree-sitter")

        for node in root.children:
            if node.type == "package_declaration":
                result.package = self._text(node).strip().removeprefix("package").strip().rstrip(";").strip()
            elif node.type == "import_declaration":
                raw = self._text(node)
                result.imports.append(
                    JavaImport(
                        imported=raw.replace("import", "", 1).replace("static", "", 1).strip().rstrip(";").strip(),
                        is_static="static" in raw.split("import", 1)[1][:10] if "import" in raw else False,
                        is_wildcard=raw.rstrip(";").strip().endswith(".*"),
                    )
                )

        for type_node in self._iter_type_decls(root):
            result.classes.append(self._parse_type(type_node, result.package))

        result.calls = self._collect_calls(root)
        return result

    def _iter_type_decls(self, node):
        for child in node.children:
            if child.type in ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration"):
                yield child
            else:
                yield from self._iter_type_decls(child)

    def _parse_type(self, node, package: str | None) -> JavaClass:
        kind = {
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
            "record_declaration": "record",
        }.get(node.type, "class")
        name_node = self._child_by_field(node, "name")
        name = self._text(name_node) if name_node else "<anonymous>"
        vis, _, _ = self._modifiers(node)

        extends_name = None
        implements_names: list[str] = []
        superclass = self._child_by_field(node, "superclass")
        if superclass:
            extends_name = self._text(superclass).replace("extends", "").strip()
        interfaces = self._child_by_field(node, "interfaces")
        if interfaces:
            implements_names = [
                t.strip() for t in self._text(interfaces).replace("implements", "").split(",") if t.strip()
            ]

        jclass = JavaClass(
            name=name,
            kind=kind,
            package=package,
            fully_qualified_name=f"{package}.{name}" if package else name,
            visibility=vis,
            extends_name=extends_name,
            implements_names=implements_names,
            line_start=self._line(node),
            line_end=node.end_point[0] + 1,
        )

        body = self._child_by_field(node, "body")
        if body:
            for member in body.children:
                if member.type == "method_declaration":
                    jclass.methods.append(self._parse_method(member))
                elif member.type == "constructor_declaration":
                    m = self._parse_method(member)
                    m.return_type = None
                    jclass.methods.append(m)
                elif member.type == "field_declaration":
                    jclass.fields.extend(self._parse_field(member))
        return jclass

    def _parse_method(self, node) -> JavaMethod:
        name_node = self._child_by_field(node, "name")
        name = self._text(name_node) if name_node else "<init>"
        vis, is_static, _ = self._modifiers(node)
        ret = self._child_by_field(node, "type")
        params_node = self._child_by_field(node, "parameters")
        params = self._text(params_node).strip("()") if params_node else ""
        return JavaMethod(
            name=name,
            signature=f"{name}({params})",
            return_type=self._text(ret) if ret else None,
            parameters=params or None,
            visibility=vis,
            is_static=is_static,
            line_start=self._line(node),
            line_end=node.end_point[0] + 1,
        )

    def _parse_field(self, node) -> list[JavaField]:
        vis, is_static, is_final = self._modifiers(node)
        type_node = self._child_by_field(node, "type")
        type_name = self._text(type_node) if type_node else None
        out: list[JavaField] = []
        for decl in node.children:
            if decl.type != "variable_declarator":
                continue
            name_node = self._child_by_field(decl, "name")
            value_node = self._child_by_field(decl, "value")
            literal = None
            if value_node is not None and value_node.type == "string_literal":
                literal = self._string_literal_text(value_node)
            out.append(
                JavaField(
                    name=self._text(name_node) if name_node else "<field>",
                    type_name=type_name,
                    visibility=vis,
                    is_static=is_static,
                    is_final=is_final,
                    string_value=literal,
                    looks_like_sql=looks_like_sql(literal),
                    line_start=self._line(decl),
                )
            )
        return out

    def _string_literal_text(self, node) -> str:
        raw = self._text(node)
        # strip surrounding quotes / text-block markers, unescape common sequences
        raw = raw.strip()
        if raw.startswith('"""'):
            raw = raw[3:-3] if raw.endswith('"""') else raw[3:]
        elif raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]
        return raw.replace('\\"', '"').replace("\\n", " ").replace("\\t", " ").strip()

    def _collect_calls(self, root) -> list[JavaCall]:
        calls: list[JavaCall] = []

        def walk(node, enclosing: str | None):
            if node.type == "method_declaration":
                nn = self._child_by_field(node, "name")
                enclosing = self._text(nn) if nn else enclosing
            if node.type == "method_invocation":
                name_node = self._child_by_field(node, "name")
                obj_node = self._child_by_field(node, "object")
                args_node = self._child_by_field(node, "arguments")
                arg_count = 0
                if args_node is not None:
                    arg_count = sum(1 for c in args_node.children if c.is_named)
                calls.append(
                    JavaCall(
                        callee_name=self._text(name_node) if name_node else "?",
                        callee_qualifier=self._text(obj_node) if obj_node else None,
                        arg_count=arg_count,
                        line_number=self._line(node),
                        enclosing_method=enclosing,
                    )
                )
            for child in node.children:
                walk(child, enclosing)

        walk(root, None)
        return calls


# --------------------------------------------------------------------------- #
# regex fallback backend
# --------------------------------------------------------------------------- #
_PKG_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_IMPORT_RE = re.compile(r"^\s*import\s+(static\s+)?([\w.*]+)\s*;", re.MULTILINE)
_TYPE_RE = re.compile(
    r"(?P<vis>public|private|protected)?\s*(?:abstract\s+|final\s+|static\s+)*"
    r"(?P<kind>class|interface|enum|record)\s+(?P<name>\w+)"
    r"(?:\s+extends\s+(?P<extends>[\w.<>]+))?"
    r"(?:\s+implements\s+(?P<impl>[\w.,<>\s]+?))?\s*\{",
)
_METHOD_RE = re.compile(
    r"(?P<vis>public|private|protected)?\s*(?P<static>static\s+)?"
    r"(?:final\s+|synchronized\s+|abstract\s+|native\s+)*"
    r"(?P<ret>[\w.<>\[\]]+)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*(?:throws [\w.,\s]+)?\s*[{;]",
)
_FIELD_RE = re.compile(
    r"(?P<vis>public|private|protected)?\s*(?P<static>static\s+)?(?P<final>final\s+)?"
    r"(?P<type>String)\s+(?P<name>\w+)\s*=\s*(?P<value>\"(?:[^\"\\]|\\.)*\"|\"\"\"[\s\S]*?\"\"\")\s*;",
)
_CALL_RE = re.compile(r"(?:(?P<qual>[\w.]+)\.)?(?P<name>\w+)\s*\(")
_JAVA_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "new", "super", "this"}


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


class _RegexJava:
    def __init__(self, source: str) -> None:
        self.src = source

    def parse(self) -> JavaFileParse:
        src = self.src
        result = JavaFileParse(parser="regex")

        m = _PKG_RE.search(src)
        result.package = m.group(1) if m else None

        for im in _IMPORT_RE.finditer(src):
            name = im.group(2)
            result.imports.append(
                JavaImport(imported=name, is_static=bool(im.group(1)), is_wildcard=name.endswith(".*"))
            )

        for tm in _TYPE_RE.finditer(src):
            name = tm.group("name")
            impl = [t.strip() for t in (tm.group("impl") or "").split(",") if t.strip()]
            jclass = JavaClass(
                name=name,
                kind=tm.group("kind"),
                package=result.package,
                fully_qualified_name=f"{result.package}.{name}" if result.package else name,
                visibility=tm.group("vis"),
                extends_name=(tm.group("extends") or None),
                implements_names=impl,
                line_start=_line_of(src, tm.start()),
            )
            result.classes.append(jclass)

        # Methods and string fields are attached to the nearest preceding class.
        for fm in _METHOD_RE.finditer(src):
            if fm.group("name") in _JAVA_KEYWORDS:
                continue
            owner = self._owner(result.classes, _line_of(src, fm.start()))
            if owner is None:
                continue
            owner.methods.append(
                JavaMethod(
                    name=fm.group("name"),
                    signature=f"{fm.group('name')}({fm.group('params').strip()})",
                    return_type=fm.group("ret"),
                    parameters=fm.group("params").strip() or None,
                    visibility=fm.group("vis"),
                    is_static=bool(fm.group("static")),
                    line_start=_line_of(src, fm.start()),
                )
            )

        for gm in _FIELD_RE.finditer(src):
            owner = self._owner(result.classes, _line_of(src, gm.start()))
            value = gm.group("value").strip()
            if value.startswith('"""'):
                value = value.strip('"').strip()
            else:
                value = value[1:-1]
            value = value.replace('\\"', '"').replace("\\n", " ").replace("\\t", " ").strip()
            fld = JavaField(
                name=gm.group("name"),
                type_name="String",
                visibility=gm.group("vis"),
                is_static=bool(gm.group("static")),
                is_final=bool(gm.group("final")),
                string_value=value,
                looks_like_sql=looks_like_sql(value),
                line_start=_line_of(src, gm.start()),
            )
            if owner is not None:
                owner.fields.append(fld)

        for cm in _CALL_RE.finditer(src):
            if cm.group("name") in _JAVA_KEYWORDS:
                continue
            result.calls.append(
                JavaCall(
                    callee_name=cm.group("name"),
                    callee_qualifier=cm.group("qual"),
                    line_number=_line_of(src, cm.start()),
                )
            )
        return result

    @staticmethod
    def _owner(classes: list[JavaClass], line: int) -> JavaClass | None:
        owner = None
        for c in classes:
            if c.line_start and c.line_start <= line:
                owner = c
        return owner


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def parse_java(source: str) -> JavaFileParse:
    """Parse Java source text. Tries tree-sitter, falls back to regex, never raises."""
    ts_parser = _get_ts_parser()
    if ts_parser is not None:
        try:
            return _TreeSitterJava(source.encode("utf-8")).parse()
        except Exception as exc:  # pragma: no cover - defensive
            fallback = _RegexJava(source).parse()
            fallback.error = f"tree-sitter failed, used regex: {exc}"
            return fallback
    try:
        return _RegexJava(source).parse()
    except Exception as exc:  # pragma: no cover - defensive
        return JavaFileParse(parser="regex", error=str(exc))
