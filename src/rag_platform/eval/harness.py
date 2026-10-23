"""Offline evaluation harness (plan v2.4 Phase 8; sections 9.1-9.5, 11).

One run, one isolated database, one report:

1. **Verify** the frozen dataset manifest (fail-closed: schema, per-file
   SHA-256 + size, path confinement beneath the dataset root, no symlinks,
   no traversal, no duplicates, valid labels) before anything is touched.
2. **Protect splits** - tuning runs are refused access to test labels
   before the question file is even parsed.
3. **Ingest** only manifest-listed files into a fresh per-run SQLite
   database under ``<data_dir>/eval_runs/<run_id>/`` (never the
   application database, never ambient files on disk).
4. **Validate gold labels against reality** - every gold chunk ID must
   exist in the ingested database and every gold answer span must fit its
   chunk; a label/ingest mismatch aborts the run (Z8: metrics over labels
   that do not match the corpus would be fabrication).
5. **Query** through the shared :func:`rag_platform.api.run_query` - the
   harness never builds a second pipeline. Request IDs are deterministic
   (``eval:<dataset>:<split>:<qid>``) and safe because each run owns its
   own database.
6. **Score** with the stdlib metrics of :mod:`rag_platform.eval.metrics`
   and emit the JSON + Markdown report of :mod:`rag_platform.eval.report`.

Boundary discipline: no model loading, no network, no provider
activation. The harness refuses to run at all when generation is enabled
in the effective settings - generation-enabled evaluation is a separately
owner-gated future workflow, never this path.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from typing import Literal

from rag_platform.api import run_query
from rag_platform.config import PROJECT_ROOT, Settings
from rag_platform.db import initialize_database
from rag_platform.eval.dataset import (
    DatasetIntegrityError,
    QuestionSet,
    load_and_verify_dataset,
    load_questions,
    require_beneath,
)
from rag_platform.eval.metrics import (
    EvalMetrics,
    QuestionRecord,
    compute_metrics,
    nearest_rank_percentile,
)
from rag_platform.ingestion.pipeline import ingest_file
from rag_platform.observability import StageEvent
from rag_platform.retrieval.errors import LexicalIndexError, QueryValidationError

logger = logging.getLogger(__name__)

#: Documents beyond this count are refused (the frozen corpus is ~20-40;
#: a manifest listing more is a different, unreviewed dataset).
MAX_DOCUMENTS_PER_RUN = 200

#: Per-run databases accumulate under data/eval_runs/ and are never
#: deleted automatically (deletion is the owner's decision); this bound
#: caps the run-id component lengths so directory names stay bounded.
RUN_ID_SANITIZE_CHARS = 64


class EvalConfigError(Exception):
    """The requested evaluation cannot run under the effective configuration.

    Messages carry field names, counts and enum values only - never
    question text, never paths outside the project, never secrets (I16).
    """


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class StageTimingsAggregate:
    """Per-stage timing aggregates from really observed stage events."""

    stage: str
    count: int
    p50_ms: float | None
    p95_ms: float | None


@dataclass(frozen=True)
class EvalRunResult:
    """Everything a report needs; contains no question or answer text."""

    run_id: str
    dataset_id: str
    manifest_digest: str
    question_file_sha256: str
    split: str
    tuning: bool
    top_k: int
    started_at: str
    finished_at: str
    wall_time_s: float
    question_count: int
    ingest_summary: dict[str, int]
    metrics: EvalMetrics
    records: tuple[QuestionRecord, ...]
    served_by_distribution: dict[str, int]
    retrieval_outcome_distribution: dict[str, int]
    generation_outcome_distribution: dict[str, int]
    abstention_reason_distribution: dict[str, int]
    error_classes: dict[str, int]
    stage_timings: tuple[StageTimingsAggregate, ...]
    prior_test_split_evaluations: int
    run_db_path: Path
    config_labels: dict[str, str]


def _percentile_nearest_rank(values: Sequence[float], p: float) -> float | None:
    return nearest_rank_percentile(values, p)


def _verify_gold_labels(conn: sqlite3.Connection, question_set: QuestionSet) -> None:
    """Fail closed when gold labels do not match the ingested chunks."""
    for question in question_set:
        for chunk in question.gold_chunk_ids:
            row = conn.execute(
                "SELECT 1 FROM chunks WHERE chunk_id = ?", (chunk,)
            ).fetchone()
            if row is None:
                raise DatasetIntegrityError(
                    f"question {question.question_id!r}: gold chunk id "
                    f"{chunk[:12]}... is not present in the ingested corpus "
                    "(labels do not match the manifest-listed documents)"
                )
        span = question.gold_answer_span
        if span is not None:
            row = conn.execute(
                "SELECT length(content) FROM chunks WHERE chunk_id = ?",
                (span.chunk_id,),
            ).fetchone()
            if row is None:
                raise DatasetIntegrityError(
                    f"question {question.question_id!r}: gold answer span "
                    "references a chunk that does not exist"
                )
            content_len = int(row[0])
            if span.char_end > content_len:
                raise DatasetIntegrityError(
                    f"question {question.question_id!r}: gold answer span "
                    f"({span.char_start}:{span.char_end}) exceeds its chunk "
                    f"content length ({content_len})"
                )


def _count_prior_test_split_evaluations(eval_runs_dir: Path, dataset_id: str) -> int:
    """Prior test-split runs for this dataset, from recorded run metadata.

    Counted from ``run.json`` records written by earlier runs; when no
    records exist the count is honestly zero (absence is the evidence).
    Deleting ``data/eval_runs/`` resets the count - recorded as a known
    limitation of this evidence source in every report.
    """
    count = 0
    if not eval_runs_dir.is_dir():
        return count
    for run_dir in eval_runs_dir.iterdir():
        meta_path = run_dir / "run.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            continue
        if meta.get("dataset_id") == dataset_id and meta.get("split") == "test":
            count += 1
    return count


def run_eval(
    settings: Settings,
    *,
    dataset_root: Path,
    split: Literal["dev", "test"] = "dev",
    tuning: bool = False,
    top_k: int = 10,
) -> EvalRunResult:
    """Run one deterministic offline evaluation; fail closed on any
    integrity, configuration, or corpus problem.

    *tuning=True* marks a threshold-tuning run: test-split access is
    refused outright (plan section 9.2). *top_k* bounds the served list
    (1..20, the config bound) and is recorded in the report; it defaults
    to 10 so Recall@10/nDCG@10 are meaningful.
    """
    if settings.local_generation_enabled:
        raise EvalConfigError(
            "the evaluation harness runs generation-disabled only; a "
            "generation-enabled eval is a separately owner-gated workflow "
            "and is not this path"
        )
    if top_k < 1 or top_k > 20:
        raise EvalConfigError("top_k must be within 1..20 (config bounds)")

    resolved_root = dataset_root.resolve()
    if not resolved_root.is_relative_to(PROJECT_ROOT):
        raise EvalConfigError("dataset root must resolve beneath the project root (Z9)")

    # 1. Manifest verification (schema, hashes, sizes, confinement).
    manifest = load_and_verify_dataset(resolved_root)
    if len(manifest.document_entries) > MAX_DOCUMENTS_PER_RUN:
        raise EvalConfigError(
            f"manifest lists {len(manifest.document_entries)} documents; the "
            f"bound for one run is {MAX_DOCUMENTS_PER_RUN}"
        )

    # 2. Split protection before any label parsing.
    question_set = load_questions(manifest, split, tuning=tuning)

    # 3. Config sanity: gold labels are chunking-dependent, and every
    #    question must fit the configured query bounds.
    if settings.chunk_size != manifest.chunk_size or (
        settings.chunk_overlap != manifest.chunk_overlap
    ):
        raise EvalConfigError(
            "configured chunk_size/chunk_overlap do not match the manifest's "
            "declared chunking; gold chunk ids would not match the ingested "
            "corpus"
        )
    for question in question_set:
        if len(question.question) > settings.retrieval_query_max_chars:
            raise EvalConfigError(
                f"question {question.question_id!r} exceeds the configured "
                "query character bound"
            )

    eval_settings = settings.model_copy(update={"retrieval_top_k": top_k})

    started_at = _utc_now()
    run_started = time.perf_counter()

    # The dataset-id SEGMENT is the only segment ever shortened, so the
    # 8-hex uniqueness suffix always survives: the per-split id budget is
    # 64 - "eval_"(5) - (1 + len(split) + 1 + stamp(16) + 1 + hex(8)),
    # i.e. 29 for dev and 28 for test. The trailing
    # [:RUN_ID_SANITIZE_CHARS] stays as a provable no-op backstop.
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"_{split}_{stamp}_{token_hex(4)}"
    id_budget = RUN_ID_SANITIZE_CHARS - len("eval_") - len(suffix)
    run_id = f"eval_{manifest.dataset_id[:id_budget]}{suffix}"[:RUN_ID_SANITIZE_CHARS]
    eval_runs_dir = settings.data_dir / "eval_runs"
    prior_test_evaluations = _count_prior_test_split_evaluations(
        eval_runs_dir, manifest.dataset_id
    )
    run_dir = eval_runs_dir / run_id
    run_dir = require_beneath(eval_runs_dir, run_dir, what="evaluation run directory")
    run_db = run_dir / "corpus.db"

    # 4. Ingest only manifest-listed documents into the isolated DB.
    conn = initialize_database(run_db)
    ingest_results = []
    try:
        for entry in sorted(manifest.document_entries, key=lambda e: e.path):
            path = manifest.root.joinpath(*Path(entry.path).parts)
            result = ingest_file(conn, path, settings=settings)
            ingest_results.append(result)
        failures = [r for r in ingest_results if r.status == "failed"]
        unsupported = [r for r in ingest_results if r.status == "skipped_unsupported"]
        if failures or unsupported:
            raise DatasetIntegrityError(
                "manifest-listed documents failed to ingest "
                f"(failed={len(failures)}, unsupported={len(unsupported)}); "
                "run aborted before any query was issued"
            )

        # 5. Gold labels must match the ingested reality.
        _verify_gold_labels(conn, question_set)

        # 6. Query through the shared orchestration; deterministic ids.
        records: list[QuestionRecord] = []
        error_classes: Counter[str] = Counter()
        for question in sorted(question_set, key=lambda q: q.question_id):
            request_id = f"eval:{manifest.dataset_id}:{split}:{question.question_id}"
            events: list[StageEvent] = []
            try:
                run = run_query(
                    eval_settings,
                    question.question,
                    db_path=run_db,
                    request_id=request_id,
                    observer=events.append,
                )
            except (QueryValidationError, LexicalIndexError) as exc:
                # Typed query-level failure: visible in denominators, never
                # silently dropped (recall contribution 0).
                error_classes[type(exc).__name__] += 1
                records.append(
                    QuestionRecord(
                        question_id=question.question_id,
                        question_type=question.type,
                        expected_outcome=question.expected_outcome,
                        gold_chunk_ids=question.gold_chunk_ids,
                        served_chunk_ids=(),
                        retrieval_outcome="error",
                        generation_outcome="unavailable",
                        abstention_reason=None,
                        retrieval_error=True,
                        gold_answer=question.gold_answer,
                    )
                )
                continue
            response = run.response
            served = tuple(chunk.chunk_id for chunk in response.passages)
            citations = response.citations
            citation_valid = 0
            passage_bounds = {
                chunk.chunk_id: (chunk.char_start, chunk.char_end)
                for chunk in response.passages
            }
            for citation in citations:
                bounds = passage_bounds.get(citation.chunk_id)
                if (
                    bounds is not None
                    and bounds[0] <= citation.char_start
                    and citation.char_end <= bounds[1]
                ):
                    citation_valid += 1
            records.append(
                QuestionRecord(
                    question_id=question.question_id,
                    question_type=question.type,
                    expected_outcome=question.expected_outcome,
                    gold_chunk_ids=question.gold_chunk_ids,
                    served_chunk_ids=served,
                    retrieval_outcome=response.retrieval.outcome,
                    generation_outcome=response.generation.outcome.value,
                    abstention_reason=(
                        response.abstention.reason.value
                        if response.abstention is not None
                        else None
                    ),
                    gold_answer=question.gold_answer,
                    predicted_answer=(
                        response.generation.text
                        if response.generation.outcome.value == "answered"
                        else None
                    ),
                    citations_valid=citation_valid if citations else None,
                    citations_total=len(citations) if citations else None,
                    latency_ms=run.total_ms,
                )
            )
    finally:
        # served_by distribution comes from the traces the runs wrote.
        served_by_rows = conn.execute(
            "SELECT served_by, COUNT(*) AS n FROM traces GROUP BY served_by"
        ).fetchall()
        # Per-stage trace timings (plan 7.1 gate 6): the trace is the
        # authoritative per-stage record - observed retrieval + context_build
        # plus boundary-measured generation + response.
        stage_timing_rows = conn.execute(
            "SELECT stage, timing_ms FROM trace_stages WHERE timing_ms IS NOT NULL"
        ).fetchall()
        conn.close()

    finished_at = _utc_now()
    wall_time_s = time.perf_counter() - run_started

    config_labels = {
        "retrieval_mode": "lexical_only",
        "top_k": str(top_k),
        "generation_enabled": "false",
        "generation_model": "none",
        "retrieval_score_floor": (
            "unset"
            if settings.retrieval_score_floor is None
            else str(settings.retrieval_score_floor)
        ),
        "chunk_size": str(manifest.chunk_size),
        "chunk_overlap": str(manifest.chunk_overlap),
        "harness": "rag_platform.eval.harness/1",
    }

    # Durable run record: the evidence base for the running count of
    # test-split evaluations (plan section 9.2). Contains identifiers,
    # splits and counts only - never questions, answers, or paths.
    run_record = {
        "run_id": run_id,
        "dataset_id": manifest.dataset_id,
        "split": split,
        "tuning": tuning,
        "question_count": len(records),
        "started_at": started_at,
        "finished_at": finished_at,
        "manifest_digest": manifest.manifest_digest(),
        "config": dict(config_labels),
    }
    run_json_path = require_beneath(run_dir, run_dir / "run.json", what="run record")
    run_json_path.write_text(
        json.dumps(run_record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    metrics = compute_metrics(records)
    timings_by_stage: dict[str, list[float]] = {}
    for stage, timing_ms in stage_timing_rows:
        timings_by_stage.setdefault(stage, []).append(float(timing_ms))
    stage_aggregates = tuple(
        StageTimingsAggregate(
            stage=stage,
            count=len(timings),
            p50_ms=_percentile_nearest_rank(timings, 0.50),
            p95_ms=_percentile_nearest_rank(timings, 0.95),
        )
        for stage, timings in sorted(timings_by_stage.items())
    )

    ingest_summary = Counter(result.status for result in ingest_results)
    ingest_summary["quarantined_chunks"] = sum(
        result.quarantined_chunks for result in ingest_results
    )

    return EvalRunResult(
        run_id=run_id,
        dataset_id=manifest.dataset_id,
        manifest_digest=manifest.manifest_digest(),
        question_file_sha256=question_set.file_sha256,
        split=split,
        tuning=tuning,
        top_k=top_k,
        started_at=started_at,
        finished_at=finished_at,
        wall_time_s=wall_time_s,
        question_count=len(records),
        ingest_summary=dict(sorted(ingest_summary.items())),
        metrics=metrics,
        records=tuple(records),
        served_by_distribution=dict(
            sorted((row["served_by"], row["n"]) for row in served_by_rows)
        ),
        retrieval_outcome_distribution=dict(
            sorted(Counter(r.retrieval_outcome for r in records).items())
        ),
        generation_outcome_distribution=dict(
            sorted(Counter(r.generation_outcome for r in records).items())
        ),
        abstention_reason_distribution=dict(
            sorted(
                Counter(
                    r.abstention_reason for r in records if r.abstention_reason
                ).items()
            )
        ),
        error_classes=dict(sorted(error_classes.items())),
        stage_timings=stage_aggregates,
        prior_test_split_evaluations=prior_test_evaluations,
        run_db_path=run_db,
        config_labels=config_labels,
    )
