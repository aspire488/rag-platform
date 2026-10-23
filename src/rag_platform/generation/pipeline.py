"""Retrieval-first answer orchestration (Phase 6; sections 4.2/5.4).

Canonical order implemented here (the section 4.2 steps this phase owns):

1. **Retrieve** through the existing ``HybridRetriever`` (unchanged).
2. **Pre-generation abstention (I12)** - decided from retrieval evidence
   alone, BEFORE any client call and BEFORE any configuration eligibility
   check, and not overridable by any flag or injected client. No
   candidates, all dense evidence below the applied floor, or no usable
   post-verified spans => typed abstention result, zero client calls.
3. **Bounded context** from post-verified spans only (frozen caps;
   no source paths ever enter the prompt).
4. **Availability check** - ``local_generation_enabled`` then an explicit
   ``generation_model``; the default configuration resolves to a typed
   ``DISABLED`` outcome and constructs nothing capable of a socket.
5. **Client call** only when enabled and eligible (one synchronous call -
   generation concurrency is structurally 1: no async, no threads, no
   parallel requests anywhere in this package).
6. **Citation validation** - every claim against the exact served
   evidence; any failure rejects the answer as a whole.
7. **One canonical ``AnswerResponse``** on every path.

Truthfulness rules (section 5.4 / I14 / Z11):

- Every unavailable/abstained path returns passages with chunk ids and
  char ranges plus ``generation.text is None``, a typed failure category,
  and an abstention object - never a fabricated answer, never a provider
  claimed as serving when it did not.
- A client that *raises* is contained to a typed ``CLIENT_ERROR`` result
  (class name only - never the exception message, never a prompt or path).
- A client returning a non-contract object, or returning ``ABSTAINED``
  (abstention is pipeline-owned, decided before any client exists), is a
  ``CLIENT_ERROR`` contract violation - never served.
- Lexical-only evidence with an unset score floor is USABLE evidence
  (it passed no dense floor because none applies); it is never
  reinterpreted as floor-rejected. Abstention keys on retrieval
  outcomes/emptiness only.

``settings`` supplies generation config and client eligibility;
``retriever`` carries its own retrieval settings (Phase 5 contract,
untouched) - callers should construct both from one ``Settings`` source.
Invalid queries raise the retriever's typed ``QueryValidationError`` - a
caller error, not a response path.
"""

from __future__ import annotations

import logging
import time

from rag_platform.config import Settings
from rag_platform.generation.citations import validate_citations
from rag_platform.generation.client import GenerationClient, build_client
from rag_platform.generation.context import build_context
from rag_platform.generation.errors import ContextBudgetError, GenerationError
from rag_platform.generation.types import (
    MAX_DEADLINE_S,
    Abstention,
    AbstentionReason,
    AnswerResponse,
    FailureCategory,
    GenerationOutcome,
    GenerationRequest,
    GenerationResult,
)
from rag_platform.observability import Observer, StageEvent
from rag_platform.retrieval.hybrid import HybridRetriever
from rag_platform.retrieval.types import RetrievalReport

logger = logging.getLogger(__name__)


def _emit(observer: Observer | None, stage: str, outcome: str, started: float) -> None:
    """Report one observed stage; an observer failure never alters the run.

    Observers are passive instrumentation (Phase 8): they receive labels,
    enums and measured timings only. A raising observer is contained to a
    logged class name - the pipeline response is produced by evidence and
    configuration, never by telemetry.
    """
    if observer is None:
        return
    try:
        observer(
            StageEvent(
                stage=stage,  # type: ignore[arg-type]
                outcome=outcome,
                timing_ms=max((time.perf_counter() - started) * 1000.0, 0.0),
            )
        )
    except Exception as exc:  # noqa: BLE001 - containment boundary by design
        logger.warning(
            "stage observer failed (class=%s); response unaffected",
            type(exc).__name__,
        )


def answer_query(
    settings: Settings,
    retriever: HybridRetriever,
    query: str,
    *,
    client: GenerationClient | None = None,
    observer: Observer | None = None,
) -> AnswerResponse:
    """Answer *query* with evidence: generated+cited, abstained, or retrieval-only.

    *client* is an explicit wiring/test hook: an injected client is used
    only when ``local_generation_enabled`` is also true (configuration
    remains the master switch - disabling always wins). With no injected
    client, eligibility is decided entirely by ``settings``; the shipped
    defaults construct no client and open no socket.

    *observer* (Phase 8, optional) receives one :class:`StageEvent` for
    each stage with a real measured duration (``retrieval``,
    ``context_build``). The default ``None`` preserves pre-Phase-8
    behaviour exactly: no events, no timing capture, identical responses.
    """
    if client is not None and not isinstance(client, GenerationClient):
        raise GenerationError(
            "injected client must implement the GenerationClient protocol; "
            f"got {type(client).__name__}"
        )

    retrieval_started = time.perf_counter()
    retrieval = retriever.retrieve(query)
    _emit(observer, "retrieval", retrieval.outcome, retrieval_started)

    # --- 2. pre-generation abstention (I12): evidence-only, pre-client ---
    if retrieval.outcome == "no_candidates":
        return _abstained(
            retrieval, AbstentionReason.NO_CANDIDATES, "retrieval:no_candidates"
        )
    if retrieval.outcome == "insufficient_evidence":
        return _abstained(
            retrieval,
            AbstentionReason.INSUFFICIENT_EVIDENCE,
            "retrieval:insufficient_evidence",
        )
    if not retrieval.results:
        # e.g. every surviving result dropped by the post-verify hook.
        return _abstained(
            retrieval, AbstentionReason.NO_USABLE_EVIDENCE, "retrieval:no_usable_spans"
        )

    # --- 3. bounded context (post-verified spans only, no paths) ---
    context_started = time.perf_counter()
    try:
        context = build_context(
            retrieval.results,
            max_chunks=settings.generation_context_chunks,
            max_prompt_chars=settings.generation_prompt_chars,
        )
    except ContextBudgetError:
        _emit(observer, "context_build", "budget_invalid", context_started)
        return _unavailable(
            retrieval,
            FailureCategory.CONTEXT_BUDGET,
            detail="context:budget_invalid",
        )
    if not context.evidence:
        # Budget could not fit one labeled span: no usable evidence to
        # ground an answer - abstain, never call a client.
        _emit(observer, "context_build", "budget_exhausted", context_started)
        return _abstained(
            retrieval,
            AbstentionReason.NO_USABLE_EVIDENCE,
            "context:budget_exhausted",
        )
    _emit(observer, "context_build", "ok", context_started)

    # --- 4. availability (master switch first; default = no socket) ---
    if not settings.local_generation_enabled:
        return _unavailable(
            retrieval, FailureCategory.DISABLED, detail="config:disabled"
        )
    selected = client
    owns_client = False
    if selected is None:
        if settings.generation_model is None:
            return _unavailable(
                retrieval,
                FailureCategory.NOT_CONFIGURED,
                detail="config:model_unset",
            )
        selected = build_client(settings)
        owns_client = selected is not None
        if selected is None:  # defensive: settings re-checked above
            return _unavailable(
                retrieval, FailureCategory.DISABLED, detail="config:disabled"
            )

    # --- 5. one bounded, synchronous client call ---
    request = GenerationRequest(
        prompt=context.prompt,
        evidence=context.evidence,
        max_tokens=settings.generation_max_tokens,
        deadline_s=float(MAX_DEADLINE_S),
    )
    try:
        result = selected.generate(request)
    except Exception as exc:  # contained: class name only, never messages
        # ``finally`` still closes an owned client before this returns.
        return _unavailable(
            retrieval,
            FailureCategory.CLIENT_ERROR,
            detail=f"client:{type(exc).__name__}",
        )
    finally:
        if owns_client:
            close = getattr(selected, "close", None)
            if callable(close):
                close()

    if not isinstance(result, GenerationResult):
        return _unavailable(
            retrieval,
            FailureCategory.CLIENT_ERROR,
            detail="client:contract_violation",
        )
    if result.outcome is GenerationOutcome.ABSTAINED:
        # Abstention is pipeline-owned (decided in step 2, before any
        # client exists): a provider abstaining is a contract violation.
        return _unavailable(
            retrieval,
            FailureCategory.CLIENT_ERROR,
            detail="client:abstained",
        )
    if result.outcome is not GenerationOutcome.ANSWERED:
        return AnswerResponse(
            retrieval=retrieval,
            passages=tuple(retrieval.results),
            generation=result,
            abstention=Abstention(
                reason=AbstentionReason.GENERATION_UNAVAILABLE,
                detail=f"generation:{result.failure_category.value}",
            ),
            citations=(),
        )

    # --- 6. citation validation against the exact served evidence ---
    validation = validate_citations(result.text or "", context.evidence)
    if not validation.valid:
        # Drop the text entirely: an ungrounded answer is never served
        # even partially. Attempt accounting of the real call is kept.
        rejected = GenerationResult(
            provider=result.provider,
            model=result.model,
            text=None,
            outcome=GenerationOutcome.UNAVAILABLE,
            failure_category=FailureCategory.INVALID_CITATION,
            latency_s=result.latency_s,
            attempts=result.attempts,
        )
        return AnswerResponse(
            retrieval=retrieval,
            passages=tuple(retrieval.results),
            generation=rejected,
            abstention=Abstention(
                reason=AbstentionReason.GENERATION_UNAVAILABLE,
                detail=f"citation:{validation.reason.value}",
            ),
            citations=(),
        )

    # --- 7. one canonical response object ---
    return AnswerResponse(
        retrieval=retrieval,
        passages=tuple(retrieval.results),
        generation=result,
        abstention=None,
        citations=validation.citations,
    )


def _abstained(
    retrieval: RetrievalReport, reason: AbstentionReason, detail: str
) -> AnswerResponse:
    """Typed abstention result: no provider concept, zero attempts."""
    return AnswerResponse(
        retrieval=retrieval,
        passages=tuple(retrieval.results),
        generation=GenerationResult(
            provider=None,
            model=None,
            text=None,
            outcome=GenerationOutcome.ABSTAINED,
            failure_category=None,
            latency_s=0.0,
            attempts=0,
        ),
        abstention=Abstention(reason=reason, detail=detail),
        citations=(),
    )


def _unavailable(
    retrieval: RetrievalReport, category: FailureCategory, *, detail: str
) -> AnswerResponse:
    """Retrieval-only response: passages served, generation unavailable."""
    return AnswerResponse(
        retrieval=retrieval,
        passages=tuple(retrieval.results),
        generation=GenerationResult(
            provider=None,
            model=None,
            text=None,
            outcome=GenerationOutcome.UNAVAILABLE,
            failure_category=category,
            latency_s=0.0,
            attempts=0,
        ),
        abstention=Abstention(
            reason=AbstentionReason.GENERATION_UNAVAILABLE, detail=detail
        ),
        citations=(),
    )
