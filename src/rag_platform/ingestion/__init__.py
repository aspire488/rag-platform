"""Ingestion package (plan v2.4 Phase 4): document loaders, bounded
sentence-aware chunking, prompt-injection scanning, declared-date capture,
and transactional corpus persistence under the project root on D:.

Scope discipline (plan section 15, Phase 4 row): this package builds the
corpus only. It performs no embedding inference (Phase 3's wrapper stays
lazy and unused), no retrieval or RRF (Phase 5), no generation (Phase 6),
and no network access of any kind. All extracted content is treated as
untrusted data; injection findings are advisory metadata, never
authorization decisions (invariants I8/I11).
"""

from __future__ import annotations

from rag_platform.ingestion.chunker import Chunk, chunk_text
from rag_platform.ingestion.dates import DeclaredDate, extract_declared_date
from rag_platform.ingestion.errors import (
    CorpusStoreError,
    EmptyDocumentError,
    FileTooLargeError,
    IngestionError,
    MalformedDocumentError,
    NoExtractableTextError,
    PathTraversalError,
    UnreadableFileError,
    UnsupportedFormatError,
)
from rag_platform.ingestion.injection import (
    InjectionFinding,
    ScanResult,
    scan_text,
)
from rag_platform.ingestion.loaders import LoadedDocument, load_document
from rag_platform.ingestion.pipeline import (
    IngestResult,
    IngestionReport,
    ingest_directory,
    ingest_file,
)
from rag_platform.ingestion.store import (
    ChunkInput,
    CorpusStore,
    UpsertOutcome,
    chunk_id,
    document_id,
)

__all__ = [
    "Chunk",
    "ChunkInput",
    "CorpusStore",
    "CorpusStoreError",
    "DeclaredDate",
    "EmptyDocumentError",
    "FileTooLargeError",
    "InjectionFinding",
    "IngestResult",
    "IngestionError",
    "IngestionReport",
    "LoadedDocument",
    "MalformedDocumentError",
    "NoExtractableTextError",
    "PathTraversalError",
    "ScanResult",
    "UnreadableFileError",
    "UnsupportedFormatError",
    "UpsertOutcome",
    "chunk_id",
    "chunk_text",
    "document_id",
    "extract_declared_date",
    "ingest_directory",
    "ingest_file",
    "load_document",
    "scan_text",
]
