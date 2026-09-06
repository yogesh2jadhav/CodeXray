"""
backend/app/llm/prompt_builder.py

Purpose
-------
Turn a question + `BuiltContext` into the exact (system, user) strings sent to
the model (build plan §25 final step, §61 technical-lead mode, §72 evidence).

Responsibility
--------------
- `SYSTEM_PROMPT`: the technical-lead persona and the hard rules —
    * answer ONLY from the provided context;
    * label claims FACT / INFERENCE / UNKNOWN;
    * cite `file:line`;
    * for dynamic SQL, report RESOLVED / PARTIALLY_RESOLVED / UNRESOLVED and
      NEVER invent a table or column name (§29).
- `build(question, context)` -> `(system, user_prompt)` with the structured
  context block and a short answer-format instruction tailored to the question
  type (explain / architecture / impact / debugging).

Pure string assembly; no I/O.
"""
from __future__ import annotations

from backend.app.llm.context_builder import BuiltContext
from backend.app.llm.question_classifier import QuestionType

SYSTEM_PROMPT = """You are CodeXray, a senior technical lead for a large Java + SQL enterprise codebase.
You answer questions about THIS project using ONLY the evidence block provided by the
deterministic project index (AST, SQL parser, dependency graph, dynamic-SQL analyzer).

Rules:
1. Ground every statement in the evidence. If the evidence does not support an answer,
   say so plainly. Do not use outside assumptions about frameworks or naming.
2. Label claims:
   FACT:      directly stated by the evidence (cite file:line).
   INFERENCE: a reasonable deduction from multiple facts (say why).
   UNKNOWN:   not determinable from the evidence provided.
3. For dynamic SQL, use the analyzer's status: RESOLVED / PARTIALLY_RESOLVED / UNRESOLVED.
   Never invent a concrete table or column name. If a table name comes from a metadata
   lookup, explain the chain and state that the runtime value is not statically known.
4. Be concise and specific. Prefer "CustomerService.loadEligibleCustomers (file:line)"
   over vague description. Use short sections, not essays.
5. End with an "Evidence:" list of the file:line references you relied on.
"""

_FORMAT_HINTS = {
    QuestionType.ARCHITECTURE: (
        "Structure: Purpose · Entry points · Layers/'modules' · Data access · Notable risks. "
        "Draw the flow as an arrow list if useful."
    ),
    QuestionType.CODE_EXPLANATION: (
        "Structure: Purpose · Inputs · Outputs · Control flow · Dependencies · "
        "Database interactions/SQL · Side effects · Risks/assumptions."
    ),
    QuestionType.DYNAMIC_SQL: (
        "Trace the construction: source method -> constants/variables/metadata lookups -> "
        "template -> tables/columns. State the resolution status and exactly what is unknown."
    ),
    QuestionType.IMPACT: (
        "Structure: Direct impact · Indirect (callers of callers) · SQL/data impact · "
        "Test impact · Unknowns. Be explicit about confidence."
    ),
    QuestionType.DEBUGGING: (
        "Structure: What the code does · Plausible cause(s) tied to evidence · "
        "What to check next · What cannot be concluded statically."
    ),
    QuestionType.DEPENDENCY: "List concrete callers/callees/references with file:line. No speculation.",
    QuestionType.SQL: "Identify the query, its tables/columns/joins/filters, and where it is executed.",
    QuestionType.BUSINESS_LOGIC: "Point to the exact method(s)/SQL/config where the rule lives, with file:line.",
    QuestionType.GENERAL: "Answer directly and cite evidence.",
}

_LINE_BY_LINE_HINT = (
    "Walk through the method body in order using the numbered METHOD SOURCE. "
    "For each statement (or a small group of tightly-related lines) output:\n"
    "  Lines N-M:  <quote the code>\n"
    "    <what it does, why it is there, and any risk / side effect / assumption>\n"
    "Cover every non-trivial line — declarations, branches, loops, calls, returns, catch blocks. "
    "Skip only blank lines and pure boilerplate. After the walkthrough add a short 'Summary' "
    "(purpose, inputs, outputs, key dependencies, SQL touched). Do not invent code that is not shown."
)


def build(question: str, context: BuiltContext) -> tuple[str, str]:
    cls = context.classification
    hint = _LINE_BY_LINE_HINT if cls.line_by_line else _FORMAT_HINTS.get(cls.qtype, _FORMAT_HINTS[QuestionType.GENERAL])
    user_prompt = (
        f"QUESTION: {question}\n\n"
        f"QUESTION TYPE: {context.classification.qtype.value}\n"
        f"ANSWER FORMAT: {hint}\n\n"
        f"--- PROJECT EVIDENCE ({context.project_name}) ---\n\n"
        f"{context.render()}\n\n"
        f"--- END EVIDENCE ---\n\n"
        f"Answer the question using only the evidence above. Label FACT / INFERENCE / UNKNOWN "
        f"and finish with an Evidence: list."
    )
    return SYSTEM_PROMPT, user_prompt
