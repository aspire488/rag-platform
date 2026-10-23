"""Command-line surface (plan v2.4 Phase 7: API + CLI, incl. ``report``
skeleton; section 3 "API + CLI only", section 4.2 point 5).

The CLI is a thin renderer over the SAME orchestration the API uses:
``query`` delegates to :func:`rag_platform.api.run_query` - never a second
pipeline - and ``report`` renders the Phase 7 trace rows written by
:mod:`rag_platform.tracing`. Standard-library ``argparse`` only (plan
section 6); no CLI framework, no new dependency.

Invocation (from the project root)::

    .venv\\Scripts\\python.exe -m rag_platform.cli --help
    .venv\\Scripts\\python.exe -m rag_platform.cli query "walrus habitat"
    .venv\\Scripts\\python.exe -m rag_platform.cli query --json "walrus habitat"
    .venv\\Scripts\\python.exe -m rag_platform.cli report runs --limit 5
    .venv\\Scripts\\python.exe -m rag_platform.cli report failures --run <request_id>

Exit codes (documented contract):

- ``0`` - the query completed with a canonical response: answered,
  abstained, AND retrieval-only. Degradation is a truthful outcome, not a
  CLI failure (Z11) - the exit code distinguishes *operational* success
  from invalid input and crashes, never answer quality.
- ``2`` - invalid input: argument errors, empty/whitespace query, query
  over the configured character cap, unknown run id.
- ``3`` - unrecoverable failure: storage unavailable or unexpected
  internal error (class name only, never a traceback).

Safety defaults: default output is a bounded summary WITHOUT passage
content dumps (``--json`` emits the full canonical response, itself
bounded by the retrieval/generation caps); no secrets, no absolute paths,
no tracebacks, and no implicit provider activation - settings come from
the validated configuration, whose shipped default is generation-off
retrieval-only. ``report`` is the Phase 7 SKELETON: it renders recorded
traces; per-class failure attribution, worst-query ranking, and before/
after comparison (section 4.2 point 5) belong to the Phase 8/M3 recorder
and are deliberately not faked here.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import TextIO

from rag_platform.api import (
    QueryResponse,
    default_db_path,
    run_query,
)
from rag_platform.config import PROJECT_ROOT, Settings
from rag_platform.db import StorageError, initialize_database, open_db
from rag_platform.eval import EvalConfigError, run_eval, write_reports
from rag_platform.eval.dataset import (
    DatasetIntegrityError,
    document_paths,
    load_and_verify_dataset,
)
from rag_platform.generation import AnswerResponse
from rag_platform.ingestion.pipeline import ingest_file
from rag_platform.retrieval import QueryValidationError

logger = logging.getLogger("rag_platform")

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_UNRECOVERABLE = 3
#: Evaluation completed and wrote its report, but some per-query errors were
#: recorded (they stay in the metric denominators; the report is still valid).
EXIT_EVAL_ERRORS = 4


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rag-platform",
        description=(
            "Local-first RAG platform CLI. Default configuration is "
            "retrieval-only: no provider is contacted and no model is loaded."
        ),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="application SQLite database path (default: <data_dir>/sqlite/rag.db)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    query_parser = subparsers.add_parser(
        "query",
        help="answer one question through the shared retrieval/generation pipeline",
    )
    query_parser.add_argument("text", help="the question to answer")
    query_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="print the full canonical JSON response (default: bounded summary)",
    )

    report_parser = subparsers.add_parser(
        "report",
        help="render recorded query traces (Phase 7 skeleton: raw trace rows)",
    )
    report_subparsers = report_parser.add_subparsers(
        dest="report_command", required=True
    )
    runs_parser = report_subparsers.add_parser(
        "runs", help="list the most recent traced queries"
    )
    runs_parser.add_argument(
        "--limit", type=int, default=20, help="max rows to print (default 20)"
    )
    failures_parser = report_subparsers.add_parser(
        "failures",
        help="print the recorded stages of one query by request id",
    )
    failures_parser.add_argument(
        "--run",
        required=True,
        dest="run_id",
        help="request id from a /query or CLI query",
    )

    eval_parser = subparsers.add_parser(
        "eval",
        help=(
            "run the offline evaluation harness over a frozen dataset "
            "(generation-disabled; never contacts a provider)"
        ),
        description=(
            "run the offline evaluation harness over a frozen dataset "
            "(generation-disabled; never contacts a provider). The test "
            "split is refused in --tuning mode (plan section 9.2)."
        ),
    )
    eval_parser.add_argument(
        "--dataset",
        required=True,
        type=Path,
        help="dataset root containing manifest.json (beneath the project root)",
    )
    eval_parser.add_argument(
        "--split",
        choices=("dev", "test"),
        default="dev",
        help="question split to evaluate (default: dev)",
    )
    eval_parser.add_argument(
        "--tuning",
        action="store_true",
        help="threshold-tuning mode: refuses access to the test split",
    )
    eval_parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        dest="top_k",
        help="served-list bound, 1..20 (default 10)",
    )
    eval_parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="report output directory (default: <project>/reports)",
    )

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="ingest only a manifest's listed documents into the database (fail-closed)",
        description=(
            "ingest only the manifest-listed documents of a frozen dataset "
            "into the application database; the manifest is fully verified "
            "first and any failure aborts before a byte is ingested"
        ),
    )
    ingest_parser.add_argument(
        "--dataset",
        required=True,
        type=Path,
        help="dataset root containing manifest.json (beneath the project root)",
    )
    return parser


def _summary_text(run_id: str, response: AnswerResponse) -> str:
    """Bounded human-readable summary: outcome first, no content dump."""
    lines = [f"request_id: {run_id}"]
    lines.append(f"retrieval_mode: {response.retrieval.mode}")
    lines.append(f"retrieval_outcome: {response.retrieval.outcome}")
    lines.append(f"passages_served: {len(response.passages)}")
    generation = response.generation
    lines.append(f"generation_outcome: {generation.outcome.value}")
    if generation.failure_category is not None:
        lines.append(f"generation_failure: {generation.failure_category.value}")
    if response.abstention is not None:
        lines.append(f"abstention_reason: {response.abstention.reason.value}")
        lines.append(f"abstention_detail: {response.abstention.detail}")
    if generation.text is not None:
        lines.append(f"answer: {generation.text}")
    for citation in response.citations:
        lines.append(
            f"citation: [C{citation.index}] chunk {citation.chunk_id[:12]} "
            f"chars {citation.char_start}-{citation.char_end}"
        )
    for passage in response.passages:
        lines.append(
            f"passage: rank {passage.rank} chunk {passage.chunk_id[:12]} "
            f"source {passage.source_path} chars {passage.char_start}-{passage.char_end}"
        )
    return "\n".join(lines)


def _run_query_command(
    args: argparse.Namespace,
    settings: Settings,
    db_path: Path,
    stdout: TextIO,
) -> int:
    query = args.text
    if not query.strip():
        print("error: query must not be empty", file=sys.stderr)
        return EXIT_INVALID_INPUT
    if len(query) > settings.retrieval_query_max_chars:
        print(
            "error: query exceeds the configured maximum length "
            f"of {settings.retrieval_query_max_chars} characters",
            file=sys.stderr,
        )
        return EXIT_INVALID_INPUT
    try:
        run = run_query(settings, query, db_path=db_path)
    except QueryValidationError:
        print("error: query is invalid for the configured bounds", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except (StorageError, sqlite3.Error) as exc:
        logger.warning("storage failure in CLI query (class=%s)", type(exc).__name__)
        print("error: storage unavailable; no result was produced", file=sys.stderr)
        return EXIT_UNRECOVERABLE
    except Exception as exc:  # never print a traceback - class name only
        logger.warning("unexpected CLI failure (class=%s)", type(exc).__name__)
        print("error: unexpected internal error", file=sys.stderr)
        return EXIT_UNRECOVERABLE
    if args.as_json:
        payload = QueryResponse.from_run(run).model_dump(mode="json")
        print(json.dumps(payload, indent=2, sort_keys=True), file=stdout)
    else:
        print(_summary_text(run.request_id, run.response), file=stdout)
    return EXIT_OK


def _run_report_command(args: argparse.Namespace, db_path: Path, stdout: TextIO) -> int:
    try:
        conn = open_db(db_path)
    except (StorageError, sqlite3.Error) as exc:
        logger.warning("storage failure in CLI report (class=%s)", type(exc).__name__)
        print("error: storage unavailable; no report was produced", file=sys.stderr)
        return EXIT_UNRECOVERABLE
    try:
        if args.report_command == "runs":
            if args.limit < 1 or args.limit > 200:
                print("error: --limit must be in 1..200", file=sys.stderr)
                return EXIT_INVALID_INPUT
            rows = conn.execute(
                "SELECT request_id, created_at, served_by, degradation_reason, "
                "cache_status FROM traces ORDER BY id DESC LIMIT ?",
                (args.limit,),
            ).fetchall()
            payload = {
                "count": len(rows),
                "traces": [
                    {
                        "request_id": row["request_id"],
                        "created_at": row["created_at"],
                        "served_by": row["served_by"],
                        "degradation_reason": row["degradation_reason"],
                        "cache_status": row["cache_status"],
                    }
                    for row in rows
                ],
            }
        else:  # failures --run <id>
            trace = conn.execute(
                "SELECT id, request_id, created_at, served_by, degradation_reason "
                "FROM traces WHERE request_id = ?",
                (args.run_id,),
            ).fetchone()
            if trace is None:
                print("error: no recorded run with that request id", file=sys.stderr)
                return EXIT_INVALID_INPUT
            stages = conn.execute(
                "SELECT stage, outcome, timing_ms, scores_json FROM trace_stages "
                "WHERE trace_id = ? ORDER BY id",
                (trace["id"],),
            ).fetchall()
            payload = {
                "request_id": trace["request_id"],
                "created_at": trace["created_at"],
                "served_by": trace["served_by"],
                "degradation_reason": trace["degradation_reason"],
                "stages": [
                    {
                        "stage": stage["stage"],
                        "outcome": stage["outcome"],
                        "timing_ms": stage["timing_ms"],
                        "scores": (
                            json.loads(stage["scores_json"])
                            if stage["scores_json"]
                            else None
                        ),
                    }
                    for stage in stages
                ],
                "note": (
                    "Phase 7 skeleton: raw recorded stages. Per-class failure "
                    "attribution and before/after comparison arrive with the "
                    "Phase 8/M3 recorder."
                ),
            }
        print(json.dumps(payload, indent=2, sort_keys=True), file=stdout)
        return EXIT_OK
    except (StorageError, sqlite3.Error) as exc:
        logger.warning("storage failure in CLI report (class=%s)", type(exc).__name__)
        print("error: storage unavailable; no report was produced", file=sys.stderr)
        return EXIT_UNRECOVERABLE
    finally:
        conn.close()


def _project_relative(path: Path) -> str | None:
    """POSIX project-relative display path, or None outside PROJECT_ROOT.

    Pure formatting: never raises, never returns an absolute path (Z8/I16).
    Callers must treat None as a report-path integrity failure (fail closed).
    """
    if not path.is_relative_to(PROJECT_ROOT):
        logger.warning("report path failed project containment")
        return None
    return path.relative_to(PROJECT_ROOT).as_posix()


def _run_eval_command(
    args: argparse.Namespace,
    settings: Settings,
    stdout: TextIO,
) -> int:
    """Run the evaluation harness; emit a bounded summary, no query dumps.

    Exit-code contract (plan section 11): ``0`` clean run; ``2`` invalid
    input (bad split, tuning+test, dataset outside project, top-k out of
    range, generation enabled); ``3`` integrity failure (tamper/hash/gold
    mismatch) or unrecoverable; ``4`` run completed but some per-query
    errors were recorded (report still written).
    """
    # Input-shaped refusals are validated BEFORE the harness so they read
    # as invalid input (2), never as an integrity failure (3).
    if args.tuning and args.split == "test":
        print(
            "error: tuning runs cannot access the test split; tune on dev",
            file=sys.stderr,
        )
        return EXIT_INVALID_INPUT
    if not args.dataset.is_dir():
        print(
            "error: --dataset must be an existing directory beneath the project root",
            file=sys.stderr,
        )
        return EXIT_INVALID_INPUT
    if args.top_k < 1 or args.top_k > 20:
        print("error: --top-k must be within 1..20", file=sys.stderr)
        return EXIT_INVALID_INPUT

    out_dir = args.out if args.out is not None else (PROJECT_ROOT / "reports")
    try:
        resolved_out = out_dir.resolve()
    except OSError:
        print("error: --out cannot be resolved", file=sys.stderr)
        return EXIT_INVALID_INPUT
    if not resolved_out.is_relative_to(PROJECT_ROOT):
        print(
            "error: --out must resolve beneath the project root (D:)",
            file=sys.stderr,
        )
        return EXIT_INVALID_INPUT

    try:
        result = run_eval(
            settings,
            dataset_root=args.dataset,
            split=args.split,
            tuning=args.tuning,
            top_k=args.top_k,
        )
    except EvalConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except DatasetIntegrityError as exc:
        print(f"error: dataset integrity failure: {exc}", file=sys.stderr)
        return EXIT_UNRECOVERABLE
    except Exception as exc:  # never a traceback - class name only
        logger.warning("unexpected eval failure (class=%s)", type(exc).__name__)
        print("error: unexpected internal error during evaluation", file=sys.stderr)
        return EXIT_UNRECOVERABLE

    try:
        json_path, md_path = write_reports(result, resolved_out)
    except DatasetIntegrityError as exc:
        logger.warning("report containment failure (class=%s)", type(exc).__name__)
        print(f"error: dataset integrity failure: {exc}", file=sys.stderr)
        return EXIT_UNRECOVERABLE
    except Exception as exc:
        logger.warning("report write failure (class=%s)", type(exc).__name__)
        print("error: could not write the evaluation report", file=sys.stderr)
        return EXIT_UNRECOVERABLE

    # Bounded summary: identifiers, counts and report paths only - never
    # question text, answers, passages, or absolute paths (Z8/I16).
    report_json = _project_relative(json_path)
    report_markdown = _project_relative(md_path)
    if report_json is None or report_markdown is None:
        print("error: could not write the evaluation report", file=sys.stderr)
        return EXIT_UNRECOVERABLE
    summary = {
        "run_id": result.run_id,
        "dataset_id": result.dataset_id,
        "split": result.split,
        "questions": result.question_count,
        "error_questions": sum(result.error_classes.values()),
        "report_json": report_json,
        "report_markdown": report_markdown,
    }
    print(json.dumps(summary, indent=2, sort_keys=True), file=stdout)
    if result.error_classes:
        return EXIT_EVAL_ERRORS
    return EXIT_OK


def _run_ingest_command(
    args: argparse.Namespace,
    settings: Settings,
    db_path: Path,
    stdout: TextIO,
) -> int:
    """Ingest ONLY the manifest-listed documents of a frozen dataset.

    Fail-closed: the manifest is fully verified (schema, hashes, sizes,
    confinement) before a single byte is ingested, and any failed document
    aborts with an integrity exit code. Ambient files not listed in the
    manifest are never touched.
    """
    if not args.dataset.is_dir():
        print(
            "error: --dataset must be an existing directory beneath the project root",
            file=sys.stderr,
        )
        return EXIT_INVALID_INPUT
    try:
        manifest = load_and_verify_dataset(args.dataset.resolve())
    except DatasetIntegrityError as exc:
        print(f"error: dataset integrity failure: {exc}", file=sys.stderr)
        return EXIT_UNRECOVERABLE
    except Exception as exc:
        logger.warning("manifest failure (class=%s)", type(exc).__name__)
        print("error: unexpected internal error verifying the dataset", file=sys.stderr)
        return EXIT_UNRECOVERABLE

    try:
        conn = initialize_database(db_path)
    except (StorageError, sqlite3.Error) as exc:
        logger.warning("storage failure in CLI ingest (class=%s)", type(exc).__name__)
        print("error: storage unavailable; nothing was ingested", file=sys.stderr)
        return EXIT_UNRECOVERABLE
    counts: Counter[str] = Counter()
    try:
        for doc_path in document_paths(manifest):
            outcome = ingest_file(conn, doc_path, settings=settings)
            counts[outcome.status] += 1
    except (StorageError, sqlite3.Error) as exc:
        logger.warning("storage failure during ingest (class=%s)", type(exc).__name__)
        print("error: storage unavailable; ingest aborted", file=sys.stderr)
        return EXIT_UNRECOVERABLE
    finally:
        conn.close()

    if counts.get("failed", 0):
        print(
            f"error: {counts['failed']} document(s) failed to ingest; "
            "ingest is fail-closed",
            file=sys.stderr,
        )
        return EXIT_UNRECOVERABLE

    summary = {
        "dataset_id": manifest.dataset_id,
        "documents_listed": len(manifest.document_entries),
        "ingested": counts.get("ingested", 0),
        "skipped_unsupported": counts.get("skipped_unsupported", 0),
    }
    print(json.dumps(summary, indent=2, sort_keys=True), file=stdout)
    return EXIT_OK


def main(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    stdout: TextIO | None = None,
) -> int:
    """CLI entry point; returns the documented exit code (never raises for
    expected conditions, never prints a traceback)."""
    out = stdout if stdout is not None else sys.stdout
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse: 0 for -h/--help, 2 for usage errors
        return int(exc.code or 0)
    resolved = settings if settings is not None else Settings()
    db_path = args.db if args.db is not None else default_db_path(resolved)
    if args.command == "query":
        return _run_query_command(args, resolved, db_path, out)
    if args.command == "eval":
        return _run_eval_command(args, resolved, out)
    if args.command == "ingest":
        return _run_ingest_command(args, resolved, db_path, out)
    return _run_report_command(args, db_path, out)


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
