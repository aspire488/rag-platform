"""Phase 6 typed-contract tests: frozen plan caps, validated request/
response shapes, abstention/citation/response invariants, and the
additive Settings bounds.

Offline and deterministic: construction-time validation only - no model,
no network, no services, no I/O.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from rag_platform.config import Settings
from rag_platform.generation.types import (
    HARD_MAX_TOKENS,
    MAX_DEADLINE_S,
    MAX_EVIDENCE_CHUNKS,
    MAX_PROMPT_CHARS,
    Abstention,
    AbstentionReason,
    AnswerResponse,
    Citation,
    CitationValidation,
    EvidenceSpan,
    FailureCategory,
    GenerationOutcome,
    GenerationRequest,
    GenerationResult,
    InvalidCitationReason,
    ProviderInfo,
)
from rag_platform.generation.errors import GenerationRequestError
from rag_platform.retrieval.types import RetrievalReport, RetrievedChunk


def _span(start: int = 0, end: int = 10) -> EvidenceSpan:
    return EvidenceSpan(
        chunk_id="a" * 64, doc_id="d" * 64, char_start=start, char_end=end
    )


def _request() -> GenerationRequest:
    return GenerationRequest(
        prompt="answer from this evidence",
        evidence=(_span(),),
        max_tokens=128,
        deadline_s=30.0,
    )


def _answered() -> GenerationResult:
    return GenerationResult(
        provider="ollama",
        model="m",
        text="Yes. [C1]",
        outcome=GenerationOutcome.ANSWERED,
        failure_category=None,
        latency_s=0.5,
        attempts=1,
    )


# --- frozen plan constants (single source of truth) ---------------------


def test_frozen_plan_caps_are_exact() -> None:
    assert MAX_EVIDENCE_CHUNKS == 8
    assert MAX_PROMPT_CHARS == 8000
    assert MAX_DEADLINE_S == 60.0
    assert HARD_MAX_TOKENS == 4096  # design choice, documented in types.py


# --- GenerationRequest bounds -------------------------------------------


def test_request_is_frozen_and_carries_bounded_context() -> None:
    request = _request()
    with pytest.raises(FrozenInstanceError):
        request.prompt = "rewritten"  # type: ignore[misc]
    assert request.evidence == (_span(),)
    assert request.deadline_s == 30.0


@pytest.mark.parametrize(
    "prompt",
    ["", "x" * (MAX_PROMPT_CHARS + 1), 42, None],
)
def test_request_rejects_invalid_prompts(prompt: object) -> None:
    with pytest.raises(GenerationRequestError):
        GenerationRequest(
            prompt=prompt,  # type: ignore[arg-type]
            evidence=(_span(),),
            max_tokens=8,
            deadline_s=10.0,
        )


@pytest.mark.parametrize("deadline", [60.5, 0.0, -1.0, float("nan"), True, "30"])
def test_request_rejects_out_of_bound_deadlines(deadline: object) -> None:
    with pytest.raises(GenerationRequestError):
        GenerationRequest(
            prompt="p",
            evidence=(_span(),),
            max_tokens=8,
            deadline_s=deadline,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("tokens", [0, HARD_MAX_TOKENS + 1, -5, True, 1.5])
def test_request_rejects_out_of_bound_token_caps(tokens: object) -> None:
    with pytest.raises(GenerationRequestError):
        GenerationRequest(
            prompt="p",
            evidence=(_span(),),
            max_tokens=tokens,  # type: ignore[arg-type]
            deadline_s=10.0,
        )


def test_request_requires_non_empty_evidence_of_spans() -> None:
    for evidence in ((), [_span()], None, ("not-a-span",)):
        with pytest.raises(GenerationRequestError):
            GenerationRequest(
                prompt="p",
                evidence=evidence,  # type: ignore[arg-type]
                max_tokens=8,
                deadline_s=10.0,
            )


# --- EvidenceSpan -------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [(0, 0), (9, 3), (-1, 5)],
)
def test_evidence_span_requires_a_non_empty_valid_range(start: int, end: int) -> None:
    with pytest.raises(GenerationRequestError):
        EvidenceSpan(chunk_id="c", doc_id="d", char_start=start, char_end=end)


def test_evidence_span_rejects_bool_and_empty_ids() -> None:
    with pytest.raises(GenerationRequestError):
        EvidenceSpan(chunk_id="c", doc_id="d", char_start=True, char_end=5)  # type: ignore[arg-type]
    with pytest.raises(GenerationRequestError):
        EvidenceSpan(chunk_id="", doc_id="d", char_start=0, char_end=5)
    with pytest.raises(GenerationRequestError):
        EvidenceSpan(chunk_id="c", doc_id="", char_start=0, char_end=5)


# --- GenerationResult outcome invariants --------------------------------


def test_answered_result_requires_text_provider_model_and_attempts() -> None:
    valid = _answered()
    assert valid.available is True
    broken = [
        {"text": None},
        {"text": ""},
        {"provider": None},
        {"model": ""},
        {"failure_category": FailureCategory.TIMEOUT},
        {"attempts": 0},
    ]
    for overrides in broken:
        base = {
            "provider": "ollama",
            "model": "m",
            "text": "Yes.",
            "outcome": GenerationOutcome.ANSWERED,
            "failure_category": None,
            "latency_s": 0.1,
            "attempts": 1,
        }
        base.update(overrides)
        with pytest.raises(GenerationRequestError):
            GenerationResult(**base)  # type: ignore[arg-type]


def test_abstained_result_carries_no_provider_text_or_attempts() -> None:
    def build(**overrides: object) -> dict:
        base = {
            "provider": None,
            "model": None,
            "text": None,
            "outcome": GenerationOutcome.ABSTAINED,
            "failure_category": None,
            "latency_s": 0.0,
            "attempts": 0,
        }
        base.update(overrides)
        return base

    with pytest.raises(GenerationRequestError):
        GenerationResult(**build(text="leaked"))  # type: ignore[arg-type]
    with pytest.raises(GenerationRequestError):
        GenerationResult(**build(failure_category=FailureCategory.DISABLED))  # type: ignore[arg-type]
    with pytest.raises(GenerationRequestError):
        GenerationResult(**build(provider="ollama"))  # type: ignore[arg-type]
    with pytest.raises(GenerationRequestError):
        GenerationResult(**build(attempts=1))  # type: ignore[arg-type]


def test_unavailable_result_requires_a_typed_category_and_no_text() -> None:
    def build(**overrides: object) -> dict:
        base = {
            "provider": None,
            "model": None,
            "text": None,
            "outcome": GenerationOutcome.UNAVAILABLE,
            "failure_category": FailureCategory.UNREACHABLE,
            "latency_s": 0.0,
            "attempts": 0,
        }
        base.update(overrides)
        return base

    unavailable = GenerationResult(**build())  # type: ignore[arg-type]
    assert unavailable.available is False
    with pytest.raises(GenerationRequestError):
        GenerationResult(**build(text="fabricated"))  # type: ignore[arg-type]
    with pytest.raises(GenerationRequestError):
        GenerationResult(**build(failure_category=None))  # type: ignore[arg-type]
    # A configured-but-failed client may record provider/model (429 etc.).
    still_ok = GenerationResult(**build(provider="ollama", model="m", attempts=3))  # type: ignore[arg-type]
    assert still_ok.failure_category is FailureCategory.UNREACHABLE


def test_result_rejects_non_enum_outcome_and_bad_numbers() -> None:
    with pytest.raises(GenerationRequestError):
        GenerationResult(
            provider=None,
            model=None,
            text=None,
            outcome="answered",  # type: ignore[arg-type]
            failure_category=None,
            latency_s=0.0,
            attempts=0,
        )
    with pytest.raises(GenerationRequestError):
        GenerationResult(
            provider=None,
            model=None,
            text=None,
            outcome=GenerationOutcome.UNAVAILABLE,
            failure_category=FailureCategory.DISABLED,
            latency_s=float("nan"),
            attempts=0,
        )
    with pytest.raises(GenerationRequestError):
        GenerationResult(
            provider=None,
            model=None,
            text=None,
            outcome=GenerationOutcome.UNAVAILABLE,
            failure_category=FailureCategory.DISABLED,
            latency_s=0.0,
            attempts=-1,
        )


# --- Abstention / ProviderInfo / Citation / CitationValidation -----------


def test_abstention_requires_a_typed_label_and_never_a_path() -> None:
    with pytest.raises(GenerationRequestError):
        Abstention(reason="no_candidates", detail="x")  # type: ignore[arg-type]
    with pytest.raises(GenerationRequestError):
        Abstention(reason=AbstentionReason.NO_CANDIDATES, detail="")
    with pytest.raises(GenerationRequestError):
        Abstention(
            reason=AbstentionReason.NO_CANDIDATES,
            detail="C:\\project\\leak",
        )
    with pytest.raises(GenerationRequestError):
        Abstention(reason=AbstentionReason.NO_CANDIDATES, detail="a/relative/path")


def test_provider_info_requires_explicit_names() -> None:
    assert ProviderInfo(provider="ollama", model="m").model == "m"
    with pytest.raises(GenerationRequestError):
        ProviderInfo(provider="", model="m")
    with pytest.raises(GenerationRequestError):
        ProviderInfo(provider="ollama", model="")


def test_citation_invariants() -> None:
    Citation(index=1, chunk_id="c", doc_id="d", char_start=0, char_end=5)
    for broken in (
        {"index": 0},
        {"char_start": 5, "char_end": 5},
        {"char_start": -1, "char_end": 5},
        {"chunk_id": ""},
    ):
        base = {
            "index": 1,
            "chunk_id": "c",
            "doc_id": "d",
            "char_start": 0,
            "char_end": 5,
        }
        base.update(broken)
        with pytest.raises(GenerationRequestError):
            Citation(**base)  # type: ignore[arg-type]


def test_citation_validation_shape_invariants() -> None:
    citation = Citation(index=1, chunk_id="c", doc_id="d", char_start=0, char_end=5)
    CitationValidation(valid=True, citations=(citation,), reason=None)
    CitationValidation(
        valid=False, citations=(), reason=InvalidCitationReason.NO_CITATIONS
    )
    with pytest.raises(GenerationRequestError):
        CitationValidation(valid=True, citations=(), reason=None)  # grounded-or-nothing
    with pytest.raises(GenerationRequestError):
        CitationValidation(
            valid=False, citations=(citation,), reason=InvalidCitationReason.MALFORMED
        )
    with pytest.raises(GenerationRequestError):
        CitationValidation(valid=False, citations=(), reason=None)


# --- AnswerResponse (the one canonical response object) -----------------


def _retrieval_report(results: tuple[RetrievedChunk, ...]) -> RetrievalReport:
    return RetrievalReport(
        mode="lexical_only",
        lexical_only_reason="no_embedder",
        outcome="ok" if results else "no_candidates",
        top_k=8,
        lexical_hits=len(results),
        dense_hits=0,
        fused_candidates=len(results),
        below_floor_excluded=0,
        unhydrated_skipped=0,
        post_verify_dropped=0,
        score_floor=None,
        results=results,
    )


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c" * 64,
        doc_id="d" * 64,
        ordinal=0,
        source_path="dir/a.txt",
        content="The walrus swims.",
        char_start=0,
        char_end=17,
        rank=1,
        fused_score=1.0,
        dense_cosine_similarity=None,
        lexical_bm25=-1.0,
        injection_quarantined=False,
        injection_finding_count=0,
    )


def test_answered_response_needs_citations_and_no_abstention() -> None:
    results = (_chunk(),)
    report = _retrieval_report(results)
    citation = Citation(
        index=1, chunk_id="c" * 64, doc_id="d" * 64, char_start=0, char_end=17
    )
    AnswerResponse(
        retrieval=report,
        passages=results,
        generation=_answered(),
        abstention=None,
        citations=(citation,),
    )
    with pytest.raises(GenerationRequestError):  # answered but ungrounded
        AnswerResponse(
            retrieval=report,
            passages=results,
            generation=_answered(),
            abstention=None,
            citations=(),
        )
    with pytest.raises(GenerationRequestError):  # answered but abstained too
        AnswerResponse(
            retrieval=report,
            passages=results,
            generation=_answered(),
            abstention=Abstention(
                reason=AbstentionReason.NO_CANDIDATES, detail="retrieval:no_candidates"
            ),
            citations=(citation,),
        )


def test_no_answer_response_requires_abstention_and_no_citations() -> None:
    results = (_chunk(),)
    report = _retrieval_report(results)
    unavailable = GenerationResult(
        provider=None,
        model=None,
        text=None,
        outcome=GenerationOutcome.UNAVAILABLE,
        failure_category=FailureCategory.DISABLED,
        latency_s=0.0,
        attempts=0,
    )
    abstention = Abstention(
        reason=AbstentionReason.GENERATION_UNAVAILABLE, detail="config:disabled"
    )
    response = AnswerResponse(
        retrieval=report,
        passages=results,
        generation=unavailable,
        abstention=abstention,
        citations=(),
    )
    assert response.generation_available is False
    with pytest.raises(GenerationRequestError):  # missing abstention
        AnswerResponse(
            retrieval=report,
            passages=results,
            generation=unavailable,
            abstention=None,
            citations=(),
        )
    with pytest.raises(GenerationRequestError):  # citations without an answer
        AnswerResponse(
            retrieval=report,
            passages=results,
            generation=unavailable,
            abstention=abstention,
            citations=(
                Citation(
                    index=1,
                    chunk_id="c" * 64,
                    doc_id="d" * 64,
                    char_start=0,
                    char_end=17,
                ),
            ),
        )


def test_passages_are_structurally_the_retrieval_results() -> None:
    results = (_chunk(),)
    report = _retrieval_report(results)
    abstention = Abstention(
        reason=AbstentionReason.GENERATION_UNAVAILABLE, detail="config:disabled"
    )
    unavailable = GenerationResult(
        provider=None,
        model=None,
        text=None,
        outcome=GenerationOutcome.UNAVAILABLE,
        failure_category=FailureCategory.DISABLED,
        latency_s=0.0,
        attempts=0,
    )
    with pytest.raises(GenerationRequestError):  # fabricated passage set
        AnswerResponse(
            retrieval=report,
            passages=(),
            generation=unavailable,
            abstention=abstention,
            citations=(),
        )
    with pytest.raises(GenerationRequestError):  # wrong report type entirely
        AnswerResponse(
            retrieval=object(),  # type: ignore[arg-type]
            passages=results,
            generation=unavailable,
            abstention=abstention,
            citations=(),
        )


# --- additive Settings bounds (Phase 6; frozen plan caps) ---------------


def test_generation_settings_defaults_stay_off_and_bounded() -> None:
    settings = Settings(_env_file=None)
    assert settings.generation_model is None  # no invented model (5.1)
    assert settings.generation_max_tokens == 512
    assert settings.generation_context_chunks == 8
    assert settings.generation_prompt_chars == 8000
    assert settings.local_generation_enabled is False  # disabled default
    assert settings.egress_mode == "off"
    assert settings.retrieval_score_floor is None  # floor untouched


def test_generation_caps_cannot_exceed_frozen_plan_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, generation_context_chunks=9)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, generation_context_chunks=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, generation_prompt_chars=8001)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, generation_prompt_chars=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, generation_max_tokens=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, generation_max_tokens=HARD_MAX_TOKENS + 1)
    # Tightening below the cap is legal (config may only tighten).
    tighter = Settings(_env_file=None, generation_context_chunks=3)
    assert tighter.generation_context_chunks == 3


def test_generation_model_must_be_explicitly_non_empty() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, generation_model="")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, generation_model="   ")
    stripped = Settings(_env_file=None, generation_model="  qwen3:8b ")
    assert stripped.generation_model == "qwen3:8b"
