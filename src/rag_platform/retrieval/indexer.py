"""Vector-index reconciliation for the dense leg (plan v2.4 Phase 5).

Chunk identity is content-derived (Phase 4: ``chunk_id =
sha256(doc_id, ordinal)`` with ``doc_id = sha256(source_path,
content_sha256)``), so replacing a document's content changes every chunk
id and leaves the previous version's points orphaned in the vector store.
Reconciliation makes the stored point set match the persisted corpus:

- ids in the store but not in the database -> deleted (stale),
- ids in the database but not in the store  -> embedded and upserted,
- ids in both                               -> left untouched. An
  unchanged corpus therefore embeds NOTHING (zero model calls, tested).

Point ids: Qdrant - and therefore the Phase 2 adapter - accepts only
non-negative ints or UUID *strings* as point ids; chunk ids are sha256
hex and are not UUIDs. Each point id is the deterministic UUIDv5
``uuid5(_POINT_NAMESPACE, chunk_id)``: a pure function of the chunk id,
so repeated reconciliations map the same chunk to the same point and the
diff stays exact. Payloads carry only ``chunk_id``/``doc_id``; content
and provenance are hydrated from SQLite at query time (the database stays
the single source of truth, and payloads stay small).

Honesty contract (Z8): setup and execution failures alike are captured
into :class:`IndexReport` with ``failed=True`` and the exception class
name only - a partial run never claims full success, and counts describe
what actually completed. Callers must check ``failed`` first. Nothing is
swallowed: the typed cause is chained inside the returned report field's
originating exception context only as a class name (no messages, no
paths).

Foreign points: the contract above makes the STORE's id set match the
corpus *exactly*, so any point whose id is not a persisted chunk id -
including ids this module never wrote (e.g. non-negative int ids the
adapter accepts from another writer) - is stale by definition and is
removed. Ids are sorted by their string form before removal so a mixed
int/UUID-string collection stays deterministic and cannot raise an
uncaught ``TypeError`` from ordering mixed types (remediation F2).

Memory behaviour (remediation F3): the corpus is read with ONE
UNBOUNDED ``fetchall()`` over the whole ``chunks`` table - chunk ids,
doc ids, and full content are materialized in memory at once, so peak
memory is proportional to total corpus content, not to the batch size.
That is acceptable for the bounded frozen corpus but is NOT a general
bound: a cursor-based/chunked corpus read is REQUIRED before any
real-corpus or large-corpus indexing run.

Model gating: reconciliation requires an injected embedder; running it
with real MiniLM embeds for real and stays owner-gated (BG-1a pending).
Tests inject counting fakes. Reconciliation is idempotent and
re-runnable; it deliberately opens no transaction and is not atomic with
ingestion - ingestion and indexing are separate owner-invoked steps.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

from rag_platform.embeddings.minilm import EmbeddingError
from rag_platform.retrieval.dense import Embedder
from rag_platform.retrieval.errors import DenseIndexError
from rag_platform.vectorstore.qdrant_local import (
    Point,
    QdrantLocalStore,
    VectorStoreError,
)

#: Fixed namespace for the deterministic chunk-id -> point-id derivation.
_POINT_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://github.com/aspire488/rag-platform/chunk-point-id/v1"
)

#: Embedding batch size (I7 bound on intermediate allocations).
_ENCODE_BATCH = 32


def point_id_for_chunk(chunk_id: str) -> str:
    """Deterministic UUID-string point id for *chunk_id* (see module docs)."""
    return str(uuid.uuid5(_POINT_NAMESPACE, chunk_id))


@dataclass(frozen=True)
class IndexReport:
    """What reconciliation actually did (honest counts; Z8).

    On failure, ``added``/``removed`` describe completed operations only,
    ``untouched`` is the remainder not (yet) attempted, and ``error`` is
    the exception class name alone - never a message, path, or content.
    """

    added: int
    removed: int
    untouched: int
    failed: bool
    error: str | None


def reconcile_index(
    conn: sqlite3.Connection,
    store: QdrantLocalStore,
    embedder: Embedder | None,
    *,
    collection: str,
) -> IndexReport:
    """Make *collection* match the persisted chunk set exactly.

    Reads the whole corpus into memory first (one UNBOUNDED ``fetchall``
    over ``chunks`` - see the module docstring's memory-behaviour note; a
    cursor-based read is required before large-corpus indexing). A ``None``
    embedder yields a failed report (``no_embedder``); collection dimension
    mismatches and any vector-store, embedder, or SQLite failure yield a
    failed report with the exception class name. No transaction is opened
    (reconciliation is idempotent and re-runnable, never atomic with
    ingestion).
    """
    if embedder is None:
        return IndexReport(
            added=0, removed=0, untouched=0, failed=True, error="no_embedder"
        )
    try:
        store.ensure_collection(collection)  # creates or validates; raises typed
        stored_ids = store.all_ids(collection)
        rows = conn.execute(
            "SELECT chunk_id, doc_id, content FROM chunks ORDER BY chunk_id"
        ).fetchall()
    except (VectorStoreError, sqlite3.Error) as exc:
        return IndexReport(
            added=0, removed=0, untouched=0, failed=True, error=exc.__class__.__name__
        )
    db_map = {row["chunk_id"]: (row["doc_id"], row["content"]) for row in rows}
    expected_points: dict[str, str] = {
        point_id_for_chunk(chunk_id): chunk_id for chunk_id in db_map
    }
    # key=str: stored ids may mix int and UUID-string (foreign points the
    # adapter legally accepts); ordering by string form is deterministic and
    # cannot raise TypeError on mixed types (remediation F2).
    stale_ids = sorted(stored_ids - set(expected_points), key=str)
    missing_ids = sorted(set(expected_points) - stored_ids, key=str)
    added = 0
    removed = 0
    try:
        if stale_ids:
            store.remove(collection, stale_ids)
            removed = len(stale_ids)
        for start in range(0, len(missing_ids), _ENCODE_BATCH):
            batch = missing_ids[start : start + _ENCODE_BATCH]
            chunk_ids = [expected_points[point_id] for point_id in batch]
            vectors = embedder.encode([db_map[chunk_id][1] for chunk_id in chunk_ids])
            if len(vectors) != len(batch):
                raise DenseIndexError(
                    f"embedder returned {len(vectors)} vectors for {len(batch)} texts"
                )
            store.add(
                collection,
                [
                    Point(
                        id=point_id,
                        vector=vector,
                        payload={
                            "chunk_id": expected_points[point_id],
                            "doc_id": db_map[expected_points[point_id]][0],
                        },
                    )
                    for point_id, vector in zip(batch, vectors, strict=True)
                ],
            )
            added += len(batch)
    except (VectorStoreError, EmbeddingError, sqlite3.Error, DenseIndexError) as exc:
        return IndexReport(
            added=added,
            removed=removed,
            untouched=max(0, len(db_map) - added),
            failed=True,
            error=exc.__class__.__name__,
        )
    return IndexReport(
        added=added,
        removed=removed,
        untouched=max(0, len(db_map) - added),
        failed=False,
        error=None,
    )
