"""
backend/app/llm/question_classifier.py

Purpose
-------
Classify a user question so the right retrieval strategy is used (build plan §24).

Responsibility
--------------
- Map a question to a `QuestionType` (ARCHITECTURE, CODE_EXPLANATION, SQL,
  DYNAMIC_SQL, DEPENDENCY, IMPACT, DEBUGGING, BUSINESS_LOGIC, GENERAL) using
  deterministic keyword / phrase heuristics — no LLM call.
- Derive the ordered list of `RetrievalMode`s the context builder should run
  (SYMBOL, SEMANTIC, KEYWORD, GRAPH, SQL, DATA_FLOW, ARCHITECTURE, IMPACT,
  DEBUGGING).
- Extract candidate symbols/identifiers/table names mentioned in the question
  (CamelCase words, `Class.method`, ALL_CAPS) to seed retrieval.

Cheap, transparent, and easy to tune; the LLM never has to guess intent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class QuestionType(str, Enum):
    ARCHITECTURE = "ARCHITECTURE"
    CODE_EXPLANATION = "CODE_EXPLANATION"
    SQL = "SQL"
    DYNAMIC_SQL = "DYNAMIC_SQL"
    DEPENDENCY = "DEPENDENCY"
    IMPACT = "IMPACT"
    DEBUGGING = "DEBUGGING"
    BUSINESS_LOGIC = "BUSINESS_LOGIC"
    GENERAL = "GENERAL"


class RetrievalMode(str, Enum):
    SYMBOL = "SYMBOL"
    SEMANTIC = "SEMANTIC"
    KEYWORD = "KEYWORD"
    GRAPH = "GRAPH"
    SQL = "SQL"
    DATA_FLOW = "DATA_FLOW"
    ARCHITECTURE = "ARCHITECTURE"
    IMPACT = "IMPACT"
    DEBUGGING = "DEBUGGING"


@dataclass
class Classification:
    question: str
    qtype: QuestionType
    modes: list[RetrievalMode]
    symbols: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    line_by_line: bool = False   # statement-by-statement walkthrough of one method
    flow: bool = False           # recursive end-to-end flow trace from a starting method

    def as_dict(self) -> dict:
        return {
            "type": self.qtype.value,
            "retrieval_modes": [m.value for m in self.modes],
            "symbols": self.symbols,
            "tables": self.tables,
            "line_by_line": self.line_by_line,
            "flow": self.flow,
        }


_LINE_BY_LINE_RE = re.compile(
    r"\b(line[-\s]?by[-\s]?line|each line|every line|statement[-\s]by[-\s]statement|"
    r"walk (me )?through (the |every |each )?(line|statement|method)|"
    r"explain (each|every) (line|statement))\b",
    re.I,
)

_FLOW_RE = re.compile(
    r"\b(end[-\s]?to[-\s]?end|full flow|whole flow|entire flow|trace (the |this )?(flow|process|"
    r"execution|call|path)|follow (the |all )?(calls|call chain|nested (method|function)|method calls)|"
    r"recursive(ly)? (into|through)|from (the )?(entry ?point|start(ing)?( method)?) to (the )?(db|database|end)|"
    r"explain (the |this )?(full |complete |entire )?(flow|process|pipeline)|step[-\s]by[-\s]step through)\b",
    re.I,
)


# Ordered rules: first match wins. (pattern, type, modes)
_RULES: list[tuple[re.Pattern[str], QuestionType, list[RetrievalMode]]] = [
    (re.compile(r"\b(dynamic sql|generated sql|how is .*(table|column) .*(determined|derived|resolved)|"
                r"table name .*(come|from|determined)|assembled|stringbuilder|concatenat)", re.I),
     QuestionType.DYNAMIC_SQL,
     [RetrievalMode.DATA_FLOW, RetrievalMode.SQL, RetrievalMode.SYMBOL, RetrievalMode.GRAPH]),

    (re.compile(r"\b(impact|affected|break|regress|if i change|what could be impacted|blast radius)\b", re.I),
     QuestionType.IMPACT,
     [RetrievalMode.IMPACT, RetrievalMode.GRAPH, RetrievalMode.SYMBOL, RetrievalMode.SQL]),

    (re.compile(r"\b(why does|why is|duplicate|null pointer|exception|stack ?trace|bug|wrong|"
                r"fails?|root cause|incorrect)\b", re.I),
     QuestionType.DEBUGGING,
     [RetrievalMode.DEBUGGING, RetrievalMode.SYMBOL, RetrievalMode.GRAPH, RetrievalMode.SQL, RetrievalMode.SEMANTIC]),

    (re.compile(r"\b(architecture|overview|how does .* (system|application|project) work|"
                r"entry ?point|major (module|component)|high[- ]level|explain this project)\b", re.I),
     QuestionType.ARCHITECTURE,
     [RetrievalMode.ARCHITECTURE, RetrievalMode.SEMANTIC, RetrievalMode.GRAPH, RetrievalMode.SYMBOL]),

    (re.compile(r"\b(who calls|callers?|called by|callees?|depend(s|ency|ent)|references?|uses this|"
                r"which classes)\b", re.I),
     QuestionType.DEPENDENCY,
     [RetrievalMode.SYMBOL, RetrievalMode.GRAPH]),

    (re.compile(r"\b(sql|query|queries|table|column|join|select|insert|update|delete|schema)\b", re.I),
     QuestionType.SQL,
     [RetrievalMode.SQL, RetrievalMode.SYMBOL, RetrievalMode.KEYWORD]),

    (re.compile(r"\b(business rule|eligibility|calculat|policy|logic for|where is .* implemented)\b", re.I),
     QuestionType.BUSINESS_LOGIC,
     [RetrievalMode.SEMANTIC, RetrievalMode.SYMBOL, RetrievalMode.SQL, RetrievalMode.KEYWORD]),

    (re.compile(r"\b(explain|what does|describe|purpose of|walk me through|how does)\b", re.I),
     QuestionType.CODE_EXPLANATION,
     [RetrievalMode.SYMBOL, RetrievalMode.SEMANTIC, RetrievalMode.GRAPH, RetrievalMode.SQL]),
]

_SYMBOL_RE = re.compile(r"\b([A-Z][A-Za-z0-9]+(?:\.[a-z][A-Za-z0-9]*)?)\b")
_METHOD_RE = re.compile(r"\b([a-z][A-Za-z0-9]*)\(\)")
# lowerCamelCase identifiers (loadEligibleCustomers, getPhysicalTable) even without ()
_CAMEL_RE = re.compile(r"\b([a-z][a-z0-9]*[A-Z][A-Za-z0-9]*)\b")
_TABLE_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")
_STOP = {"SQL", "API", "AND", "THE", "FROM", "WHERE", "JOIN"}
# Capitalised English question words that are not code symbols.
_SYMBOL_STOP = {
    "How", "What", "Where", "Why", "When", "Which", "Who", "Whose", "Explain", "Trace",
    "Find", "Show", "List", "Describe", "Compare", "This", "That", "The", "Is", "Are",
    "Does", "Do", "If", "Can", "Could", "Would", "Should",
}


def classify(question: str) -> Classification:
    qtype, modes = QuestionType.GENERAL, [RetrievalMode.SEMANTIC, RetrievalMode.SYMBOL, RetrievalMode.KEYWORD]
    for pattern, t, m in _RULES:
        if pattern.search(question):
            qtype, modes = t, m
            break

    line_by_line = bool(_LINE_BY_LINE_RE.search(question))
    flow = bool(_FLOW_RE.search(question))
    if flow:
        qtype = QuestionType.CODE_EXPLANATION
        modes = [RetrievalMode.DATA_FLOW, RetrievalMode.GRAPH, RetrievalMode.SYMBOL, RetrievalMode.SQL]
        line_by_line = False  # flow narration takes precedence over per-line
    elif line_by_line:
        # a line-by-line request is always a code-explanation task
        qtype = QuestionType.CODE_EXPLANATION
        modes = [RetrievalMode.SYMBOL, RetrievalMode.GRAPH, RetrievalMode.SQL]

    symbols = {s for s in _SYMBOL_RE.findall(question) if s not in _SYMBOL_STOP}
    symbols |= {s for s in _METHOD_RE.findall(question)}
    symbols |= {s for s in _CAMEL_RE.findall(question)}
    tables = {t for t in _TABLE_RE.findall(question) if t not in _STOP and "." not in t}
    # a bare ALL_CAPS token is more likely a table than a symbol
    symbols -= tables

    return Classification(
        question=question,
        qtype=qtype,
        modes=modes,
        symbols=sorted(symbols),
        tables=sorted(tables),
        line_by_line=line_by_line,
        flow=flow,
    )
