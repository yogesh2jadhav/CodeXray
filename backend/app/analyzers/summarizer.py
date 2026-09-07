"""
backend/app/analyzers/summarizer.py

Purpose
-------
Generate a one-line "what this method does" summary for every method, at index
time, so flow traces and the explorer can show a purpose next to each step
(build plan §38 — method-level overview).

Responsibility
--------------
- Prefer the method's Javadoc / leading comment first sentence.
- Otherwise derive from deterministic signals: the camelCase-split name + a verb
  template (get/find/build/process/validate/save/convert/…), plus what the method
  touches (reads/writes which tables, builds dynamic SQL, calls which methods).
- Persist to `method_summaries` (rebuilt each run).

Heuristic and fast (~1 s for a few thousand methods): no LLM, no network. An
optional LLM refinement for the methods in a specific flow can be layered later.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from backend.app.models.database import get_connection, transaction

log = logging.getLogger("codexray.summarizer")

_VERB = [
    (re.compile(r"^(get|fetch|load|read|retrieve|lookup|find|select|query|list)"), "Returns"),
    (re.compile(r"^(is|has|can|should|contains|matches|exists)"), "Checks whether"),
    (re.compile(r"^(set|assign|apply|populate|fill)"), "Sets"),
    (re.compile(r"^(build|create|make|construct|generate|assemble|compose|prepare)"), "Builds"),
    (re.compile(r"^(process|handle|run|execute|perform|do)"), "Processes"),
    (re.compile(r"^(validate|check|verify|ensure|assert)"), "Validates"),
    (re.compile(r"^(save|store|persist|insert|write|update|delete|remove|merge|upsert)"), "Persists / updates"),
    (re.compile(r"^(convert|map|transform|parse|to|from|serialize|deserialize|format)"), "Converts"),
    (re.compile(r"^(calculate|compute|derive|resolve|determine|evaluate)"), "Computes"),
    (re.compile(r"^(add|append|register|put|push|enqueue)"), "Adds"),
    (re.compile(r"^(init|initialize|configure|setup|start|open|connect)"), "Initialises"),
    (re.compile(r"^(close|stop|shutdown|dispose|cleanup|release)"), "Tears down"),
]
_CAMEL = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")
_JDOC_SENT = re.compile(r"[.!?](\s|$)")


def _humanise(name: str) -> str:
    parts = [p.lower() for p in _CAMEL.findall(name)]
    return " ".join(parts) or name


def _javadoc_above(src: list[str], line_start: int) -> str | None:
    """First sentence of a /** */ or // block immediately preceding the method."""
    i = line_start - 2  # 0-based line just above the signature
    # skip annotations / blank lines
    while i >= 0 and (not src[i].strip() or src[i].strip().startswith("@")):
        i -= 1
    if i < 0:
        return None
    block: list[str] = []
    if src[i].strip().endswith("*/"):
        while i >= 0:
            block.insert(0, src[i])
            if src[i].strip().startswith("/*"):
                break
            i -= 1
    else:
        while i >= 0 and src[i].strip().startswith("//"):
            block.insert(0, src[i])
            i -= 1
    if not block:
        return None
    text = " ".join(
        re.sub(r"^\s*(/\*\*?|\*/?|//)", "", ln).strip() for ln in block
    ).strip()
    text = re.sub(r"\{@\w+\s+([^}]+)\}", r"\1", text)          # {@link X#y} -> X#y
    text = text.replace("#", ".").replace("<p>", " ").replace("</p>", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.split(r"@param|@return|@throws|@see", text)[0].strip()
    if not text or len(text) < 6:
        return None
    m = _JDOC_SENT.search(text)
    sentence = text[: m.start() + 1] if m else text
    return sentence[:160].strip()


def _signals(conn, project_id: int, method_id: int, qualified: str) -> str:
    reads, writes, meta_t = set(), set(), set()
    dyn = False
    for q in conn.execute(
        "SELECT id FROM sql_queries WHERE project_id=? AND source_class=? AND source_method=?",
        (project_id, qualified.split(".")[0], qualified.split(".")[-1]),
    ):
        for t in conn.execute("SELECT name, access FROM sql_tables WHERE query_id=?", (q["id"],)):
            (writes if t["access"] == "write" else reads).add(t["name"].upper())
    for d in conn.execute(
        "SELECT id, tables_json FROM dynamic_sql WHERE project_id=? AND source_class=? AND source_method=?",
        (project_id, qualified.split(".")[0], qualified.split(".")[-1]),
    ):
        dyn = True
    bits = []
    if reads:
        bits.append(f"reads {', '.join(sorted(reads)[:3])}")
    if writes:
        bits.append(f"writes {', '.join(sorted(writes)[:3])}")
    if dyn:
        bits.append("builds dynamic SQL")
    return "; ".join(bits)


class MethodSummarizer:
    def __init__(self, conn=None) -> None:
        self.conn = conn or get_connection()

    def run(self, project_id: int) -> int:
        proj = self.conn.execute("SELECT root_path FROM projects WHERE id=?", (project_id,)).fetchone()
        root = Path(proj["root_path"]) if proj else None
        rows = list(self.conn.execute(
            "SELECT m.id, m.name, m.signature, m.line_start, c.name AS cname, f.path AS fpath "
            "FROM methods m LEFT JOIN classes c ON c.id=m.class_id JOIN files f ON f.id=m.file_id "
            "WHERE m.project_id=?", (project_id,),
        ))
        src_cache: dict[str, list[str]] = {}
        out: list[tuple] = []

        for r in rows:
            qualified = f'{r["cname"]}.{r["name"]}'
            summary, source = None, "heuristic"

            if root is not None and r["line_start"]:
                src = src_cache.get(r["fpath"])
                if src is None:
                    p = root / r["fpath"]
                    src = p.read_text(encoding="utf-8", errors="replace").splitlines() if p.is_file() else []
                    src_cache[r["fpath"]] = src
                jd = _javadoc_above(src, r["line_start"]) if src else None
                if jd:
                    summary, source = jd, "javadoc"

            if summary is None:
                verb = "Handles"
                low = r["name"].lower()
                for rx, v in _VERB:
                    if rx.match(low):
                        verb = v
                        break
                phrase = _humanise(r["name"])
                # drop the leading verb token from the humanised phrase to avoid "Returns get physical table"
                phrase = re.sub(r"^\w+\s+", "", phrase) if verb != "Handles" else phrase
                summary = f"{verb} {phrase}".strip()

            sig = self._signals(project_id, r["id"], qualified)
            if sig:
                summary = f"{summary} ({sig})"
            out.append((project_id, r["id"], qualified, summary[:240], source))

        with transaction(self.conn) as cur:
            cur.execute("DELETE FROM method_summaries WHERE project_id=?", (project_id,))
            cur.executemany(
                "INSERT OR REPLACE INTO method_summaries(project_id, method_id, qualified, summary, source) "
                "VALUES(?,?,?,?,?)", out,
            )
        log.info("method summaries for project %s: %d", project_id, len(out))
        return len(out)

    def _signals(self, project_id: int, method_id: int, qualified: str) -> str:
        return _signals(self.conn, project_id, method_id, qualified)


def load_summaries(conn, project_id: int) -> dict[str, str]:
    return {r["qualified"]: r["summary"] for r in conn.execute(
        "SELECT qualified, summary FROM method_summaries WHERE project_id=?", (project_id,))}
