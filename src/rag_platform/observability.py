"""Stage observation seam (plan v2.4 Phase 8; section 4.2 stage timings).

A deliberately tiny, dependency-free module so that :mod:`rag_platform.generation.pipeline`,
:mod:`rag_platform.api`, and :mod:`rag_platform.tracing` can share the stage
event type without an import cycle (tracing already imports
``generation.types``; generation must not import tracing).

Contract:

- One :class:`StageEvent` per *observed* pipeline stage. At M1 the only
  stages with real measured timings outside the provider result are
  ``retrieval`` and ``context_build`` (generation latency already comes
  from the provider result; response timing is boundary-measured by
  ``run_query``).
- Stage vocabulary (frozen here; the schema CHECK admits the nine plan
  stage names, of which only these are written before M3): ``retrieval``,
  ``context_build``.
- Retrieval outcome vocabulary equals ``RetrievalReport.outcome``:
  ``ok`` / ``no_candidates`` / ``insufficient_evidence``. Context-build
  outcomes: ``ok`` / ``budget_invalid`` / ``budget_exhausted``.
- Observers are *passive*: they receive events and must never raise into
  the pipeline. Callers that wrap observers (:func:`rag_platform.api.run_query`)
  guarantee observer exceptions cannot change a response (logged by class
  name, swallowed).
- Events carry labels, enums, counts and timings only - never query text,
  never prompts, never passages, never paths (I6/I10/I16).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

StageName = Literal["retrieval", "context_build"]

RETRIEVAL_OUTCOMES = ("ok", "no_candidates", "insufficient_evidence")
CONTEXT_BUILD_OUTCOMES = ("ok", "budget_invalid", "budget_exhausted")


@dataclass(frozen=True)
class StageEvent:
    """One observed stage completion: label + outcome + measured duration."""

    stage: StageName
    outcome: str
    timing_ms: float


#: Passive observer callback; must not raise (callers also guard defensively).
Observer = Callable[[StageEvent], None]
