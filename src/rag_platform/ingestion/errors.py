"""Typed, path-safe ingestion errors (plan v2.4 Phase 4; assumption A7,
invariant I16-style safe reporting).

Every failure is an explicit :class:`IngestionError` subclass with a stable
machine-readable ``code``. Messages carry the file *basename* and numeric
facts only - never an absolute path, never document content. Callers report
failures honestly (a failed document is never silently skipped); the
pipeline records the ``code`` per file so the overall report can never
claim success for a document that failed to load.

**Logging invariant (exception chaining):** these errors are raised ``from``
their underlying causes, and those causes can legitimately contain absolute
paths (path-containment rejections, OS-level open/read errors). The visible
message is safe; the *traceback* is not. Ingestion exceptions must therefore
never be logged with ``exc_info=True``/``logger.exception(...)`` or printed
as a traceback - log ``exc.code`` (and optionally ``exc.detail``) as the
pipeline does. Chaining is preserved deliberately for interactive debugging;
if a traceback is ever rendered, its cause frames must be redacted first.
"""

from __future__ import annotations

from typing import ClassVar


class IngestionError(Exception):
    """Base class for deterministic, local ingestion failures."""

    code: ClassVar[str] = "ingestion_error"

    def __init__(self, name: str, detail: str) -> None:
        # ``name`` is always a basename supplied by the loaders module;
        # ``detail`` is a short, path-free, content-free explanation.
        self.file_name = name
        self.detail = detail
        super().__init__(f"{self.code}: {name!r}: {detail}")


class UnsupportedFormatError(IngestionError):
    """Extension is not one of txt/md/markdown/pdf (plan A7: clear error)."""

    code = "unsupported_format"


class EmptyDocumentError(IngestionError):
    """File contains no ingestible text (zero-length or whitespace-only)."""

    code = "empty_document"


class NoExtractableTextError(EmptyDocumentError):
    """A structurally valid PDF yielded no text (no OCR - plan A7)."""

    code = "no_extractable_text"


class MalformedDocumentError(IngestionError):
    """Bytes could not be decoded (text) or parsed (PDF)."""

    code = "malformed_document"


class UnreadableFileError(IngestionError):
    """File is missing, a directory, or raised an OS error on read."""

    code = "unreadable_file"


class FileTooLargeError(IngestionError):
    """File size exceeds the per-file ingest cap.

    Raised by the pre-read ``stat`` check (rejected before any read) and by
    the post-read re-check, which detects and rejects bytes actually read
    over the cap but does not prevent the transient allocation (see
    ``loaders.load_document``).
    """

    code = "file_too_large"


class PathTraversalError(IngestionError):
    """Input path does not resolve strictly under the project root (Z9)."""

    code = "path_traversal"


class CorpusStoreError(IngestionError):
    """Persistence failed; the per-document transaction was rolled back."""

    code = "storage_write"
