"""Evaluation report generation (plan v2.4 Phase 8; sections 9.4-9.5).

Two artifacts per run, written under the gitignored ``reports/`` directory:

- ``eval_<dataset>_<split>_<config>_<timestamp>.json`` - machine-readable,
  split into a **deterministic** section (corpus identity, metric values,
  outcome distributions, counts - byte-identical for the same corpus and
  code) and a **volatile** section (run id, wall clock, latency, prior
  test-split count). Determinism is a property this module promises and
  the test-suite verifies.
- ``eval_<dataset>_<split>_<config>_<timestamp>.md`` - the same content
  as a human-readable Markdown summary.

Honesty rules enforced here (Z8/I7):

- Every metric row renders ``value | status``; ``not_measured`` renders as
  the literal string ``not_measured``, never as 0 or 100.
- ``generation_unavailable`` (the default lexical-only outcome) is never
  conflated with a correct abstention: the outcome distribution and the
  abstention-reason distribution are printed separately.
- The known-metric limitations of plan section 9.4 are printed in every
  report header - the report never reads as a bare score.
- Payloads carry question IDs only - no question text, no answers, no
  chunk text, no paths beyond the run database's project-relative name.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from rag_platform.config import PROJECT_ROOT
from rag_platform.eval.dataset import require_beneath
from rag_platform.eval.harness import EvalRunResult
from rag_platform.eval.metrics import MetricValue

#: Plan section 9.4: known-metric limitations printed in every header.
KNOWN_LIMITATIONS: tuple[str, ...] = (
    "Recall/precision/MRR/nDCG measure retrieval overlap with gold chunk "
    "IDs only; they do not measure answer correctness.",
    "Token-F1 is lexical overlap with gold answer spans; it is "
    "not_measured until a generation-enabled run is authorized.",
    "Citation validity counts citations whose span resolves inside a "
    "served chunk; it does not verify that the cited span supports the "
    "claim.",
    "Abstention correctness is judged against authored expectations in "
    "the frozen corpus; it is not a human preference judgment.",
    "Latency p50/p95 are nearest-rank percentiles from a single machine; "
    "no cross-machine generalization is claimed.",
    "M1 has no frozen numeric thresholds (plan section 7.1): a measured "
    "value is evidence, not a pass - threshold decisions are the owner's.",
)

#: Plan section 9.5: the lockfile hash pins the environment a run ran in.
_LOCKFILE = PROJECT_ROOT / "uv.lock"


def _lockfile_sha256() -> str:
    if not _LOCKFILE.is_file():
        return "not_available"
    digest = hashlib.sha256()
    with _LOCKFILE.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def _metric_rows(metrics: object) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for field_name in metrics.__dataclass_fields__:  # type: ignore[attr-defined]
        value = getattr(metrics, field_name)
        if not isinstance(value, MetricValue):
            continue  # scalar counts are surfaced separately, not as metric rows
        rows.append(
            {
                "name": value.name,
                "value": value.value,
                "status": value.status,
                "notes": value.notes,
            }
        )
    return rows


def _metric_counts(metrics: object) -> dict[str, int]:
    """Scalar counts on EvalMetrics (answerable/unanswerable/error totals)."""
    counts: dict[str, int] = {}
    for field_name in metrics.__dataclass_fields__:  # type: ignore[attr-defined]
        value = getattr(metrics, field_name)
        if isinstance(value, int) and not isinstance(value, bool):
            counts[field_name] = value
    return counts


#: Metric names whose values are wall-clock dependent and therefore live
#: in the volatile section even though they were really measured.
_VOLATILE_METRIC_NAMES = frozenset({"latency_p50_ms", "latency_p95_ms"})


def build_report(result: EvalRunResult) -> dict[str, object]:
    """Build the JSON-able report with deterministic/volatile split."""
    all_rows = _metric_rows(result.metrics)
    deterministic_rows = [
        row for row in all_rows if row["name"] not in _VOLATILE_METRIC_NAMES
    ]
    volatile_rows = [row for row in all_rows if row["name"] in _VOLATILE_METRIC_NAMES]

    deterministic: dict[str, object] = {
        "dataset_id": result.dataset_id,
        "manifest_sha256": result.manifest_digest,
        "question_file_sha256": result.question_file_sha256,
        "lockfile_sha256": _lockfile_sha256(),
        "split": result.split,
        "tuning": result.tuning,
        "top_k": result.top_k,
        "question_count": result.question_count,
        "metric_counts": _metric_counts(result.metrics),
        "ingest_summary": result.ingest_summary,
        "config": result.config_labels,
        "metrics": deterministic_rows,
        "retrieval_outcome_distribution": result.retrieval_outcome_distribution,
        "generation_outcome_distribution": result.generation_outcome_distribution,
        "abstention_reason_distribution": result.abstention_reason_distribution,
        "served_by_distribution": result.served_by_distribution,
        "error_classes": result.error_classes,
        "records": [
            {
                "question_id": record.question_id,
                "question_type": record.question_type,
                "expected_outcome": record.expected_outcome,
                "gold_chunk_count": len(record.gold_chunk_ids),
                "served_chunk_count": len(record.served_chunk_ids),
                "served_chunk_ids": list(record.served_chunk_ids),
                "retrieval_outcome": record.retrieval_outcome,
                "generation_outcome": record.generation_outcome,
                "abstention_reason": record.abstention_reason,
                "retrieval_error": record.retrieval_error,
            }
            for record in result.records
        ],
    }

    volatile: dict[str, object] = {
        "run_id": result.run_id,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "wall_time_s": round(result.wall_time_s, 3),
        "metrics": volatile_rows,
        "stage_timings": [
            {
                "stage": timing.stage,
                "count": timing.count,
                "p50_ms": timing.p50_ms,
                "p95_ms": timing.p95_ms,
            }
            for timing in result.stage_timings
        ],
        "prior_test_split_evaluations": result.prior_test_split_evaluations,
        "run_db": f"data/eval_runs/{result.run_id}/corpus.db",
    }

    return {
        "schema": "rag-platform-eval-report/1",
        "known_limitations": list(KNOWN_LIMITATIONS),
        "deterministic": deterministic,
        "volatile": volatile,
    }


def _format_value(row: dict[str, object]) -> str:
    if row["status"] == "not_measured" or row["value"] is None:
        return "not_measured"
    value = row["value"]
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_markdown(result: EvalRunResult, report: dict[str, object]) -> str:
    """Render the human-readable Markdown report."""
    deterministic = report["deterministic"]  # type: ignore[assignment]
    volatile = report["volatile"]  # type: ignore[assignment]
    config = deterministic["config"]  # type: ignore[index]

    lines: list[str] = []
    lines.append(f"# Evaluation report: {deterministic['dataset_id']}")
    lines.append("")
    lines.append(
        f"- **Split:** {deterministic['split']} (tuning={deterministic['tuning']})"
    )
    lines.append(
        f"- **Questions:** {deterministic['question_count']}  "
        f"**top_k:** {deterministic['top_k']}"
    )
    lines.append(f"- **Manifest SHA-256:** `{deterministic['manifest_sha256']}`")
    lines.append(
        f"- **Question file SHA-256:** `{deterministic['question_file_sha256']}`"
    )
    lines.append(f"- **Lockfile SHA-256:** `{deterministic['lockfile_sha256']}`")
    lines.append(
        f"- **Retrieval mode:** {config['retrieval_mode']}  "  # type: ignore[index]
        f"**generation:** {config['generation_enabled']}  "  # type: ignore[index]
        f"**model:** {config['generation_model']}"
    )  # type: ignore[index]
    lines.append(
        f"- **Score floor:** {config['retrieval_score_floor']}  "  # type: ignore[index]
        f"**chunking:** {config['chunk_size']}/{config['chunk_overlap']}"
    )  # type: ignore[index]
    lines.append(f"- **Run id:** `{volatile['run_id']}`")
    lines.append(
        f"- **Started:** {volatile['started_at']}  "
        f"**Finished:** {volatile['finished_at']}"
    )
    lines.append("")
    lines.append("## Known metric limitations (plan 9.4)")
    lines.append("")
    for limitation in report["known_limitations"]:  # type: ignore[union-attr]
        lines.append(f"- {limitation}")
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("| Metric | Value | Status | Notes |")
    lines.append("|---|---|---|---|")
    for row in deterministic["metrics"]:  # type: ignore[index]
        lines.append(
            f"| {row['name']} | {_format_value(row)} | {row['status']} | "
            f"{row['notes']} |"
        )
    for row in volatile["metrics"]:  # type: ignore[index]
        lines.append(
            f"| {row['name']} | {_format_value(row)} | {row['status']} | "
            f"{row['notes']} (volatile) |"
        )
    lines.append("")
    lines.append("## Outcome distributions")
    lines.append("")
    lines.append(
        "- **Retrieval outcomes:** "
        f"{json.dumps(deterministic['retrieval_outcome_distribution'], sort_keys=True)}"
    )
    lines.append(
        "- **Generation outcomes:** "
        f"{json.dumps(deterministic['generation_outcome_distribution'], sort_keys=True)}"
    )
    lines.append(
        "- **Abstention reasons:** "
        f"{json.dumps(deterministic['abstention_reason_distribution'], sort_keys=True)}"
    )
    lines.append(
        "- **Served by:** "
        f"{json.dumps(deterministic['served_by_distribution'], sort_keys=True)}"
    )
    lines.append(
        "- **Query error classes:** "
        f"{json.dumps(deterministic['error_classes'], sort_keys=True)}"
    )
    lines.append("")
    lines.append("## Ingestion")
    lines.append("")
    lines.append(json.dumps(deterministic["ingest_summary"], sort_keys=True))
    lines.append("")
    lines.append("## Resources (measured, volatile)")
    lines.append("")
    lines.append("| Stage | Count | p50 (ms) | p95 (ms) |")
    lines.append("|---|---|---|---|")
    for timing in volatile["stage_timings"]:  # type: ignore[index]
        lines.append(
            f"| {timing['stage']} | {timing['count']} | "
            f"{timing['p50_ms'] if timing['p50_ms'] is not None else 'not_measured'} | "
            f"{timing['p95_ms'] if timing['p95_ms'] is not None else 'not_measured'} |"
        )
    lines.append("")
    lines.append(f"- **Wall time:** {volatile['wall_time_s']} s")
    lines.append(
        "- **Prior test-split evaluations (this dataset):** "
        f"{volatile['prior_test_split_evaluations']} "
        "(counted from data/eval_runs run records; deleting that directory "
        "resets the count)"
    )
    lines.append("")
    lines.append("## Per-question records (IDs only)")
    lines.append("")
    lines.append(
        "| Question | Type | Expected | Gold # | Served # | Retrieval | Generation |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for record in deterministic["records"]:  # type: ignore[index]
        lines.append(
            f"| {record['question_id']} | {record['question_type']} | "
            f"{record['expected_outcome']} | {record['gold_chunk_count']} | "
            f"{record['served_chunk_count']} | {record['retrieval_outcome']} | "
            f"{record['generation_outcome']} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_reports(result: EvalRunResult, reports_dir: Path) -> tuple[Path, Path]:
    """Write the JSON and Markdown reports; returns both paths.

    Reports accumulate - never deleted or overwritten; the timestamp in
    the filename keeps runs distinct. ``reports_dir`` must already be
    beneath the project root; this function itself enforces that both
    output filenames resolve back beneath ``reports_dir`` before any byte
    is written (fail-closed containment, independent of the settings
    validator).
    """
    report = build_report(result)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    # The run-id suffix keeps filenames unique per run: two runs finishing
    # inside the same second never overwrite each other (reports
    # accumulate). Re-writing the SAME result stays byte-identical.
    run_hex = result.run_id.rsplit("_", 1)[-1]
    stem = f"eval_{result.dataset_id}_{result.split}_{stamp}_{run_hex}"
    json_path = reports_dir / f"{stem}.json"
    md_path = reports_dir / f"{stem}.md"
    json_path = require_beneath(reports_dir, json_path, what="report file")
    md_path = require_beneath(reports_dir, md_path, what="report file")
    # Order is deliberate: resolve -> verify containment -> mkdir -> write,
    # so a rejected path never creates anything, not even the root dir.
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(result, report), encoding="utf-8")
    return json_path, md_path
