"""API-facing query layer (plan v2.4 Phase 7): request/response schemas and
the ONE shared query orchestration used by both the ``/query`` endpoint and
the CLI.

Design constraints honored here:

- **No second pipeline.** :func:`run_query` composes the existing Phase 5
  ``HybridRetriever`` and the Phase 6 ``answer_query`` orchestration - the
  API and CLI are surfaces over the same canonical ``AnswerResponse``;
  neither surface re-implements retrieval, abstention, or citation logic.
- **No model, no socket (I3).** The retriever is constructed lexical-only
  (``embedder=None, store=None``): FTS5 search over SQLite. Dense retrieval
  requires the MiniLM embedder and vector store, which stay gated behind
  the BG-1a/D3 verdict - the API never loads a model or opens a network
  connection, asserted by the egress suite. ``sentence_transformers`` and
  ``qdrant_client`` are never imported by this module.
- **Bounded input (I7).** ``query`` is a required string (schema
  ``min_length=1``), additionally bounded by the configured
  ``retrieval_query_max_chars`` cap, and the request BODY size is bounded
  before parsing by the content-length guard in ``main.py``. Caps may only
  be tightened by configuration, never raised by the API.
- **Safe errors (I6).** Errors are a fixed envelope
  ``{"error": {"category", "detail"}}`` with category-specific, constant
  detail strings. Raw prompts, retrieved content, filesystem paths,
  exception messages, and tracebacks NEVER appear in an error body -
  unexpected failures log the exception CLASS name only.
- **Truthful outcomes.** Every completed query returns HTTP 200 with the
  canonical response, including retrieval-only and abstention outcomes: a
  ``UNAVAILABLE`` generation result is a valid truthful result, never an
  HTTP 500 (Z11/section 5.4). Only rejected input (4xx) and operational
  storage/internal failures (5xx) are errors.
- **Tracing at the boundary (section 4.2).** Each completed query writes
  one trace via :mod:`rag_platform.tracing` on the same SQLite database.
  Trace persistence is best-effort: a trace failure is logged (class name
  only) and never blocks, alters, or fabricates the response.

Known Phase 7 boundaries (documented, not silently expanded): no
authentication (M2), no cache (M4), no ledger (M3), lexical-only
retrieval at the API (dense awaits BG-1a/D3), and rejected requests are
not traced (full trace completeness belongs to the Phase 8 recorder).
"""

from __future__ import annotations

import dataclasses
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from rag_platform.config import Settings
from rag_platform.db import StorageError, initialize, open_db
from rag_platform.generation import AnswerResponse, answer_query
from rag_platform.observability import Observer, StageEvent
from rag_platform.retrieval import HybridRetriever, QueryValidationError
from rag_platform.tracing import record_query_trace

logger = logging.getLogger("rag_platform")

#: Hard bound on the raw request body for POST /query (I7). The query
#: itself is capped much lower (``retrieval_query_max_chars`` <= 2000);
#: this bound rejects oversized/absurd bodies BEFORE pydantic parses them.
MAX_QUERY_BODY_BYTES = 16 * 1024


def default_db_path(settings: Settings) -> Path:
    """Canonical application database location (under ``data_dir``, on D:).

    Containment is re-enforced by :func:`~rag_platform.db.sqlite.open_db`
    at open time (Z9).
    """
    return settings.data_dir / "sqlite" / "rag.db"


class ApiError(Exception):
    """A mapped, client-safe API failure: fixed envelope, no internals."""

    def __init__(self, status_code: int, category: str, detail: str) -> None:
        super().__init__(category)
        self.status_code = status_code
        self.category = category
        self.detail = detail

    def as_body(self) -> dict[str, dict[str, str]]:
        return {"error": {"category": self.category, "detail": self.detail}}


def translate_query_error(exc: Exception) -> ApiError:
    """Map a pipeline exception to a safe envelope - never echo messages."""
    if isinstance(exc, QueryValidationError):
        return ApiError(
            422,
            "invalid_request",
            "query is invalid for the configured retrieval bounds",
        )
    if isinstance(exc, (StorageError, sqlite3.Error)):
        logger.warning("storage failure during query (class=%s)", type(exc).__name__)
        return ApiError(
            503,
            "storage_unavailable",
            "storage is unavailable; no result was produced",
        )
    logger.warning("unexpected query failure (class=%s)", type(exc).__name__)
    return ApiError(500, "internal_error", "unexpected internal error")


# --- request schema -------------------------------------------------------


class QueryRequest(BaseModel):
    """POST /query body: one required, bounded query string.

    ``extra="forbid"`` makes unrecognized fields a predictable 422 instead
    of a silent ignore - the request means exactly what it says.
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)


# --- response schema (explicit projection of the canonical AnswerResponse) -


class PassageModel(BaseModel):
    """One retrieved passage: citation-grade provenance, relative paths."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    doc_id: str
    ordinal: int
    source_path: str  # project-relative POSIX (never absolute, I16)
    content: str
    char_start: int
    char_end: int
    rank: int
    fused_score: float
    dense_cosine_similarity: float | None
    lexical_bm25: float | None
    injection_quarantined: bool
    injection_finding_count: int


class RetrievalModel(BaseModel):
    """Retrieval report metadata plus its results (the canonical shape)."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["hybrid", "lexical_only"]
    lexical_only_reason: str | None
    outcome: Literal["ok", "no_candidates", "insufficient_evidence"]
    top_k: int
    lexical_hits: int
    dense_hits: int
    fused_candidates: int
    below_floor_excluded: int
    unhydrated_skipped: int
    post_verify_dropped: int
    score_floor: float | None
    results: list[PassageModel]


class GenerationModel(BaseModel):
    """Generation result: provider/model labels, outcome, typed category."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None
    model: str | None
    text: str | None
    outcome: Literal["answered", "abstained", "unavailable"]
    failure_category: str | None
    latency_s: float
    attempts: int


class AbstentionModel(BaseModel):
    """Abstention as a first-class response object (never an error)."""

    model_config = ConfigDict(extra="forbid")

    reason: str
    detail: str


class CitationModel(BaseModel):
    """One validated citation resolved from served evidence."""

    model_config = ConfigDict(extra="forbid")

    index: int
    chunk_id: str
    doc_id: str
    char_start: int
    char_end: int


class QueryResponse(BaseModel):
    """The canonical Phase 6 response shape on the wire, plus the request
    correlation ID (section 4.2)."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    retrieval: RetrievalModel
    passages: list[PassageModel]
    generation: GenerationModel
    abstention: AbstentionModel | None
    citations: list[CitationModel]

    @classmethod
    def from_run(cls, run: "QueryRun") -> "QueryResponse":
        """Project the canonical dataclass response into the wire schema.

        Field-for-field: every ``AnswerResponse``/``RetrievalReport``/
        ``RetrievedChunk`` field appears with the same name and value
        (enums serialized by value) - the API never invents, drops, or
        renames contract data; ``request_id`` is the one additive field.
        """
        response = run.response
        return cls(
            request_id=run.request_id,
            retrieval=RetrievalModel(
                mode=response.retrieval.mode,
                lexical_only_reason=response.retrieval.lexical_only_reason,
                outcome=response.retrieval.outcome,
                top_k=response.retrieval.top_k,
                lexical_hits=response.retrieval.lexical_hits,
                dense_hits=response.retrieval.dense_hits,
                fused_candidates=response.retrieval.fused_candidates,
                below_floor_excluded=response.retrieval.below_floor_excluded,
                unhydrated_skipped=response.retrieval.unhydrated_skipped,
                post_verify_dropped=response.retrieval.post_verify_dropped,
                score_floor=response.retrieval.score_floor,
                results=[
                    PassageModel(**dataclasses.asdict(chunk))
                    for chunk in response.retrieval.results
                ],
            ),
            passages=[
                PassageModel(**dataclasses.asdict(chunk)) for chunk in response.passages
            ],
            generation=GenerationModel(
                provider=response.generation.provider,
                model=response.generation.model,
                text=response.generation.text,
                outcome=response.generation.outcome.value,
                failure_category=(
                    response.generation.failure_category.value
                    if response.generation.failure_category
                    else None
                ),
                latency_s=response.generation.latency_s,
                attempts=response.generation.attempts,
            ),
            abstention=(
                AbstentionModel(
                    reason=response.abstention.reason.value,
                    detail=response.abstention.detail,
                )
                if response.abstention is not None
                else None
            ),
            citations=[
                CitationModel(
                    index=citation.index,
                    chunk_id=citation.chunk_id,
                    doc_id=citation.doc_id,
                    char_start=citation.char_start,
                    char_end=citation.char_end,
                )
                for citation in response.citations
            ],
        )


@dataclass(frozen=True)
class QueryRun:
    """One completed query: the canonical response plus correlation data."""

    response: AnswerResponse
    request_id: str
    total_ms: float


def run_query(
    settings: Settings,
    query: str,
    *,
    db_path: Path,
    request_id: str | None = None,
    observer: Observer | None = None,
) -> QueryRun:
    """Execute one bounded query end to end - the single orchestration
    shared by the API endpoint and the CLI.

    Opens the contained SQLite database (schema initialized idempotently if
    this is its first use - migrations are the trusted Phase 2/4 registry),
    retrieves (lexical-only), runs the Phase 6 generation orchestration,
    writes the best-effort trace on the same database, and closes the
    connection before returning. Raises the typed retrieval/storage
    exceptions untranslated; surfaces map them to safe envelopes/exit codes.

    *observer* (Phase 8, optional): receives the same stage events that
    feed the trace recorder. With the default ``None`` the trace is
    byte-identical to Phase 7 (retrieval timing NULL, three stages). With
    an observer attached (the eval harness), the measured ``retrieval``
    timing and one optional ``context_build`` row are recorded in
    ``trace_stages`` and forwarded to the caller. Observer failures are
    contained: the response and the trace are produced regardless.
    """
    started = time.perf_counter()
    correlation_id = request_id or token_hex(8)  # server-derived (I2)
    stage_events: list[StageEvent] = []

    def _collect_and_forward(event: StageEvent) -> None:
        stage_events.append(event)
        if observer is not None:
            observer(event)

    conn = open_db(db_path)
    try:
        initialize(conn)
        retriever = HybridRetriever(conn, settings=settings, embedder=None, store=None)
        response = answer_query(
            settings,
            retriever,
            query,
            observer=_collect_and_forward if observer is not None else None,
        )
        total_ms = (time.perf_counter() - started) * 1000.0
        try:
            record_query_trace(
                conn,
                request_id=correlation_id,
                query=query,
                response=response,
                total_ms=total_ms,
                stage_events=stage_events,
            )
        except Exception as exc:  # trace persistence is best-effort by policy
            logger.warning(
                "trace write failed (class=%s); response unaffected",
                type(exc).__name__,
            )
        return QueryRun(response=response, request_id=correlation_id, total_ms=total_ms)
    finally:
        conn.close()
