"""Deterministic document loaders (plan v2.4 Phase 4; assumption A7).

Supported formats - text-based only, no OCR, no network, no archives:

- ``.txt``  plain text
- ``.md`` / ``.markdown``  Markdown (plain text extraction)
- ``.pdf``  text-based PDF via the pinned ``pypdf`` dependency (imported
  lazily so the rest of the package never depends on it; extraction reads
  the file's own bytes only - no externalization, no forms/JS, no images)

Every load is bounded and fail-loud:

- Input paths must resolve strictly under the project root (Z9); traversal
  attempts raise :class:`PathTraversalError` whose message never echoes the
  absolute path (I16-style safe reporting).
- The per-file size cap (``Settings.max_input_file_bytes``; plan v1 section
  6.2 "Ingest file size" 50 MB row) is checked by ``stat`` BEFORE any read
  and re-checked against the bytes actually read, closing the window in
  which the file grows between ``stat`` and read (detection and rejection -
  the excess bytes are read, then refused, before any parsing).
- Text decoding is strict UTF-8 (BOM tolerated); undecodable bytes are a
  typed :class:`MalformedDocumentError`, never silent replacement.
- Empty/whitespace-only documents and PDFs with no extractable text are
  typed errors, not silent skips (A7: clear error, not degradation).
- Error messages contain the file basename and numbers only - never an
  absolute path and never document content.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from rag_platform.config import PROJECT_ROOT
from rag_platform.db.sqlite import PathContainmentError, resolve_under_project
from rag_platform.ingestion.errors import (
    EmptyDocumentError,
    FileTooLargeError,
    MalformedDocumentError,
    NoExtractableTextError,
    PathTraversalError,
    UnreadableFileError,
    UnsupportedFormatError,
)

#: Extension -> canonical format label (single routing table).
SUPPORTED_EXTENSIONS: dict[str, Literal["txt", "md", "pdf"]] = {
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
    ".pdf": "pdf",
}


@dataclass(frozen=True)
class LoadedDocument:
    """One successfully extracted document - untrusted text plus provenance."""

    text: str
    format: Literal["txt", "md", "pdf"]
    size_bytes: int
    content_sha256: str  # sha256 of the extracted UTF-8 text
    source_rel_path: str  # POSIX-style, relative to PROJECT_ROOT
    source_name: str  # basename only (safe for logs/errors)
    source_mtime: str  # ISO-8601 UTC file metadata - never a declared date


def _utc_iso(timestamp: float) -> str:
    return (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _safe_name(path: Path) -> str:
    return path.name


def load_document(path: str | Path, *, max_bytes: int) -> LoadedDocument:
    """Load one file into a :class:`LoadedDocument` or raise a typed error.

    *max_bytes* is the per-file cap enforced by ``stat`` before the file is
    ever read into memory.
    """
    candidate = Path(path)
    name = _safe_name(candidate)
    try:
        resolved = resolve_under_project(candidate)
    except PathContainmentError as exc:
        # Never echo the absolute path (I16-style); basename only.
        raise PathTraversalError(
            name, "input path must resolve under the project root"
        ) from exc

    extension = resolved.suffix.lower()
    file_format = SUPPORTED_EXTENSIONS.get(extension)
    if file_format is None:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise UnsupportedFormatError(
            name,
            f"extension {extension or '(none)'!r} is not supported (supported: {supported})",
        )

    try:
        stat = resolved.stat()
    except OSError as exc:
        raise UnreadableFileError(
            name, f"cannot stat file ({exc.__class__.__name__})"
        ) from exc
    if resolved.is_dir():
        raise UnreadableFileError(name, "input path is a directory, not a file")
    if stat.st_size > max_bytes:
        raise FileTooLargeError(
            name,
            f"file is {stat.st_size} bytes which exceeds the per-file ingest cap "
            f"of {max_bytes} bytes; rejected before reading",
        )

    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise UnreadableFileError(
            name, f"cannot read file ({exc.__class__.__name__})"
        ) from exc

    # Time-of-check/time-of-use re-check: the file may have grown between the
    # pre-read ``stat`` above and this read. This DETECTS and rejects an
    # over-read before any decoding or parsing - it does not prevent the
    # transient allocation (only a same-handle bounded read could). Message
    # carries the basename and byte counts only (I16-style).
    if len(raw) > max_bytes:
        raise FileTooLargeError(
            name,
            f"file changed size during read: {len(raw)} bytes exceeds the "
            f"per-file ingest cap of {max_bytes} bytes; rejected before parsing",
        )

    if file_format == "pdf":
        text = _extract_pdf_text(raw, name)
    else:
        text = _decode_text(raw, name)

    if not text.strip():
        if file_format == "pdf":
            raise NoExtractableTextError(
                name, "PDF contains no extractable text (no OCR is performed)"
            )
        raise EmptyDocumentError(
            name, "document contains no text (empty or whitespace-only)"
        )

    return LoadedDocument(
        text=text,
        format=file_format,
        size_bytes=stat.st_size,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        source_rel_path=resolved.relative_to(PROJECT_ROOT).as_posix(),
        source_name=name,
        source_mtime=_utc_iso(stat.st_mtime),
    )


def _decode_text(raw: bytes, name: str) -> str:
    """Strict UTF-8 decode with BOM tolerance; undecodable bytes fail loud."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MalformedDocumentError(
            name,
            f"file is not valid UTF-8 text (invalid byte at offset {exc.start}; "
            "no encoding guessing is performed)",
        ) from exc


def _extract_pdf_text(raw: bytes, name: str) -> str:
    """Extract text from a text-based PDF's own bytes; no OCR, no network."""
    try:
        from pypdf import PdfReader  # lazy: only PDF loads need pypdf
    except ImportError as exc:  # pragma: no cover - pypdf is a pinned dependency
        raise MalformedDocumentError(
            name, "pypdf is not installed; PDF support unavailable"
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(raw))
        page_texts = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # pypdf raises a wide exception family; fail typed
        raise MalformedDocumentError(
            name, f"PDF could not be parsed ({exc.__class__.__name__})"
        ) from exc
    return "\n\n".join(text.strip() for text in page_texts if text.strip())
