"""
backend/app/analyzers/tree_sitter_setup.py

Purpose
-------
One place that produces a working tree-sitter Java parser, across the several
incompatible tree-sitter releases and grammar-packaging options.

Responsibility
--------------
`get_java_parser()` tries, in order:
  1. `tree_sitter_java` + `tree_sitter` (the maintained path — has wheels for
     Python 3.11-3.13, both the 0.22 and 0.23+ Parser APIs).
  2. `tree_sitter_language_pack` (maintained multi-language bundle).
  3. `tree_sitter_languages` (legacy bundle, Python <= 3.11).
Returns a parser exposing `.parse(bytes) -> tree`, or ``None`` if nothing is
installed — callers then fall back to regex parsing and the app still runs.

The result is cached; the node API used elsewhere (`child_by_field_name`,
`children`, `type`, `start_byte`, `start_point`, `is_named`) is stable across
all these versions.
"""
from __future__ import annotations

import logging
from functools import lru_cache

log = logging.getLogger("codexray.tree_sitter")


def _from_tree_sitter_java():
    import tree_sitter_java as tsjava
    from tree_sitter import Language, Parser

    lang = Language(tsjava.language())
    try:
        return Parser(lang)                     # tree-sitter >= 0.23
    except TypeError:
        parser = Parser()                       # tree-sitter 0.22.x
        try:
            parser.language = lang
        except AttributeError:
            parser.set_language(lang)           # tree-sitter <= 0.21 style
        return parser


def _from_language_pack():
    from tree_sitter_language_pack import get_parser
    return get_parser("java")


def _from_legacy_bundle():
    from tree_sitter_languages import get_parser
    return get_parser("java")


@lru_cache(maxsize=1)
def get_java_parser():
    for name, factory in (
        ("tree_sitter_java", _from_tree_sitter_java),
        ("tree_sitter_language_pack", _from_language_pack),
        ("tree_sitter_languages", _from_legacy_bundle),
    ):
        try:
            parser = factory()
            # smoke test so a broken install is treated as "unavailable"
            parser.parse(b"class T {}")
            return parser
        except Exception as exc:  # pragma: no cover - environment dependent
            log.debug("tree-sitter backend %s unavailable: %s", name, exc)
    log.warning("no tree-sitter Java grammar available — using regex parser fallback")
    return None
