"""Phase 5 indexer tests: reconciliation against the persisted corpus -
first run adds everything, an unchanged corpus embeds nothing, replaced
content leaves no stale points, and every failure is reported honestly
(partial runs never claim full success).

Offline and deterministic: contained SQLite + local Qdrant under
``data/tmp``; counting fake embedders only - no model is ever loaded.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from rag_platform.db import initialize_database
from rag_platform.ingestion.chunker import chunk_text
from rag_platform.ingestion.injection import scan_text
from rag_platform.ingestion.store import ChunkInput, CorpusStore
from rag_platform.retrieval.indexer import point_id_for_chunk, reconcile_index
from rag_platform.vectorstore.qdrant_local import QdrantLocalStore

_DIM = 4


class CountingEmbedder:
    """Deterministic hash-derived vectors; counts batches and texts."""

    def __init__(self, *, fail: bool = False) -> None:
        self.encode_calls = 0
        self.texts_embedded = 0
        self._fail = fail

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        self.encode_calls += 1
        if self._fail:
            from rag_platform.embeddings.minilm import EmbeddingError

            raise EmbeddingError("deterministic fake failure (no model involved)")
        self.texts_embedded += len(texts)
        return [_vector(text) for text in texts]

    @property
    def is_loaded(self) -> bool:
        return not self._fail


def _vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [digest[i] / 255.0 for i in range(_DIM)]


@pytest.fixture
def corpus_db(storage_tmp: Path) -> Iterator[sqlite3.Connection]:
    conn = initialize_database(storage_tmp / "corpus.db")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def store(storage_tmp: Path) -> Iterator[QdrantLocalStore]:
    local = QdrantLocalStore(storage_tmp / "vectors", vector_size=_DIM)
    try:
        yield local
    finally:
        local.close()


def _ingest(
    conn: sqlite3.Connection,
    rel_path: str,
    text: str,
    *,
    size: int = 40,
    overlap: int = 5,
) -> None:
    chunks = [
        ChunkInput(
            ordinal=chunk.ordinal,
            content=chunk.content,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            scan=scan_text(chunk.content),
        )
        for chunk in chunk_text(text, size=size, overlap=overlap)
    ]
    CorpusStore(conn).upsert_document(
        source_rel_path=rel_path,
        source_name=Path(rel_path).name,
        file_format="txt",
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        size_bytes=len(text.encode("utf-8")),
        source_mtime="2026-10-19T12:00:00Z",
        declared_date=None,
        declared_date_status="missing",
        ingested_at="2026-10-19T12:00:00Z",
        chunks=chunks,
    )


def _db_chunk_ids(conn: sqlite3.Connection) -> set[str]:
    return {
        row["chunk_id"]
        for row in conn.execute("SELECT chunk_id FROM chunks").fetchall()
    }


def test_point_id_is_a_stable_uuid_of_the_chunk_id() -> None:
    first = point_id_for_chunk("a" * 64)
    assert first == point_id_for_chunk("a" * 64)
    assert first != point_id_for_chunk("b" * 64)
    uuid.UUID(first)  # Qdrant/adapter accept only UUID strings


def test_first_run_adds_every_chunk(
    corpus_db: sqlite3.Connection, store: QdrantLocalStore
) -> None:
    _ingest(corpus_db, "a.txt", "The walrus swims past the ice floe. " * 2)
    embedder = CountingEmbedder()
    report = reconcile_index(corpus_db, store, embedder, collection="chunks")
    assert report.failed is False and report.error is None
    expected = {point_id_for_chunk(cid) for cid in _db_chunk_ids(corpus_db)}
    assert report.added == len(expected) >= 2
    assert store.all_ids("chunks") == expected
    assert embedder.texts_embedded == report.added


def test_unchanged_corpus_embeds_nothing(
    corpus_db: sqlite3.Connection, store: QdrantLocalStore
) -> None:
    _ingest(corpus_db, "a.txt", "The walrus swims past the ice floe. " * 2)
    embedder = CountingEmbedder()
    first = reconcile_index(corpus_db, store, embedder, collection="chunks")
    calls_after_first = embedder.encode_calls
    second = reconcile_index(corpus_db, store, embedder, collection="chunks")
    assert (second.added, second.removed) == (0, 0)
    assert second.untouched == first.added
    assert second.failed is False
    assert embedder.encode_calls == calls_after_first  # zero embedding work


def test_replacement_removes_stale_points(
    corpus_db: sqlite3.Connection, store: QdrantLocalStore
) -> None:
    _ingest(corpus_db, "a.txt", "The walrus swims past the ice floe. " * 3)
    embedder = CountingEmbedder()
    reconcile_index(corpus_db, store, embedder, collection="chunks")
    stale = store.all_ids("chunks")
    assert len(stale) >= 2
    # Same path, new content -> every chunk id changes (content-derived).
    _ingest(corpus_db, "a.txt", "Elephants wander the savanna at dawn.")
    report = reconcile_index(corpus_db, store, embedder, collection="chunks")
    assert report.failed is False
    assert report.removed == len(stale)
    assert store.all_ids("chunks") == {
        point_id_for_chunk(cid) for cid in _db_chunk_ids(corpus_db)
    }
    assert not (store.all_ids("chunks") & stale)


def test_no_embedder_fails_honestly(
    corpus_db: sqlite3.Connection, store: QdrantLocalStore
) -> None:
    report = reconcile_index(corpus_db, store, None, collection="chunks")
    assert report == type(report)(
        added=0, removed=0, untouched=0, failed=True, error="no_embedder"
    )


def test_embedder_failure_is_reported_not_claimed(
    corpus_db: sqlite3.Connection, store: QdrantLocalStore
) -> None:
    _ingest(corpus_db, "a.txt", "The walrus swims past the ice floe. " * 2)
    total = len(_db_chunk_ids(corpus_db))
    report = reconcile_index(
        corpus_db, store, CountingEmbedder(fail=True), collection="chunks"
    )
    assert report.failed is True
    assert report.error == "EmbeddingError"
    assert report.added == 0
    assert report.untouched == total  # honest: nothing was indexed


def test_dimension_mismatch_fails_honestly(
    corpus_db: sqlite3.Connection,
    storage_tmp: Path,
) -> None:
    _ingest(corpus_db, "a.txt", "The walrus swims past the ice floe.")

    class WrongDimEmbedder:
        def encode(self, texts: Sequence[str]) -> list[list[float]]:
            return [[0.1, 0.2, 0.3] for _ in texts]  # store expects 4

        @property
        def is_loaded(self) -> bool:
            return True

    with QdrantLocalStore(storage_tmp / "other-vectors", vector_size=_DIM) as other:
        report = reconcile_index(corpus_db, other, WrongDimEmbedder(), collection="c")
        assert report.failed is True
        assert report.error == "InvalidPointError"
        assert report.added == 0


def test_mixed_foreign_int_ids_are_removed_without_type_error(
    corpus_db: sqlite3.Connection, store: QdrantLocalStore
) -> None:
    # Remediation F2: the adapter legally accepts non-negative int point
    # ids from another writer; a mixed int/UUID-string id set must not
    # crash stale-id ordering (raw TypeError) and must still produce the
    # exact-corpus end state the contract promises (foreign points are
    # stale by definition: not persisted chunk ids).
    from rag_platform.vectorstore.qdrant_local import Point

    _ingest(corpus_db, "a.txt", "The walrus swims past the ice floe. " * 3)
    embedder = CountingEmbedder()
    reconcile_index(corpus_db, store, embedder, collection="chunks")
    uuid_ids = store.all_ids("chunks")
    assert len(uuid_ids) >= 2
    store.add(
        "chunks",
        [
            Point(
                id=12345,  # foreign non-negative int id (legal for the adapter)
                vector=[0.5] * _DIM,
                payload={"chunk_id": "foreign", "doc_id": "foreign-doc"},
            )
        ],
    )
    assert 12345 in store.all_ids("chunks")
    # Replace the content: stale UUID-string ids AND the foreign int id now
    # coexist in the removal set - previously an uncaught sorted() TypeError.
    _ingest(corpus_db, "a.txt", "Elephants wander the savanna at dawn.")
    report = reconcile_index(corpus_db, store, embedder, collection="chunks")
    assert report.failed is False and report.error is None
    assert report.removed == len(uuid_ids) + 1  # stale chunks + the foreign point
    assert store.all_ids("chunks") == {
        point_id_for_chunk(cid) for cid in _db_chunk_ids(corpus_db)
    }
    assert 12345 not in store.all_ids("chunks")
