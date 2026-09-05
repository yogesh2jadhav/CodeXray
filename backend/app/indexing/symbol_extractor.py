"""
backend/app/indexing/symbol_extractor.py

Purpose
-------
Turn a `JavaFileParse` into a flat list of searchable symbols (build plan §18).

Responsibility
--------------
- Emit one `Symbol` per package / class / interface / method / field.
- Give each a simple `name` (for partial search) and a `qualified` name such as
  ``CustomerService.processCustomer`` (for exact search and evidence display).
- Carry the line number and the record kind so the API can jump to source and
  the index writer can back-link to the owning row.

No I/O. Pure transformation.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.app.models.records import JavaFileParse


@dataclass(frozen=True)
class Symbol:
    kind: str          # package | class | interface | method | field
    name: str
    qualified: str
    line_number: int | None
    # Index within its own list on the parse (class index, or method/field
    # "classIdx.memberIdx") so the writer can resolve the real DB id later.
    locator: str


def extract_symbols(java: JavaFileParse) -> list[Symbol]:
    symbols: list[Symbol] = []

    if java.package:
        symbols.append(Symbol("package", java.package.split(".")[-1], java.package, None, "pkg"))

    for ci, cls in enumerate(java.classes):
        kind = "interface" if cls.kind == "interface" else "class"
        symbols.append(
            Symbol(kind, cls.name, cls.fully_qualified_name or cls.name, cls.line_start, f"{ci}")
        )
        for mi, method in enumerate(cls.methods):
            symbols.append(
                Symbol(
                    "method",
                    method.name,
                    f"{cls.name}.{method.name}",
                    method.line_start,
                    f"{ci}.m{mi}",
                )
            )
        for fi, fld in enumerate(cls.fields):
            symbols.append(
                Symbol(
                    "field",
                    fld.name,
                    f"{cls.name}.{fld.name}",
                    fld.line_start,
                    f"{ci}.f{fi}",
                )
            )
    return symbols
