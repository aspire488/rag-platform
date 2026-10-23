"""Request tracing over the existing Phase 2 trace tables (plan v2.4
Phase 7; sections 4.2 design point 1, 5, and invariant I5/I6/I10).

Scope discipline for Phase 7: this is the API/orchestration-boundary
tracer, NOT the full Phase 8 eval trace recorder and NOT the nine-stage
M3 completeness recorder. It writes, per completed ``/query``:

- one ``traces`` row: request ID, UTC timestamp, ``served_by`` (I5),
  ``degradation_reason`` (sanitized category), ``cache_status``;
- three ``trace_stages`` rows for the stages this phase can truthfully
  observe - ``retrieval`` (outcome + bounded score summary), ``generation``
  (outcome, real provider latency, attempt count), and ``response``
  (answered/abstained/unavailable + total boundary-measured duration);

and it deliberately writes NOTHING else:

- ``authn`` / ``authz_filter`` / ``cache_lookup`` / ``post_verify`` /
  ``context_build`` / ``ledger`` stages do not exist yet (M2/M3/M4 and
  Phase 8); inventing outcomes for them would claim unrun work (Z8).
- ``trace_authz`` stays empty until M2 authorization exists to populate
  it; there is no tenant/ACL decision to record before then.
- ``excerpt`` is always NULL (I10: excerpts are authorization-scoped and
  post-verified; with no M2 authorization the only safe value is none).

Sanitization (I6/I10): rows carry digests, enums, counts, timings, and
labels only - never the raw query, never the prompt, never retrieved
content, never filesystem paths, never exception details. ``inputs_digest``
is a SHA-256 of the query (the plan's prescribed digest form, section
4.2), not the query itself.

Failure policy (documented and tested): trace persistence is BEST-EFFORT
and never blocks or mutates a valid response. Any database failure here is
logged with the exception CLASS name only and swallowed by the caller -
a retrieval-only answer stays a retrieval-only answer; a valid response is
never converted into an error, and never into a fabricated success, by a
trace write. This module never claims durable recording: durability is
only what the SQLite commit provides, verified by the tests that read the
rows back.

No schema change: the tables and CHECK constraints are the Phase 2
baseline (``db/schema.sql`` migration v1); this module only INSERTs.

Phase 8 additions (smallest possible; defaults unchanged):

- :func:`record_query_trace` accepts an optional ``stage_events`` sequence
  (:class:`rag_platform.observability.StageEvent`). When *omitted or
  empty*, every INSERT and every value is byte-identical to Phase 7
  (retrieval timing stays NULL; no ``context_build`` row). When provided,
  the measured ``retrieval`` timing is recorded and one optional
  ``context_build`` row is written - the two stages whose durations are
  really measured outside the provider result. Stage-outcome vocabulary
  is frozen in :mod:`rag_platform.observability` (retrieval: ``ok`` /
  ``no_candidates`` / ``insufficient_evidence``; context_build: ``ok`` /
  ``budget_invalid`` / ``budget_exhausted``); the schema's ``outcome``
  column stays CHECK-free per the Phase 2 comment.
- Event payloads are labels/enums/timings only (I6/I10): events carry no
  query text, no prompt, no passage, no path - so passing them through
  cannot leak content into traces.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from rag_platform.generation.types import (
    AnswerResponse,
    GenerationOutcome,
)
from rag_platform.observability import StageEvent


class TraceWriteError(Exception):
    """Trace persistence failed (raised to the boundary, which degrades it)."""


def _digest(text: str) -> str:
    """SHA-256 hex digest - the plan's bounded inputs/outputs digest form."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def served_by(response: AnswerResponse) -> str:
    """I5 ``served_by`` label: the configured provider:model, else the
    retrieval-only label. Configuration facts only - never a URL or path."""
    generation = response.generation
    if generation.provider:
        if generation.model:
            return f"{generation.provider}:{generation.model}"
        return generation.provider
    return "retrieval_only"


def degradation_reason(response: AnswerResponse) -> str | None:
    """Sanitized degradation label (I5): typed category or abstention reason."""
    generation = response.generation
    if (
        generation.outcome is GenerationOutcome.UNAVAILABLE
        and generation.failure_category
    ):
        return generation.failure_category.value
    if response.abstention is not None:
        return response.abstention.reason.value
    return None


def response_outcome(response: AnswerResponse) -> str:
    """One bounded outcome label for the ``response`` stage."""
    if response.abstention is None:
        return "answered"
    if response.generation.outcome is GenerationOutcome.ABSTAINED:
        return "abstained"
    return "unavailable"


def record_query_trace(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    query: str,
    response: AnswerResponse,
    total_ms: float,
    stage_events: Sequence[StageEvent] = (),
) -> None:
    """Persist one trace for a completed query; raise :class:`TraceWriteError`
    on any database failure (the boundary logs the class and continues).

    *query* is used ONLY to compute a digest; it is never stored.
    *total_ms* is the boundary-measured wall time of the request.
    *stage_events* (Phase 8, optional): observed stage completions. With no
    events the write is byte-identical to Phase 7 (retrieval timing NULL,
    three stages). With events, the measured retrieval timing replaces the
    NULL and one ``context_build`` row is added when that stage ran. Events
    for stages already written here (generation/response) are ignored in
    favor of their authoritative sources (provider latency, boundary time).
    """
    report = response.retrieval
    generation = response.generation
    observed = {event.stage: event for event in stage_events}
    retrieval_timing = (
        observed["retrieval"].timing_ms if "retrieval" in observed else None
    )
    try:
        # One transaction per trace: the trace row and its stage rows
        # commit together or not at all (I9).
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute(
                "INSERT INTO traces "
                "(request_id, created_at, tenant_id, served_by, "
                " degradation_reason, cache_status) VALUES (?, ?, NULL, ?, ?, ?)",
                (
                    request_id,
                    _now_iso(),
                    served_by(response),
                    degradation_reason(response),
                    # Honest state: the M4 semantic cache does not exist yet.
                    "not_implemented",
                ),
            )
            trace_id = int(cursor.lastrowid or 0)

            scores_json = json.dumps(
                {
                    "top_k": report.top_k,
                    "lexical_hits": report.lexical_hits,
                    "dense_hits": report.dense_hits,
                    "fused_candidates": report.fused_candidates,
                    "below_floor_excluded": report.below_floor_excluded,
                    "score_floor": report.score_floor,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            # Outputs digest over served chunk IDs only (IDs, never content).
            served_ids = ",".join(chunk.chunk_id for chunk in response.passages)
            conn.execute(
                "INSERT INTO trace_stages "
                "(trace_id, stage, outcome, timing_ms, inputs_digest, "
                " outputs_digest, scores_json, excerpt) "
                "VALUES (?, 'retrieval', ?, ?, NULL, ?, ?, NULL)",
                (
                    trace_id,
                    report.outcome,
                    (
                        max(retrieval_timing, 0.0)
                        if retrieval_timing is not None
                        else None
                    ),
                    _digest(served_ids),
                    scores_json,
                ),
            )
            # Phase 8 optional stage: context_build, only when really observed.
            context_event = observed.get("context_build")
            if context_event is not None:
                conn.execute(
                    "INSERT INTO trace_stages "
                    "(trace_id, stage, outcome, timing_ms, inputs_digest, "
                    " outputs_digest, scores_json, excerpt) "
                    "VALUES (?, 'context_build', ?, ?, NULL, NULL, NULL, NULL)",
                    (
                        trace_id,
                        context_event.outcome,
                        max(context_event.timing_ms, 0.0),
                    ),
                )
            conn.execute(
                "INSERT INTO trace_stages "
                "(trace_id, stage, outcome, timing_ms, inputs_digest, "
                " outputs_digest, scores_json, excerpt) "
                "VALUES (?, 'generation', ?, ?, ?, ?, ?, NULL)",
                (
                    trace_id,
                    generation.outcome.value,
                    max(generation.latency_s, 0.0) * 1000.0,
                    # Generation inputs digest: the served evidence IDs.
                    _digest(served_ids),
                    # Outputs digest: the answer text digest (never the text).
                    _digest(generation.text or ""),
                    json.dumps(
                        {
                            "attempts": generation.attempts,
                            "failure_category": (
                                generation.failure_category.value
                                if generation.failure_category
                                else None
                            ),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            conn.execute(
                "INSERT INTO trace_stages "
                "(trace_id, stage, outcome, timing_ms, inputs_digest, "
                " outputs_digest, scores_json, excerpt) "
                "VALUES (?, 'response', ?, ?, ?, ?, NULL, NULL)",
                (
                    trace_id,
                    response_outcome(response),
                    max(total_ms, 0.0),
                    _digest(query),
                    _digest(f"{request_id}:{response_outcome(response)}"),
                ),
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    except sqlite3.Error as exc:
        raise TraceWriteError(
            f"trace persistence failed ({type(exc).__name__})"
        ) from exc
