"""Dense retrieval behind an injected embedder (plan v2.4 Phase 5).

The embedder is a *structural protocol*, not the MiniLM class: this module
never imports ``rag_platform.embeddings.minilm`` (and therefore never
triggers ``sentence_transformers``). The real embedder is injected by the
caller at runtime; tests inject deterministic fakes. Constructing or
invoking real MiniLM remains owner-gated (BG-1a pending, D3 deferred).

Score space: the Phase 2 store's score for its default Cosine collection
is cosine similarity in [-1, 1] - the ONLY score space the configurable
``retrieval_score_floor`` may compare against. BM25 never enters this
module.

Degradation contract: a missing collection yields ``[]`` (Phase 2 reads
are total). Embedder failures propagate as the Phase 3 typed
``EmbeddingError`` family to the caller (the hybrid retriever degrades
that query to lexical-only and reports it); vector-store misconfiguration
propagates as the store's typed ``VectorStoreError`` - index corruption
is never silently degraded.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from rag_platform.retrieval.errors import DenseIndexError
from rag_platform.retrieval.types import DenseHit
from rag_platform.vectorstore.qdrant_local import QdrantLocalStore


@runtime_checkable
class Embedder(Protocol):
    """Structural contract satisfied by ``MiniLMEmbedder`` (Phase 3).

    ``is_loaded`` mirrors the Phase 3 property exactly (a bool property,
    not a method); test fakes implement the same shape. Retrieval never
    reads model internals - only ``encode`` output.
    """

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...

    @property
    def is_loaded(self) -> bool: ...


def dense_search(
    store: QdrantLocalStore,
    collection: str,
    embedder: Embedder,
    query_text: str,
    *,
    limit: int,
) -> list[DenseHit]:
    """Ranked dense neighbours of *query_text* (1-based similarity order).

    The query text is embedded once; the store returns results already
    ordered by descending similarity. Hits whose payload lacks the
    ``chunk_id``/``doc_id`` provenance written by the Phase 5 indexer are
    skipped (defensively: they cannot be hydrated or cited), and ranks are
    renumbered over the usable hits. An injected embedder returning the
    wrong number of vectors raises :class:`DenseIndexError`; nothing is
    swallowed.
    """
    vectors = embedder.encode([query_text])
    if len(vectors) != 1:
        raise DenseIndexError(
            f"embedder returned {len(vectors)} vectors for 1 query text"
        )
    hits = store.search(collection, vectors[0], limit=limit)
    result: list[DenseHit] = []
    for hit in hits:
        chunk_id = hit.payload.get("chunk_id")
        doc_id = hit.payload.get("doc_id")
        if not isinstance(chunk_id, str) or not isinstance(doc_id, str):
            continue
        result.append(
            DenseHit(
                chunk_id=chunk_id,
                doc_id=doc_id,
                rank=len(result) + 1,
                cosine_similarity=float(hit.score),
            )
        )
    return result
