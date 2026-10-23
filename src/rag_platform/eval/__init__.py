"""Offline evaluation harness (plan v2.4 Phase 8; sections 9.1-9.5).

Submodules:

- :mod:`rag_platform.eval.dataset` - manifest verification, split
  protection, frozen question-set loading (fail-closed).
- :mod:`rag_platform.eval.metrics` - deterministic stdlib-only metrics.
- :mod:`rag_platform.eval.harness` - one run, one isolated database,
  one result; queries go through the shared ``run_query``.
- :mod:`rag_platform.eval.report` - JSON (deterministic/volatile split)
  + Markdown report generation.

The package never loads a model, never opens a network socket, and
refuses generation-enabled runs (they are a separately owner-gated
future workflow).
"""

from __future__ import annotations

from rag_platform.eval.dataset import (
    DatasetIntegrityError,
    DatasetManifest,
    GoldSpan,
    Question,
    QuestionSet,
    load_and_verify_dataset,
    load_questions,
)
from rag_platform.eval.harness import (
    EvalConfigError,
    EvalRunResult,
    StageTimingsAggregate,
    run_eval,
)
from rag_platform.eval.metrics import (
    EvalMetrics,
    MetricValue,
    QuestionRecord,
    compute_metrics,
    nearest_rank_percentile,
)
from rag_platform.eval.report import (
    KNOWN_LIMITATIONS,
    build_report,
    render_markdown,
    write_reports,
)

__all__ = [
    "KNOWN_LIMITATIONS",
    "DatasetIntegrityError",
    "DatasetManifest",
    "EvalConfigError",
    "EvalMetrics",
    "EvalRunResult",
    "GoldSpan",
    "MetricValue",
    "Question",
    "QuestionRecord",
    "QuestionSet",
    "StageTimingsAggregate",
    "build_report",
    "compute_metrics",
    "load_and_verify_dataset",
    "load_questions",
    "nearest_rank_percentile",
    "render_markdown",
    "run_eval",
    "write_reports",
]
