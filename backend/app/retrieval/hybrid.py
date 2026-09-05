"""
backend/app/retrieval/hybrid.py

Purpose
-------
Blend the retrieval signals into one ranked list (build plan §24, §36).

Responsibility
--------------
- Run symbol search, keyword search and semantic search for a query.
- Score each candidate with the configurable weights
  (`retrieval.weights`: symbol_exact / path_match / keyword / semantic /
   dependency), normalising each signal to 0..1 first.
- Add a small dependency-proximity bonus: a candidate that is a graph neighbour
  of the top symbol hit gets boosted (call-graph proximity, §36).
- De-duplicate by (file, symbol) and return the top-k with a per-signal
  breakdown so the ranking stays explainable.

Deterministic given the same index + embedder.
"""
from __future__ import annotations

from backend.app.config.settings import get_settings
from backend.app.models.database import get_connection
from backend.app.retrieval import search as KW
from backend.app.retrieval.semantic import SemanticIndex


def _key(file: str | None, symbol: str | None) -> str:
    return f"{file or '?'}::{symbol or '?'}"


def hybrid_search(
    project_id: int,
    query: str,
    k: int | None = None,
    *,
    semantic: SemanticIndex | None = None,
    conn=None,
) -> list[dict]:
    cfg = get_settings()
    w = cfg.retrieval_weights
    k = k or cfg.retrieval.hybrid_top_k
    conn = conn or get_connection()
    sem = semantic or SemanticIndex(conn=conn)

    pool: dict[str, dict] = {}

    def bump(key: str, signal: str, score: float, meta: dict) -> None:
        entry = pool.setdefault(key, {"signals": {}, **meta})
        entry["signals"][signal] = max(entry["signals"].get(signal, 0.0), score)
        entry.update({mk: mv for mk, mv in meta.items() if mv is not None and not entry.get(mk)})

    # --- symbol
    sym_hits = KW.search_symbols(project_id, query, limit=k * 2, conn=conn)
    for i, h in enumerate(sym_hits):
        bump(_key(h["file"], h["qualified"]), "symbol", (h["score"] / 3.0) * (1 - i / (len(sym_hits) + 1)),
             {"file": h["file"], "symbol": h["qualified"], "line": h.get("line_number"), "type": h["kind"]})

    # --- keyword
    for i, h in enumerate(KW.search_keyword(project_id, query, limit=k * 2, conn=conn)):
        bump(_key(h["file"], h["label"]), "keyword", max(0.0, 1 - i / (k * 2)),
             {"file": h["file"], "symbol": h["label"], "line": h.get("line"), "type": h["type"]})

    # --- semantic
    try:
        for h in sem.search(project_id, query, k=k * 2):
            norm = (h["score"] + 1) / 2  # cosine [-1,1] -> [0,1]
            bump(_key(h["file"], h["symbol"]), "semantic", norm,
                 {"file": h["file"], "symbol": h["symbol"], "line": h.get("line_start"),
                  "type": h["chunk_type"], "snippet": h.get("snippet")})
    except Exception:
        pass  # semantic index not built yet — degrade to symbol+keyword

    # --- dependency proximity bonus (call-graph neighbours of the top symbol hit)
    if sym_hits:
        try:
            from backend.app.graph.service import GraphService
            gs = GraphService(project_id, conn=conn)
            top = sym_hits[0]["qualified"].split(".")[-1]
            near = set(gs.callers(top)) | set(gs.callees(top))
            for entry in pool.values():
                sym = (entry.get("symbol") or "").split(".")[-1]
                if sym and sym in {n.split(".")[-1] for n in near}:
                    entry["signals"]["dependency"] = 1.0
        except Exception:
            pass

    weight = {
        "symbol": w.get("symbol_exact", 5.0),
        "keyword": w.get("keyword", 1.5),
        "semantic": w.get("semantic", 3.0),
        "dependency": w.get("dependency", 1.0),
    }
    results = []
    for entry in pool.values():
        score = sum(weight.get(sig, 1.0) * val for sig, val in entry["signals"].items())
        entry["score"] = round(score, 4)
        results.append(entry)
    results.sort(key=lambda e: e["score"], reverse=True)
    return results[:k]
