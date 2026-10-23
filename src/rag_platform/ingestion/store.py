"""Corpus persistence over the Phase 2 SQLite layer (plan v2.4 Phase 4;
D5 storage, I9/Z9 path policy, I7 bounded writes).

Layout: the corpus tables arrive as migration v2 in the same versioned
registry as the Phase 2 baseline (single contained WAL database under
``data\\sqlite``), and the standalone ``chunk_fts`` index (migration v1) is
populated in the same transaction as the metadata tables, so the keyword
index can never disagree with the corpus.

Atomicity and repeat-ingestion semantics (tested in
``tests/test_ingestion_pipeline.py``):

- One document = one ``BEGIN IMMEDIATE ... COMMIT`` transaction covering
  the document row, all chunk rows, and all FTS rows. Any failure rolls
  the whole document back - no partial document, no FTS/metadata drift.
- Identity is content-derived and stable: ``doc_id = sha256(source_path,
  content_sha256)``; ``chunk_id = sha256(doc_id, ordinal)``. Re-ingesting
  identical content at the same path is a no-op (``unchanged``). Changed
  content at a known path atomically replaces the previous version (the
  old FTS rows and chunks are deleted inside the same transaction).
- Findings JSON is bounded (I7): at most
  :data:`rag_platform.ingestion.injection.MAX_FINDINGS_PER_CHUNK` entries
  plus an explicit ``truncated`` marker.

No embeddings, no retrieval, no generation: Phase 3's wrapper stays lazy
and unused here; this module imports only stdlib SQLite.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rag_platform.config import Settings
from rag_platform.ingestion.errors import CorpusStoreError
from rag_platform.ingestion.injection import MAX_FINDINGS_PER_CHUNK, ScanResult


def default_corpus_db_path(settings: Settings) -> Path:
    """Canonical corpus database location: ``<data_dir>\\sqlite\\rag_platform.db``.

    Relative ``data_dir`` values are already validated by Settings (Z9), so
    the returned path is always under the project root on D:.
    """
    return settings.data_dir / "sqlite" / "rag_platform.db"


def document_id(source_rel_path: str, content_sha256: str) -> str:
    """Stable document identity: content-derived, path-scoped."""
    return hashlib.sha256(
        f"{source_rel_path}\0{content_sha256}".encode("utf-8")
    ).hexdigest()


def chunk_id(doc_id: str, ordinal: int) -> str:
    """Stable chunk identity derived from its document and position."""
    return hashlib.sha256(f"{doc_id}#{ordinal}".encode("utf-8")).hexdigest()


def findings_json(scan: ScanResult) -> str | None:
    """Bounded JSON encoding of a scan result; ``None`` when clean."""
    if not scan.findings:
        return None
    return json.dumps(
        {
            "findings": [
                finding.to_json_dict()
                for finding in scan.findings[:MAX_FINDINGS_PER_CHUNK]
            ],
            "truncated": scan.truncated,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class ChunkInput:
    """One chunk prepared for persistence (content already scanned)."""

    ordinal: int
    content: str
    char_start: int
    char_end: int
    scan: ScanResult


@dataclass(frozen=True)
class UpsertOutcome:
    """Result of persisting one document.

    ``chunk_count`` and ``quarantined_chunks`` always describe what is
    **persisted** - on the ``unchanged`` path they are read back from the
    stored rows rather than recomputed, so they stay correct even when the
    caller's chunking/scanning parameters differ from the ones the document
    was originally written with.
    """

    doc_id: str
    status: Literal["ingested", "unchanged"]
    chunk_count: int
    quarantined_chunks: int


class CorpusStore:
    """Transactional writer/reader for the ingestion corpus tables."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_document(
        self,
        *,
        source_rel_path: str,
        source_name: str,
        file_format: str,
        content_sha256: str,
        size_bytes: int,
        source_mtime: str,
        declared_date: str | None,
        declared_date_status: str,
        ingested_at: str,
        chunks: list[ChunkInput],
    ) -> UpsertOutcome:
        """Persist one document and its chunks atomically.

        Requires a connection with **no open transaction**: this store owns
        transaction control here (``BEGIN IMMEDIATE`` ... ``COMMIT``/
        ``ROLLBACK``), mirroring the migration runner's documented
        precondition - an already-open transaction makes ``BEGIN IMMEDIATE``
        fail loudly instead of silently nesting.

        Raises :class:`CorpusStoreError` (message: basename only) after
        rolling back on any SQLite failure. Documents always carry at least
        one chunk (loaders reject empty documents before this point).
        Returned counts describe persisted rows on both status paths.
        """
        if not chunks:
            raise CorpusStoreError(
                source_name, "refusing to persist a document with zero chunks"
            )
        doc_id = document_id(source_rel_path, content_sha256)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._conn.execute(
                    "SELECT doc_id, content_sha256, chunk_count FROM documents "
                    "WHERE source_path = ?",
                    (source_rel_path,),
                ).fetchone()
                if (
                    existing is not None
                    and existing["content_sha256"] == content_sha256
                ):
                    # No-op write: report what is PERSISTED for this document,
                    # not what the caller's current parameters would recompute
                    # (chunking/scanner config may have changed since ingest).
                    stored_doc_id = existing["doc_id"]
                    stored_quarantined = int(
                        self._conn.execute(
                            "SELECT COUNT(*) AS n FROM chunks "
                            "WHERE doc_id = ? AND injection_quarantined = 1",
                            (stored_doc_id,),
                        ).fetchone()["n"]
                    )
                    self._conn.execute("COMMIT")  # read-only decision, nothing written
                    return UpsertOutcome(
                        doc_id=stored_doc_id,
                        status="unchanged",
                        chunk_count=int(existing["chunk_count"]),
                        quarantined_chunks=stored_quarantined,
                    )
                if existing is not None:
                    self._conn.execute(
                        "DELETE FROM chunk_fts WHERE doc_id = ?", (existing["doc_id"],)
                    )
                    self._conn.execute(
                        "DELETE FROM documents WHERE doc_id = ?", (existing["doc_id"],)
                    )  # chunks cascade
                self._conn.execute(
                    "INSERT INTO documents (doc_id, source_path, source_name, format, "
                    "content_sha256, size_bytes, source_mtime, declared_date, "
                    "declared_date_status, ingested_at, chunk_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        doc_id,
                        source_rel_path,
                        source_name,
                        file_format,
                        content_sha256,
                        size_bytes,
                        source_mtime,
                        declared_date,
                        declared_date_status,
                        ingested_at,
                        len(chunks),
                    ),
                )
                for chunk in chunks:
                    cid = chunk_id(doc_id, chunk.ordinal)
                    quarantined = 1 if chunk.scan.quarantined else 0
                    self._conn.execute(
                        "INSERT INTO chunks (chunk_id, doc_id, ordinal, content, "
                        "char_start, char_end, injection_quarantined, injection_findings) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            cid,
                            doc_id,
                            chunk.ordinal,
                            chunk.content,
                            chunk.char_start,
                            chunk.char_end,
                            quarantined,
                            findings_json(chunk.scan),
                        ),
                    )
                    self._conn.execute(
                        "INSERT INTO chunk_fts (chunk_id, doc_id, content) VALUES (?, ?, ?)",
                        (cid, doc_id, chunk.content),
                    )
                self._conn.execute("COMMIT")  # document + chunks + FTS, atomically
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise
        except sqlite3.Error as exc:
            raise CorpusStoreError(
                source_name, f"database write failed ({exc.__class__.__name__})"
            ) from exc
        return UpsertOutcome(
            doc_id=doc_id,
            status="ingested",
            chunk_count=len(chunks),
            quarantined_chunks=sum(1 for chunk in chunks if chunk.scan.quarantined),
        )

    # --- read helpers (used by tests and later phases; no retrieval logic) ---

    def get_document(self, doc_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()

    def get_document_by_path(self, source_rel_path: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM documents WHERE source_path = ?", (source_rel_path,)
        ).fetchone()

    def get_chunks(self, doc_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM chunks WHERE doc_id = ? ORDER BY ordinal", (doc_id,)
        ).fetchall()

    def counts(self) -> tuple[int, int]:
        """(document count, chunk count) - used for duplicate-prevention tests."""
        documents = self._conn.execute(
            "SELECT COUNT(*) AS n FROM documents"
        ).fetchone()["n"]
        chunks = self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        return int(documents), int(chunks)

    def fts_chunk_ids(self, match: str) -> list[str]:
        """Chunk ids matching a raw FTS5 query (index verification only)."""
        rows = self._conn.execute(
            "SELECT chunk_id FROM chunk_fts WHERE chunk_fts MATCH ?", (match,)
        ).fetchall()
        return [row["chunk_id"] for row in rows]
