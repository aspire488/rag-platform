"""Ingestion pipeline (plan v2.4 Phase 4): load -> chunk -> scan -> capture
declared date -> persist. Offline, deterministic, and honest about failure.

Per-file outcome model (invariant Z8 honesty; task rule "never silently
skip a failed document"):

- ``ingested``   document written (or replaced) in one transaction.
- ``unchanged``  identical content already present; no write, no duplicate.
- ``failed``     typed error; the report's ``ok`` is False whenever any
  file failed - a failed document can never be reported as success.
- ``skipped_unsupported``  a directory entry whose extension is outside the
  supported set; explicitly reported (never silently dropped).

Boundaries respected here: no embeddings (Phase 3's wrapper is not even
imported), no retrieval, no generation, no network, no services, no model
loading. ``ingested_at`` is injectable for deterministic tests; the
default is the current UTC clock.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from rag_platform.config import Settings
from rag_platform.db.sqlite import PathContainmentError, resolve_under_project
from rag_platform.ingestion.chunker import chunk_text
from rag_platform.ingestion.dates import extract_declared_date
from rag_platform.ingestion.errors import IngestionError, PathTraversalError
from rag_platform.ingestion.injection import scan_text
from rag_platform.ingestion.loaders import SUPPORTED_EXTENSIONS, load_document
from rag_platform.ingestion.store import ChunkInput, CorpusStore

#: Project logger; records carry basenames, codes and counts only - never an
#: absolute path and never document content (I16-style safe logging; the
#: Phase 1 redacting handler provides defense in depth on top of that).
logger = logging.getLogger(__name__)

Status = Literal["ingested", "unchanged", "failed", "skipped_unsupported"]


@dataclass(frozen=True)
class IngestResult:
    """One file's outcome; ``name`` is a basename (never an absolute path)."""

    name: str
    status: Status
    doc_id: str | None = None
    chunk_count: int = 0
    quarantined_chunks: int = 0
    declared_date: str | None = None
    declared_date_status: str | None = None
    error_code: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True)
class IngestionReport:
    """Aggregated, deterministic report for one file or one directory."""

    results: tuple[IngestResult, ...]

    @property
    def ok(self) -> bool:
        """True only when no file failed (Z8: honest classification)."""
        return all(result.status != "failed" for result in self.results)

    def count(self, status: Status) -> int:
        return sum(1 for result in self.results if result.status == status)

    def summary(self) -> dict[str, int]:
        return {
            "ingested": self.count("ingested"),
            "unchanged": self.count("unchanged"),
            "failed": self.count("failed"),
            "skipped_unsupported": self.count("skipped_unsupported"),
            "quarantined_chunks": sum(r.quarantined_chunks for r in self.results),
        }


def _utc_now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def ingest_file(
    conn: sqlite3.Connection,
    path: str | Path,
    *,
    settings: Settings,
    ingested_at: str | None = None,
) -> IngestResult:
    """Ingest one file; failures are recorded as results, never raised."""
    store = CorpusStore(conn)
    candidate = Path(path)
    name = candidate.name
    timestamp = ingested_at if ingested_at is not None else _utc_now_iso()
    try:
        document = load_document(candidate, max_bytes=settings.max_input_file_bytes)
        declared = extract_declared_date(document.text)
        spans = chunk_text(
            document.text, size=settings.chunk_size, overlap=settings.chunk_overlap
        )
        chunk_inputs = [
            ChunkInput(
                ordinal=span.ordinal,
                content=span.content,
                char_start=span.char_start,
                char_end=span.char_end,
                scan=scan_text(span.content),
            )
            for span in spans
        ]
        outcome = store.upsert_document(
            source_rel_path=document.source_rel_path,
            source_name=document.source_name,
            file_format=document.format,
            content_sha256=document.content_sha256,
            size_bytes=document.size_bytes,
            source_mtime=document.source_mtime,
            declared_date=declared.value,
            declared_date_status=declared.status,
            ingested_at=timestamp,
            chunks=chunk_inputs,
        )
    except IngestionError as exc:
        logger.warning("ingestion failed: file=%r code=%s", name, exc.code)
        return IngestResult(
            name=name,
            status="failed",
            error_code=exc.code,
            error_detail=exc.detail,  # path-free, content-free by construction
        )
    # Persisted counts from the store (not recomputed): on the ``unchanged``
    # path they describe the rows already in the database, so a caller whose
    # chunking/scanner settings differ from the original ingest still gets
    # accurate results.
    quarantined = outcome.quarantined_chunks
    if quarantined:
        logger.warning(
            "advisory injection findings (metadata only, content preserved): "
            "file=%r quarantined_chunks=%d",
            name,
            quarantined,
        )
    logger.info(
        "ingestion finished: file=%r status=%s chunks=%d",
        name,
        outcome.status,
        outcome.chunk_count,
    )
    return IngestResult(
        name=name,
        status=outcome.status,
        doc_id=outcome.doc_id,
        chunk_count=outcome.chunk_count,
        quarantined_chunks=quarantined,
        declared_date=declared.value,
        declared_date_status=declared.status,
    )


def ingest_directory(
    conn: sqlite3.Connection,
    directory: str | Path,
    *,
    settings: Settings,
    ingested_at: str | None = None,
) -> IngestionReport:
    """Ingest every supported file in *directory* (flat, sorted, no recursion).

    The directory itself must resolve strictly under the project root (Z9);
    an escaping directory argument raises :class:`PathTraversalError`
    before anything is listed. Entries are processed in sorted basename
    order so reports are deterministic; subdirectories are not descended
    into (no recursive crawling - plan A7 scope).
    """
    candidate = Path(directory)
    try:
        resolved = resolve_under_project(candidate)
    except PathContainmentError as exc:
        # Basename only; filesystem roots have an empty ``name``, so fall back
        # to a fixed safe label rather than ``str(candidate)`` (which would
        # echo an absolute path like "D:\" into the message - I16-style).
        raise PathTraversalError(
            candidate.name or "(corpus)",
            "corpus directory must resolve under the project root",
        ) from exc
    if not resolved.is_dir():
        raise PathTraversalError(candidate.name, "corpus path is not a directory")

    results: list[IngestResult] = []
    for entry in sorted(resolved.iterdir(), key=lambda item: item.name):
        if not entry.is_file():
            continue  # flat corpus: subdirectories are not crawled
        if entry.suffix.lower() not in SUPPORTED_EXTENSIONS:
            results.append(
                IngestResult(
                    name=entry.name,
                    status="skipped_unsupported",
                    error_code="unsupported_format",
                    error_detail="extension is outside the supported set",
                )
            )
            continue
        results.append(
            ingest_file(conn, entry, settings=settings, ingested_at=ingested_at)
        )
    return IngestionReport(results=tuple(results))
