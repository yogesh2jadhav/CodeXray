"""
backend/app/retrieval/semantic.py

Purpose
-------
Semantic (vector) retrieval over the project (build plan §23, §58).

Responsibility
--------------
- `SemanticIndex.build(project_id)`: chunk -> embed -> store, wholesale per run.
- `SemanticIndex.search(project_id, query, k)`: embed the query, cosine top-k
  from the vector store, return hits with file/symbol/line/snippet.
- `status(project_id)`: chunk count + embedder in use.

Vector search is a *candidate finder*, not the source of truth — the build plan
is explicit that graph/data-flow analysis handles deterministic relationships
(§23). Hybrid ranking blends this with symbol/keyword signals.
"""
from __future__ import annotations

import logging

from backend.app.config.settings import get_settings
from backend.app.models.database import get_connection
from backend.app.retrieval.chunker import Chunker
from backend.app.retrieval.embeddings import Embedder, get_embedder
from backend.app.retrieval.vector_store import SqliteVectorStore, VectorStore

log = logging.getLogger("codexray.semantic")


class SemanticIndex:
    def __init__(self, embedder: Embedder | None = None, store: VectorStore | None = None, conn=None) -> None:
        self.conn = conn or get_connection()
        self.embedder = embedder or get_embedder()
        self.store = store or SqliteVectorStore(self.conn)

    # ------------------------------------------------------------------ build
    def build(self, project_id: int) -> int:
        chunks = Chunker(self.conn).chunk_project(project_id)
        if not chunks:
            return 0
        texts = [f"{c.chunk_type} {c.symbol}\n{c.content}" for c in chunks]
        batch = max(get_settings().embedding.batch, 1)
        vectors = []
        for i in range(0, len(texts), batch):
            vectors.extend(self.embedder.embed(texts[i:i + batch]))
        rows = [
            ({"file": c.file, "chunk_type": c.chunk_type, "symbol": c.symbol,
              "line_start": c.line_start, "line_end": c.line_end, "content": c.content}, v)
            for c, v in zip(chunks, vectors)
        ]
        n = self.store.replace_project(project_id, rows, model=self.embedder.name)
        log.info("semantic index for project %s: %d chunks (%s)", project_id, n, self.embedder.name)
        return n

    # ----------------------------------------------------------------- search
    def search(self, project_id: int, query: str, k: int | None = None) -> list[dict]:
        k = k or get_settings().retrieval.semantic_top_k
        qvec = self.embedder.embed_one(query)
        return self.store.search(project_id, qvec, k)

    def status(self, project_id: int) -> dict:
        return {
            "embedder": self.embedder.name,
            "dim": getattr(self.embedder, "dim", None),
            "chunks": self.store.count(project_id),
        }
