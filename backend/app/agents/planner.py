"""
backend/app/agents/planner.py

Purpose
-------
Deterministic investigation plan for a question (build plan §27).

Responsibility
--------------
- Map a `Classification` to an ordered list of concrete `ToolCall`s that will
  gather the evidence a good answer needs — before the LLM is involved at all.
- Fill tool arguments from the classification's extracted symbols / tables, with
  a keyword fallback when none were found.

Why deterministic first: a weak local model may plan poorly. The planner
guarantees the essential evidence is collected; the agent's LLM loop then only
has to add targeted follow-ups. Mirrors the worked example in §27
("why does this process use TABLE_X?").
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.app.llm.question_classifier import Classification, QuestionType


@dataclass
class ToolCall:
    tool: str
    args: dict
    reason: str = ""


def _first(seq: list[str], default: str) -> str:
    return seq[0] if seq else default


def plan(cls: Classification) -> list[ToolCall]:
    q = cls.question
    sym = _first(cls.symbols, "")
    tbl = _first(cls.tables, "")
    kw = " ".join(w for w in q.replace("?", "").split() if len(w) > 3)[:80]
    calls: list[ToolCall] = []

    def add(tool: str, args: dict, reason: str) -> None:
        calls.append(ToolCall(tool, args, reason))

    if cls.qtype is QuestionType.ARCHITECTURE:
        add("get_project_architecture", {}, "high-level roles/layers/entry points")
        add("search_documents", {"query": kw or "architecture overview"}, "project docs")
        add("search_code", {"query": kw or q}, "anchor classes for the narrative")

    elif cls.qtype is QuestionType.DYNAMIC_SQL:
        if sym:
            add("search_symbol", {"name": sym}, "locate the construction site")
            add("trace_dynamic_sql", {"selector": sym}, "reconstruct the SQL template + deps")
            add("trace_metadata_dependency", {"selector": sym}, "metadata lookup chain")
            add("get_method", {"name": sym}, "read the constructing method")
        if tbl:
            add("find_table_usage", {"table": tbl}, "where the table is referenced")
            add("trace_dynamic_sql", {"selector": tbl}, "dynamic sites touching the table")
        if not sym and not tbl:
            add("search_code", {"query": kw or q}, "find the SQL-building code")
            add("trace_dynamic_sql", {"selector": kw.split(" ")[0] if kw else q}, "best-effort trace")

    elif cls.qtype is QuestionType.IMPACT:
        target = sym or tbl
        if target:
            add("search_symbol", {"name": target}, "confirm the symbol")
            add("impact_analysis", {"symbol": target}, "direct/indirect/SQL/table impact")
            add("find_callers", {"symbol": target}, "call sites")
        else:
            add("search_code", {"query": kw or q}, "identify the change target")

    elif cls.qtype is QuestionType.DEPENDENCY:
        if sym:
            add("search_symbol", {"name": sym}, "resolve the symbol")
            add("find_callers", {"symbol": sym}, "callers")
            add("find_callees", {"symbol": sym}, "callees")
            add("get_class_dependencies", {"name": sym.split('.')[0]}, "class-level deps")
        else:
            add("search_code", {"query": kw or q}, "locate the subject")

    elif cls.qtype is QuestionType.SQL:
        if tbl:
            add("find_table_usage", {"table": tbl}, "queries on the table")
        add("search_sql", {"query": tbl or kw or q}, "matching SQL")
        if sym:
            add("get_method", {"name": sym}, "method that runs the SQL")

    elif cls.qtype is QuestionType.DEBUGGING:
        if sym:
            add("get_method", {"name": sym}, "the suspect method")
            add("find_callers", {"symbol": sym}, "how it is invoked")
            add("trace_dynamic_sql", {"selector": sym}, "any dynamic SQL involved")
        add("search_code", {"query": kw or q}, "related code")
        add("search_sql", {"query": kw or q}, "related SQL")

    elif cls.qtype is QuestionType.BUSINESS_LOGIC:
        add("search_code", {"query": kw or q}, "where the rule lives")
        add("search_documents", {"query": kw or q}, "described in docs/config")
        if sym:
            add("get_method", {"name": sym}, "the implementing method")
        add("search_sql", {"query": kw or q}, "rule expressed in SQL")

    else:  # CODE_EXPLANATION / GENERAL
        if sym:
            add("search_symbol", {"name": sym}, "resolve the symbol")
            add("get_method", {"name": sym}, "read the method")
            add("get_class", {"name": sym.split('.')[0]}, "surrounding class")
            add("find_callers", {"symbol": sym}, "usage context")
        else:
            add("search_code", {"query": kw or q}, "find relevant code")
        add("search_sql", {"query": kw or q}, "any SQL touched")

    # de-dupe identical calls, cap the seed plan
    seen, out = set(), []
    for c in calls:
        key = (c.tool, tuple(sorted(c.args.items())))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out[:8]
