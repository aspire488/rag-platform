"""Phase 8 observer seam and trace-enrichment tests (plan v2.4 sections
7.1 gate 6, 8.3, 15 Phase 8 row).

The observer is the ONLY addition the evaluation harness makes to the
shared orchestration. These tests pin its contract:

- default (``observer=None``): the trace keeps its exact Phase 7 shape -
  three stage rows (``retrieval``/``generation``/``response``), retrieval
  ``timing_ms`` NULL, no ``context_build`` row;
- with an observer: the measured ``retrieval`` timing and the optional
  ``context_build`` row reach BOTH the caller's callback and the
  ``trace_stages`` table (regression test for the missing
  ``stage_events`` wiring - the trace must record what the pipeline
  observed);
- a raising observer is contained: the response and the enriched trace
  are produced regardless;
- observed stages stay inside the frozen outcome vocabulary
  (retrieval: ``ok``/``no_candidates``/``insufficient_evidence``;
  context_build: ``ok``/``budget_invalid``/``budget_exhausted``);
- a retrieval-level abstention honestly has NO ``context_build`` event.

Offline: contained SQLite under ``data/tmp`` (D:), no network, no model.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from rag_platform.api import QueryRun, run_query
from rag_platform.config import Settings
from rag_platform.db import initialize_database
from rag_platform.ingestion.injection import scan_text
from rag_platform.ingestion.store import ChunkInput, CorpusStore
from rag_platform.observability import (
    CONTEXT_BUILD_OUTCOMES,
    RETRIEVAL_OUTCOMES,
    StageEvent,
)

_WALRUS_DOC = "The walrus swims slowly past the ice floe."


def _seed_corpus(db_path: Path) -> None:
    conn = initialize_database(db_path)
    try:
        text = _WALRUS_DOC
        CorpusStore(conn).upsert_document(
            source_rel_path="a.txt",
            source_name="a.txt",
            file_format="txt",
            content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            size_bytes=len(text.encode("utf-8")),
            source_mtime="2026-10-19T12:00:00Z",
            declared_date=None,
            declared_date_status="missing",
            ingested_at="2026-10-19T12:00:00Z",
            chunks=[
                ChunkInput(
                    ordinal=0,
                    content=text,
                    char_start=0,
                    char_end=len(text),
                    scan=scan_text(text),
                )
            ],
        )
    finally:
        conn.close()


def _stages(db_path: Path, request_id: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        trace = conn.execute(
            "SELECT id FROM traces WHERE request_id = ?", (request_id,)
        ).fetchone()
        assert trace is not None, f"no trace written for {request_id}"
        return conn.execute(
            "SELECT stage, outcome, timing_ms, excerpt FROM trace_stages "
            "WHERE trace_id = ? ORDER BY id",
            (trace["id"],),
        ).fetchall()
    finally:
        conn.close()


def _run(
    storage_tmp: Path,
    query: str,
    *,
    observer=None,
    request_id: str = "obs-test",  # noqa: ANN001
) -> tuple[QueryRun, Path]:
    db_path = storage_tmp / "observer-corpus.db"
    if not db_path.exists():
        _seed_corpus(db_path)
    settings = Settings(_env_file=None)
    run = run_query(
        settings,
        query,
        db_path=db_path,
        request_id=request_id,
        observer=observer,
    )
    return run, db_path


# --- default: Phase 7 byte-identical trace -----------------------------------


def test_default_query_without_observer_keeps_phase7_trace_shape(
    storage_tmp: Path,
) -> None:
    run, db_path = _run(storage_tmp, "walrus", request_id="obs-default")
    assert isinstance(run, QueryRun)
    assert run.response.retrieval.outcome == "ok"

    stages = _stages(db_path, "obs-default")
    # Three stages only: the optional context_build row is observer-only.
    assert [row["stage"] for row in stages] == [
        "retrieval",
        "generation",
        "response",
    ]
    # No measured retrieval timing without an observer (Phase 7 shape),
    # while the response boundary timing still exists.
    assert stages[0]["timing_ms"] is None
    assert stages[2]["timing_ms"] is not None
    assert stages[2]["timing_ms"] >= 0.0


# --- observer attached: enriched trace + forwarded events --------------------


def test_observer_receives_events_and_trace_records_the_same_timings(
    storage_tmp: Path,
) -> None:
    events: list[StageEvent] = []
    run, db_path = _run(
        storage_tmp, "walrus", observer=events.append, request_id="obs-rich"
    )
    assert run.response.retrieval.outcome == "ok"

    # The caller saw one event per observed stage, in pipeline order.
    assert [(e.stage, e.outcome) for e in events] == [
        ("retrieval", "ok"),
        ("context_build", "ok"),
    ]
    assert all(e.timing_ms >= 0.0 for e in events)

    # ...and the trace carries the SAME stages with REAL timings - this is
    # the regression test for the missing stage_events wiring: retrieval
    # must no longer be NULL and the context_build row must exist.
    stages = _stages(db_path, "obs-rich")
    assert [row["stage"] for row in stages] == [
        "retrieval",
        "context_build",
        "generation",
        "response",
    ]
    assert stages[0]["timing_ms"] is not None
    assert stages[1]["timing_ms"] is not None
    assert stages[0]["timing_ms"] >= 0.0
    assert stages[1]["timing_ms"] >= 0.0
    assert stages[0]["outcome"] == "ok"
    assert stages[1]["outcome"] == "ok"

    # Observer events and trace enrichment agree exactly (stage/outcome/
    # timing): one source of truth, recorded twice for two readers.
    traced_observed = [
        (row["stage"], row["outcome"], row["timing_ms"])
        for row in stages
        if row["stage"] in {"retrieval", "context_build"}
    ]
    assert traced_observed == [
        (e.stage, e.outcome, pytest.approx(e.timing_ms)) for e in events
    ]


def test_observed_outcomes_stay_inside_the_frozen_vocabulary(
    storage_tmp: Path,
) -> None:
    events: list[StageEvent] = []
    _run(storage_tmp, "walrus", observer=events.append, request_id="obs-vocab")
    assert events, "expected observed stages"
    for event in events:
        assert event.stage in {"retrieval", "context_build"}
        if event.stage == "retrieval":
            assert event.outcome in RETRIEVAL_OUTCOMES
        else:
            assert event.outcome in CONTEXT_BUILD_OUTCOMES


def test_raising_observer_is_contained_and_trace_still_enriched(
    storage_tmp: Path,
) -> None:
    def exploding(event: StageEvent) -> None:
        raise RuntimeError(f"observer boom for {event.stage}")

    run, db_path = _run(
        storage_tmp, "walrus", observer=exploding, request_id="obs-boom"
    )
    # The response is produced normally - the observer cannot break a run.
    assert run.response.retrieval.outcome == "ok"
    assert run.response.generation.text is None  # still generation-disabled

    # The events were collected BEFORE forwarding, so the trace keeps its
    # enrichment even when the callback raises every time.
    stages = _stages(db_path, "obs-boom")
    assert [row["stage"] for row in stages] == [
        "retrieval",
        "context_build",
        "generation",
        "response",
    ]
    assert stages[0]["timing_ms"] is not None
    assert stages[1]["timing_ms"] is not None


# --- retrieval-level abstention: honestly no context_build --------------------


def test_no_candidates_query_observable_without_context_build(
    storage_tmp: Path,
) -> None:
    events: list[StageEvent] = []
    run, db_path = _run(
        storage_tmp,
        "zzqxw rpqtz vjkm blarn",
        observer=events.append,
        request_id="obs-abstain",
    )
    # Evidence abstention, not generation state.
    assert run.response.retrieval.outcome == "no_candidates"
    assert run.response.abstention is not None
    assert run.response.abstention.reason.value == "no_candidates"

    # Only the retrieval stage ran: no context assembly happened, so no
    # context_build event is observed and none is recorded.
    assert [(e.stage, e.outcome) for e in events] == [("retrieval", "no_candidates")]
    stages = _stages(db_path, "obs-abstain")
    assert [row["stage"] for row in stages] == [
        "retrieval",
        "generation",
        "response",
    ]
    assert stages[0]["outcome"] == "no_candidates"
    assert stages[0]["timing_ms"] is not None  # measured, even for abstention
    assert all(row["excerpt"] is None for row in stages)


# --- scores_json stays a counters-only envelope ------------------------------


def test_retrieval_scores_json_is_counters_only(storage_tmp: Path) -> None:
    events: list[StageEvent] = []  # noqa: F841 - enrichment on for full path
    _, db_path = _run(
        storage_tmp, "walrus", observer=events.append, request_id="obs-scores"
    )
    conn = sqlite3.connect(db_path)
    try:
        (payload,) = conn.execute(
            "SELECT scores_json FROM trace_stages WHERE stage = 'retrieval'"
        ).fetchone()
    finally:
        conn.close()
    scores = json.loads(payload)
    assert set(scores) == {
        "top_k",
        "lexical_hits",
        "dense_hits",
        "fused_candidates",
        "below_floor_excluded",
        "score_floor",
    }
