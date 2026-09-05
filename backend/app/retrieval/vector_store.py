"""
backend/app/retrieval/vector_store.py

Purpose
-------
Local vector storage + cosine search (build plan §34 — a local, easy-to-operate
store; Qdrant can be swapped in later behind this same interface).

Responsibility
--------------
- `VectorStore` interface: `replace_project()`, `search()`, `count()`.
- `SqliteVectorStore`: stores each chunk's `float32` vector (+ L2 norm) as a BLOB
  in the existing `embeddings` table, alongside a `chunks` row. Search loads the
  project's vectors into one matrix and does an exact cosine top-k with numpy —
  fine for a ~2k-file project; no external daemon.

Vectors are assumed pre-normalised by the embedder, so cosine == dot product.
"""
from __future__ import annotations

import abc

import numpy as np

from backend.app.models.database import get_connection, transaction


class VectorStore(abc.ABC):
    @abc.abstractmethod
    def replace_project(self, project_id: int, rows: list[tuple[dict, np.ndarray]], model: str) -> int:
        """rows: list of (chunk_dict, vector). Clears the project first."""

    @abc.abstractmethod
    def search(self, project_id: int, query_vec: np.ndarray, k: int) -> list[dict]:
        ...

    @abc.abstractmethod
    def count(self, project_id: int) -> int:
        ...


class SqliteVectorStore(VectorStore):
    def __init__(self, conn=None) -> None:
        self.conn = conn or get_connection()

    def replace_project(self, project_id: int, rows: list[tuple[dict, np.ndarray]], model: str) -> int:
        with transaction(self.conn) as cur:
            cur.execute(
                "DELETE FROM embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE project_id=?)",
                (project_id,),
            )
            cur.execute("DELETE FROM chunks WHERE project_id=?", (project_id,))
            for chunk, vec in rows:
                cur.execute(
                    "INSERT INTO chunks(project_id, file_id, chunk_type, symbol, line_start, line_end, content) "
                    "VALUES(?, (SELECT id FROM files WHERE project_id=? AND path=?), ?, ?, ?, ?, ?)",
                    (project_id, project_id, chunk["file"], chunk["chunk_type"], chunk["symbol"],
                     chunk["line_start"], chunk["line_end"], chunk["content"]),
                )
                chunk_id = int(cur.lastrowid)
                v = np.asarray(vec, dtype=np.float32)
                cur.execute(
                    "INSERT INTO embeddings(chunk_id, project_id, model, dim, vector, norm) VALUES(?,?,?,?,?,?)",
                    (chunk_id, project_id, model, int(v.shape[0]), v.tobytes(),
                     float(np.linalg.norm(v)) or 1.0),
                )
        return self.count(project_id)

    def search(self, project_id: int, query_vec: np.ndarray, k: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT e.chunk_id, e.vector, e.dim, e.norm, c.chunk_type, c.symbol, c.line_start, "
            "c.content, f.path AS file "
            "FROM embeddings e JOIN chunks c ON c.id=e.chunk_id "
            "LEFT JOIN files f ON f.id=c.file_id WHERE e.project_id=?",
            (project_id,),
        ).fetchall()
        if not rows:
            return []
        q = np.asarray(query_vec, dtype=np.float64).ravel()
        qn = float(np.linalg.norm(q)) or 1.0

        # Only compare vectors of matching dimensionality (an embedder/model
        # change leaves stale rows behind until the next full re-index).
        usable = [r for r in rows if len(r["vector"]) // 4 == q.shape[0]]
        if not usable:
            return []
        mat = np.vstack([np.frombuffer(r["vector"], dtype=np.float32).astype(np.float64) for r in usable])
        norms = np.array([(r["norm"] or 1.0) for r in usable], dtype=np.float64)
        norms[norms == 0] = 1.0
        with np.errstate(all="ignore"):
            sims = np.nan_to_num((mat @ q) / (norms * qn))

        top = np.argsort(-sims)[:k]
        out = []
        for i in top:
            r = usable[int(i)]
            out.append({
                "chunk_id": r["chunk_id"],
                "score": float(sims[int(i)]),
                "file": r["file"],
                "symbol": r["symbol"],
                "chunk_type": r["chunk_type"],
                "line_start": r["line_start"],
                "snippet": (r["content"] or "")[:400],
            })
        return out

    def count(self, project_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM embeddings WHERE project_id=?", (project_id,)).fetchone()[0]
