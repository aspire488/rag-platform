"""Typed, path-safe retrieval errors (plan v2.4 Phase 5; I16-style reporting).

Messages carry numeric facts and type labels only - never an absolute path,
never query text, never chunk content. Exception chaining (``from exc``) is
preserved for interactive debugging; per the logging invariant documented in
``rag_platform.ingestion.errors``, these exceptions must never be logged with
``exc_info=True``/``logger.exception`` or printed as tracebacks (their causes
may legitimately hold absolute paths).
"""

from __future__ import annotations


class RetrievalError(Exception):
    """Base class for retrieval-stage failures."""


class QueryValidationError(RetrievalError):
    """A query or runtime bound failed a validated limit (I7)."""


class LexicalIndexError(RetrievalError):
    """Unexpected SQLite/FTS5 failure (never swallowed; safe message)."""


class DenseIndexError(RetrievalError):
    """Dense-leg contract violation (e.g. injected embedder output shape)."""


class PostVerifyError(RetrievalError):
    """The post-verification hook broke its results-in/results-out contract.

    Raised when a verifier returns a non-sequence, a non-``RetrievedChunk``
    item (fabrication), or a structurally invalid ``RetrievedChunk``. The
    retriever never returns unvalidated verifier output (remediation F4).
    """
