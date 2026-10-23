"""Typed, content-safe generation errors (plan v2.4 Phase 6).

Messages carry type labels and numeric facts only - never prompt text,
never document content, never source paths, never credentials or headers,
never exception tracebacks (I6/I15/I16-style reporting, mirroring the
``retrieval`` and ``ingestion`` error contracts). Exception chaining is
preserved for interactive debugging, but per the project-wide logging
invariant these exceptions must never be logged with
``exc_info=True``/``logger.exception`` or printed as tracebacks.
"""

from __future__ import annotations


class GenerationError(Exception):
    """Base class for generation-stage failures."""


class GenerationRequestError(GenerationError):
    """A generation request/response contract bound failed validation (I7)."""


class ContextBudgetError(GenerationError):
    """Context construction received an invalid or out-of-bound budget."""
