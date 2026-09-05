"""
backend/app/llm/response_parser.py

Purpose
-------
Parse the model's free-text answer into a light structure for the API / UI
(build plan §50, §72).

Responsibility
--------------
- Split out FACT / INFERENCE / UNKNOWN lines.
- Extract the trailing "Evidence:" list and any inline `file.java:123` refs.
- Derive a coarse confidence: HIGH if there are facts and no unknowns,
  LOW if the answer is mostly UNKNOWN, MEDIUM otherwise.
- Always keep the full raw text.

Never rewrites the answer; only annotates it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_LABEL_RE = re.compile(r"^\s*(FACT|INFERENCE|UNKNOWN)\s*:\s*(.+)$", re.I | re.M)
_REF_RE = re.compile(r"([A-Za-z0-9_./-]+\.(?:java|sql|properties|xml|yaml|yml|json))(?::(\d+))?")
_EVIDENCE_BLOCK_RE = re.compile(r"(?is)\bevidence\s*:\s*\n?(.+)$")


@dataclass
class ParsedAnswer:
    answer: str
    facts: list[str] = field(default_factory=list)
    inferences: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    cited_refs: list[dict] = field(default_factory=list)
    confidence: str = "MEDIUM"

    def as_dict(self) -> dict:
        return {
            "answer": self.answer,
            "facts": self.facts,
            "inferences": self.inferences,
            "unknowns": self.unknowns,
            "cited_refs": self.cited_refs,
            "confidence": self.confidence,
        }


def parse(text: str) -> ParsedAnswer:
    text = (text or "").strip()
    facts, inferences, unknowns = [], [], []
    for label, body in _LABEL_RE.findall(text):
        bucket = {"fact": facts, "inference": inferences, "unknown": unknowns}[label.lower()]
        bucket.append(body.strip())

    refs: list[dict] = []
    seen: set[tuple[str, str | None]] = set()
    ev_block = _EVIDENCE_BLOCK_RE.search(text)
    scan_targets = [ev_block.group(1)] if ev_block else []
    scan_targets.append(text)
    for chunk in scan_targets:
        for path, line in _REF_RE.findall(chunk):
            key = (path, line or None)
            if key in seen:
                continue
            seen.add(key)
            refs.append({"file": path, "line": int(line) if line else None})

    if facts and not unknowns:
        confidence = "HIGH"
    elif not facts or len(unknowns) > len(facts):
        confidence = "LOW"
    else:
        confidence = "MEDIUM"

    return ParsedAnswer(
        answer=text,
        facts=facts,
        inferences=inferences,
        unknowns=unknowns,
        cited_refs=refs,
        confidence=confidence,
    )
