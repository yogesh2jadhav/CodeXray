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
    "Reproduce the method IN FULL from the numbered METHOD SOURCE, and insert an explanatory "
    "block comment directly ABOVE each logical section of code. Use exactly this shape:\n\n"
    "```java\n"
    "/*\n"
    " * <one line: what this section does and why>\n"
    " *\n"
    " * <optional: the sub-steps, as>\n"
    " * a. <step>\n"
    " * b. <step>\n"
    " */\n"
    "<the original code of that section, verbatim, same indentation>\n"
    "```\n\n"
    "A 'section' is a declaration group, a stream/lambda pipeline, a loop, an if/else branch, "
    "a try/catch, or a return. Keep EVERY original line of code, in order, unchanged — do not "
    "rewrite, reformat or simplify it. Cover the whole method. After the annotated code add:\n"
    "  Summary: purpose · inputs · outputs · key dependencies · SQL/DB touched · risks & assumptions\n"
    "Do not invent code, fields or SQL that are not in the evidence."
)


_FLOW_HINT = (
    "Narrate the full execution flow in plain language, following the EXECUTION FLOW "
    "call tree in order. Number each step:\n"
    "  1. <Class.method> — <what happens here, in one or two sentences: what it receives, "
    "what it does, what it calls next, what data/DB/SQL it touches>\n"
    "Recurse into nested calls exactly as the tree shows; when the tree marks a branch as "
    "bounded or recursive, say so rather than guessing. Use the analyzer's dynamic-SQL status "
    "verbatim and never invent a table/column. End with:\n"
    "  - a compact arrow diagram of the flow (A -> B -> C ...)\n"
    "  - 'Data touched:' the tables read/written and where\n"
    "  - 'Open questions:' anything not statically determinable."
)


def build(question: str, context: BuiltContext) -> tuple[str, str]:
    cls = context.classification
    if cls.flow:
        closing = (
            "Narrate the flow as specified in ANSWER FORMAT using only the EXECUTION FLOW tree "
            "and the source shown. Finish with the arrow diagram, 'Data touched:' and "
            "'Open questions:' sections, then an Evidence: list."
        )
        hint = _FLOW_HINT
        user_prompt = (
            f"QUESTION: {question}\n\nQUESTION TYPE: {cls.qtype.value}\n"
            f"ANSWER FORMAT: {hint}\n\n"
            f"--- PROJECT EVIDENCE ({context.project_name}) ---\n\n{context.render()}\n\n"
            f"--- END EVIDENCE ---\n\n{closing}"
        )
        return SYSTEM_PROMPT, user_prompt
    if cls.line_by_line:
        closing = (
            "Produce the annotated method exactly as specified in ANSWER FORMAT, using only the "
            "code shown in the evidence. Then the Summary line, then an Evidence: list of the "
            "file:line references. Do not add FACT/INFERENCE/UNKNOWN prefixes to the code."
        )
        hint = _LINE_BY_LINE_HINT
    else:
        closing = (
            "Answer the question using only the evidence above. Label FACT / INFERENCE / UNKNOWN "
            "and finish with an Evidence: list."
        )
        hint = _FORMAT_HINTS.get(cls.qtype, _FORMAT_HINTS[QuestionType.GENERAL])

    user_prompt = (
        f"QUESTION: {question}\n\n"
        f"QUESTION TYPE: {context.classification.qtype.value}\n"
        f"ANSWER FORMAT: {hint}\n\n"
        f"--- PROJECT EVIDENCE ({context.project_name}) ---\n\n"
        f"{context.render()}\n\n"
        f"--- END EVIDENCE ---\n\n"
        f"{closing}"
    )
    return SYSTEM_PROMPT, user_prompt
