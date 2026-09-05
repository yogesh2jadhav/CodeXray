"""
backend/app/analyzers/dynamic_sql/expression.py

Purpose
-------
The intermediate representation for a reconstructed SQL string.

Responsibility
--------------
- `Part`: one contribution to a SQL string (a literal, a resolved constant, a
  method return, a metadata-query result, a bind of a method parameter, an
  unknown). Each part carries its own resolution status and evidence.
- `StringExpr`: an ordered list of parts, i.e. the whole expression that a
  variable / return value evaluates to.
- Deterministic helpers to (a) render a human-readable SQL *template* with
  `{slot}` placeholders for the unresolved parts, (b) render the fully resolved
  SQL when — and only when — every part is known, and (c) fold the parts'
  statuses into one overall status.

This module never guesses. If a part is unknown its text stays ``None`` and it
shows up in the template as a placeholder, never as an invented table/column.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from backend.app.models.records import ResolutionStatus


class PartKind(str, Enum):
    LITERAL = "LITERAL"                # string literal written in source
    CONSTANT = "CONSTANT"              # static final String reference (resolved)
    VARIABLE = "VARIABLE"             # local variable reference
    METHOD_RETURN = "METHOD_RETURN"    # value returned by a called method
    METADATA_QUERY = "METADATA_QUERY"  # value fetched by executing a metadata SQL
    PARAMETER = "PARAMETER"            # bound to an (unresolved) method parameter
    CONFIGURATION = "CONFIGURATION"    # value from a config/properties entry
    UNKNOWN = "UNKNOWN"                # cannot be characterised


# Map a part kind to the dependency_type vocabulary of dynamic_sql_dependencies.
DEPENDENCY_TYPE = {
    PartKind.LITERAL: "CONSTANT",
    PartKind.CONSTANT: "CONSTANT",
    PartKind.VARIABLE: "VARIABLE",
    PartKind.METHOD_RETURN: "METHOD_RETURN",
    PartKind.METADATA_QUERY: "METADATA_QUERY",
    PartKind.PARAMETER: "PARAMETER",
    PartKind.CONFIGURATION: "CONFIGURATION",
    PartKind.UNKNOWN: "UNKNOWN",
}


@dataclass
class Evidence:
    """A single traceable fact: claim -> source location (build plan §87)."""
    detail: str
    file: str | None = None
    line: int | None = None
    metadata_sql: str | None = None
    metadata_tables: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {"detail": self.detail, "file": self.file, "line": self.line}
        if self.metadata_sql:
            d["metadata_sql"] = self.metadata_sql
        if self.metadata_tables:
            d["metadata_tables"] = self.metadata_tables
        return d


@dataclass
class Part:
    kind: PartKind
    text: str | None = None                       # resolved value, if known
    label: str = ""                               # short name for the {slot}
    status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def slot(self) -> str:
        base = re.sub(r"[^A-Za-z0-9_]+", "_", self.label or self.kind.value).strip("_").lower()
        return "{" + (base or "value") + "}"

    def as_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "label": self.label,
            "status": self.status.value,
            "dependency_type": DEPENDENCY_TYPE[self.kind],
            "evidence": [e.as_dict() for e in self.evidence],
        }


def literal(text: str) -> Part:
    return Part(PartKind.LITERAL, text=text, label="literal", status=ResolutionStatus.RESOLVED)


@dataclass
class StringExpr:
    parts: list[Part] = field(default_factory=list)

    # -- construction --------------------------------------------------------
    def add(self, part: Part) -> "StringExpr":
        # Merge adjacent literals to keep the template readable.
        if part.kind is PartKind.LITERAL and self.parts and self.parts[-1].kind is PartKind.LITERAL:
            self.parts[-1].text = (self.parts[-1].text or "") + (part.text or "")
        else:
            self.parts.append(part)
        return self

    def extend(self, other: "StringExpr") -> "StringExpr":
        for p in other.parts:
            self.add(p)
        return self

    # -- inspection --------------------------------------------------------
    @property
    def dynamic_parts(self) -> list[Part]:
        return [p for p in self.parts if p.kind not in (PartKind.LITERAL, PartKind.CONSTANT)]

    def looks_like_sql(self) -> bool:
        skeleton = " ".join(p.text or "" for p in self.parts if p.text)
        return bool(re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|MERGE|FROM|WHERE|JOIN)\b", skeleton, re.I))

    # -- rendering --------------------------------------------------------
    def template(self) -> str:
        out = []
        for p in self.parts:
            if p.status is ResolutionStatus.RESOLVED and p.text is not None:
                out.append(p.text)
            else:
                out.append(p.slot)
        return _squash(" ".join(s for s in out if s != "")) if False else _squash("".join(out))

    def resolved_sql(self) -> str | None:
        if any(p.status is not ResolutionStatus.RESOLVED or p.text is None for p in self.parts):
            return None
        return _squash("".join(p.text or "" for p in self.parts))

    def status(self) -> ResolutionStatus:
        if not self.parts:
            return ResolutionStatus.UNRESOLVED
        statuses = {p.status for p in self.parts}
        if statuses == {ResolutionStatus.RESOLVED}:
            return ResolutionStatus.RESOLVED
        # We have at least a skeleton of literals/constants → partially resolved.
        if any(p.kind in (PartKind.LITERAL, PartKind.CONSTANT) and p.text for p in self.parts):
            return ResolutionStatus.PARTIALLY_RESOLVED
        return ResolutionStatus.UNRESOLVED

    def as_dict(self) -> dict:
        return {
            "template": self.template(),
            "resolved_sql": self.resolved_sql(),
            "status": self.status().value,
            "parts": [p.as_dict() for p in self.parts],
        }


def _squash(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()
