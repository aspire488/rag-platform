"""Immutable generation contracts (plan v2.4 Phase 6; sections 5.1/5.4).

Honesty and safety rules baked into these contracts:

- Every response is validated at construction (frozen dataclass + explicit
  checks): an ``ANSWERED`` result must carry text, a real provider and a
  real model (never invented, section 5.1); a ``UNAVAILABLE`` result must
  carry a typed failure category and NO text - a failed generation can
  never masquerade as an answer (Z8/I14); an ``ABSTAINED`` result carries
  no provider at all (abstention happens before any client exists).
- ``EvidenceSpan`` deliberately has NO source-path field: local filesystem
  paths never enter a prompt or any provider-bound structure (I16).
- ``AnswerResponse`` is the one canonical response object: passages are
  structurally forced to equal the retrieval report's results (fabricated
  passages are impossible), every answered response carries citations and
  no abstention, every no-answer response carries an abstention object and
  no citations (section 5.4: an abstention with its reason is the correct
  output for a no-generation response).
- Frozen plan caps (section 10.1) live here as single-source constants:
  8 evidence chunks, 8000 prompt characters, 60 s total deadline. Config
  may only tighten these (validated in ``config.py``).
- ``HARD_MAX_TOKENS`` is a DESIGN CHOICE (I7 "bounded everything"): the
  plan freezes input caps explicitly and leaves the output bound to the
  implementation. It is a request-side limit handed to the provider, not
  a claim of token-level runtime enforcement - character limits are the
  structurally enforced ones.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from rag_platform.generation.errors import GenerationRequestError
from rag_platform.retrieval.types import RetrievalReport, RetrievedChunk

#: Frozen section 10.1 caps (single source of truth for the package).
MAX_EVIDENCE_CHUNKS = 8
MAX_PROMPT_CHARS = 8000
MAX_DEADLINE_S = 60.0

#: Design choice (documented above): hard bound for the output-token cap.
HARD_MAX_TOKENS = 4096


class GenerationOutcome(str, Enum):
    """Typed top-level outcome of one generation request."""

    ANSWERED = "answered"
    ABSTAINED = "abstained"
    UNAVAILABLE = "unavailable"


class FailureCategory(str, Enum):
    """Sanitized failure category (section 5.1 taxonomy, subset for M1).

    ``DISABLED``/``NOT_CONFIGURED`` are the *skipped* class; ``UNREACHABLE``
    /``TIMEOUT``/``MALFORMED_RESPONSE``/``PROVIDER_ERROR`` are the
    *transient* class (bounded retry permitted, never applied by this
    enum itself); ``PERMANENT_FAILURE`` is never retried (I15);
    ``INVALID_CITATION``/``CONTEXT_BUDGET``/``CLIENT_ERROR`` are local
    pipeline failures. Section 4.2 trace mapping (recorded for the Phase 8
    trace recorder): ``disabled``/``not_configured`` ->
    ``DEGRADED_RETRIEVAL_ONLY``; ``permanent_failure`` ->
    ``PROVIDER_FAILSTOP``; the rest -> ``MODEL_UNAVAILABLE``.
    """

    DISABLED = "disabled"
    NOT_CONFIGURED = "not_configured"
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    MALFORMED_RESPONSE = "malformed_response"
    PROVIDER_ERROR = "provider_error"
    PERMANENT_FAILURE = "permanent_failure"
    INVALID_CITATION = "invalid_citation"
    CONTEXT_BUDGET = "context_budget"
    CLIENT_ERROR = "client_error"


class AbstentionReason(str, Enum):
    """Why no generated answer was produced (I12 + section 5.4)."""

    NO_CANDIDATES = "no_candidates"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NO_USABLE_EVIDENCE = "no_usable_evidence"
    GENERATION_UNAVAILABLE = "generation_unavailable"


class InvalidCitationReason(str, Enum):
    """Why a generated answer failed citation validation (whole answer rejected)."""

    MALFORMED = "malformed"
    UNKNOWN_REFERENCE = "unknown_reference"
    SPAN_OUT_OF_RANGE = "span_out_of_range"
    NO_CITATIONS = "no_citations"


@dataclass(frozen=True)
class EvidenceSpan:
    """One served evidence reference: ids + absolute document char range.

    No source-path field by design (I16): this is the only provenance
    shape a prompt or provider request may carry.
    """

    chunk_id: str
    doc_id: str
    char_start: int
    char_end: int

    def __post_init__(self) -> None:
        if not isinstance(self.chunk_id, str) or not self.chunk_id:
            raise _contract("EvidenceSpan.chunk_id must be a non-empty string")
        if not isinstance(self.doc_id, str) or not self.doc_id:
            raise _contract("EvidenceSpan.doc_id must be a non-empty string")
        for name in ("char_start", "char_end"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise _contract(
                    f"EvidenceSpan.{name} must be a non-negative int; "
                    f"got {type(value).__name__}"
                )
        if self.char_end <= self.char_start:
            raise _contract("EvidenceSpan char range must be non-empty")


@dataclass(frozen=True)
class GenerationRequest:
    """A bounded generation request (section 5.1 request shape).

    ``prompt`` is the pre-built bounded context (never raw documents),
    ``evidence`` the exact served spans it references, ``deadline_s`` the
    remaining total generation budget (frozen section 10.1 cap), and
    ``max_tokens`` the validated output cap handed to the provider.
    """

    prompt: str
    evidence: tuple[EvidenceSpan, ...]
    max_tokens: int
    deadline_s: float

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, str) or not self.prompt:
            raise _contract("prompt must be a non-empty string")
        if len(self.prompt) > MAX_PROMPT_CHARS:
            raise _contract(
                f"prompt length {len(self.prompt)} exceeds the frozen cap "
                f"{MAX_PROMPT_CHARS} (I7)"
            )
        if not isinstance(self.evidence, tuple) or not self.evidence:
            raise _contract("evidence must be a non-empty tuple of EvidenceSpan")
        for span in self.evidence:
            if not isinstance(span, EvidenceSpan):
                raise _contract(
                    "evidence must contain only EvidenceSpan items; got "
                    f"{type(span).__name__}"
                )
        if (
            isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or not 1 <= self.max_tokens <= HARD_MAX_TOKENS
        ):
            raise _contract(
                f"max_tokens must be an int in 1..{HARD_MAX_TOKENS}; "
                f"got {self.max_tokens!r}"
            )
        if isinstance(self.deadline_s, bool) or not isinstance(
            self.deadline_s, (int, float)
        ):
            raise _contract(
                f"deadline_s must be a number; got {type(self.deadline_s).__name__}"
            )
        if (
            not math.isfinite(self.deadline_s)
            or not 0 < self.deadline_s <= MAX_DEADLINE_S
        ):
            raise _contract(
                f"deadline_s must be finite and in (0, {MAX_DEADLINE_S}]; "
                f"got {self.deadline_s!r}"
            )


@dataclass(frozen=True)
class GenerationResult:
    """One provider result or one typed no-answer outcome (section 5.1).

    Invariants (construction-time, so no consumer can be misled):

    - ``ANSWERED``: non-empty text, no failure category, non-empty
      provider AND model (never invented - adapters only fill these from
      explicit configuration), attempts >= 1.
    - ``ABSTAINED``: no text, no failure category, provider/model ``None``
      (abstention precedes any client; a provider cannot abstain - the
      pipeline treats a client-returned ABSTAINED as a contract
      violation), attempts == 0.
    - ``UNAVAILABLE``: no text, a required failure category, provider/
      model ``None`` (never configured) or set (configured but failed),
      attempts >= 0.
    """

    provider: str | None
    model: str | None
    text: str | None
    outcome: GenerationOutcome
    failure_category: FailureCategory | None
    latency_s: float
    attempts: int

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, GenerationOutcome):
            raise _contract(
                f"outcome must be a GenerationOutcome; got {type(self.outcome).__name__}"
            )
        for name in ("latency_s",):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise _contract(f"{name} must be a finite non-negative number")
        if (
            isinstance(self.attempts, bool)
            or not isinstance(self.attempts, int)
            or self.attempts < 0
        ):
            raise _contract(
                f"attempts must be a non-negative int; got {type(self.attempts).__name__}"
            )
        if self.outcome is GenerationOutcome.ANSWERED:
            if not isinstance(self.text, str) or not self.text:
                raise _contract("ANSWERED results must carry non-empty text")
            if self.failure_category is not None:
                raise _contract("ANSWERED results must not carry a failure category")
            for name in ("provider", "model"):
                value = getattr(self, name)
                if not isinstance(value, str) or not value:
                    raise _contract(
                        f"ANSWERED results must record an explicit {name} "
                        "(never invented, section 5.1)"
                    )
            if self.attempts < 1:
                raise _contract("ANSWERED results must record at least one attempt")
        elif self.outcome is GenerationOutcome.ABSTAINED:
            if self.text is not None:
                raise _contract("ABSTAINED results must not carry text")
            if self.failure_category is not None:
                raise _contract(
                    "ABSTAINED results carry no failure category (the "
                    "abstention reason lives on Abstention)"
                )
            if self.provider is not None or self.model is not None:
                raise _contract(
                    "ABSTAINED results carry no provider/model (abstention "
                    "precedes any client)"
                )
            if self.attempts != 0:
                raise _contract("ABSTAINED results record zero attempts")
        else:  # UNAVAILABLE
            if self.text is not None:
                raise _contract(
                    "UNAVAILABLE results must not carry text (retrieval-only: "
                    "a failure can never present an answer, section 5.4)"
                )
            if not isinstance(self.failure_category, FailureCategory):
                raise _contract(
                    "UNAVAILABLE results must record a typed failure category"
                )
            for name in ("provider", "model"):
                value = getattr(self, name)
                if value is not None and (not isinstance(value, str) or not value):
                    raise _contract(
                        f"{name} must be None or a non-empty string when unavailable"
                    )

    @property
    def available(self) -> bool:
        """Explicit generation state for responses (``generation: unavailable``)."""
        return self.outcome is GenerationOutcome.ANSWERED


@dataclass(frozen=True)
class ProviderInfo:
    """What a client is, as configuration - booleans and labels only (I6)."""

    provider: str
    model: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider:
            raise _contract("provider must be a non-empty string")
        if not isinstance(self.model, str) or not self.model:
            raise _contract("model must be a non-empty string (explicit selection)")


@dataclass(frozen=True)
class Abstention:
    """The abstention object every no-answer response must carry (5.4).

    ``detail`` is a typed label (``_``/``:``-joined identifiers only) -
    never content, never paths.
    """

    reason: AbstentionReason
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.reason, AbstentionReason):
            raise _contract(
                f"reason must be an AbstentionReason; got {type(self.reason).__name__}"
            )
        if not isinstance(self.detail, str) or not self.detail:
            raise _contract("abstention detail must be a non-empty typed label")
        if any(separator in self.detail for separator in ("/", "\\")):
            raise _contract(
                "abstention detail must be a typed label, never a path (I16)"
            )


@dataclass(frozen=True)
class Citation:
    """One validated citation, built exclusively from served evidence.

    ``chunk_id``/``doc_id``/full-range defaults always come from the
    ``EvidenceSpan`` we actually served - model text can only *reference*
    evidence, never supply provenance (fabrication is structurally
    impossible, section 5.4).
    """

    index: int  # 1-based position in the served evidence list
    chunk_id: str
    doc_id: str
    char_start: int
    char_end: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.index, bool)
            or not isinstance(self.index, int)
            or self.index < 1
        ):
            raise _contract("citation index must be an int >= 1")
        if not isinstance(self.chunk_id, str) or not self.chunk_id:
            raise _contract("citation chunk_id must be a non-empty string")
        if not isinstance(self.doc_id, str) or not self.doc_id:
            raise _contract("citation doc_id must be a non-empty string")
        for name in ("char_start", "char_end"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise _contract(
                    f"citation {name} must be a non-negative int; "
                    f"got {type(value).__name__}"
                )
        if self.char_end <= self.char_start:
            raise _contract("citation char range must be non-empty")


@dataclass(frozen=True)
class CitationValidation:
    """Result of validating an answer against the exact served evidence.

    Invalid validations always carry an EMPTY citation tuple: a partially
    resolvable answer is never returned as partially grounded.
    """

    valid: bool
    citations: tuple[Citation, ...]
    reason: InvalidCitationReason | None

    def __post_init__(self) -> None:
        if self.valid:
            if self.reason is not None or not self.citations:
                raise _contract(
                    "a valid validation carries citations and no failure reason "
                    "(at least one citation is required to be grounded)"
                )
        else:
            if not isinstance(self.reason, InvalidCitationReason):
                raise _contract("an invalid validation must record a typed reason")
            if self.citations:
                raise _contract(
                    "an invalid validation carries no citations (never partially "
                    "grounded)"
                )


@dataclass(frozen=True)
class AnswerResponse:
    """The one canonical response object (section 5.4; Phase 7 serves this).

    - ``passages`` must equal ``retrieval.results`` exactly: retrieved
      passages with their chunk ids and char ranges are always present,
      whatever happened to generation.
    - ``generation`` always exists: ``outcome`` says answered / abstained /
      unavailable with a typed category; ``text is None`` whenever no
      answer was produced (never a fabricated answer).
    - ``abstention`` is ``None`` only for a fully grounded answer; every
      no-answer response carries one (section 5.4).
    - ``citations`` are non-empty only for a validated answer.
    """

    retrieval: RetrievalReport
    passages: tuple[RetrievedChunk, ...]
    generation: GenerationResult
    abstention: Abstention | None
    citations: tuple[Citation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.retrieval, RetrievalReport):
            raise _contract(
                f"retrieval must be a RetrievalReport; got {type(self.retrieval).__name__}"
            )
        if self.passages != tuple(self.retrieval.results):
            raise _contract(
                "passages must be exactly the retrieval report's results "
                "(never fabricated, section 5.4)"
            )
        if not isinstance(self.generation, GenerationResult):
            raise _contract(
                "generation must be a GenerationResult; got "
                f"{type(self.generation).__name__}"
            )
        if self.generation.outcome is GenerationOutcome.ANSWERED:
            if self.abstention is not None:
                raise _contract("an answered response carries no abstention")
            if not self.citations:
                raise _contract(
                    "an answered response must carry validated citations "
                    "(grounded or nothing, section 5.4)"
                )
        else:
            if not isinstance(self.abstention, Abstention):
                raise _contract(
                    "a no-answer response must carry an Abstention object (section 5.4)"
                )
            if self.citations:
                raise _contract("a no-answer response carries no citations")
        for citation in self.citations:
            if not isinstance(citation, Citation):
                raise _contract(
                    "citations must contain only Citation items; got "
                    f"{type(citation).__name__}"
                )

    @property
    def generation_available(self) -> bool:
        """``generation: unavailable`` state for any no-answer response."""
        return self.generation.available


def _contract(message: str) -> GenerationRequestError:
    """Contract violations are raised as the typed generation error."""
    return GenerationRequestError(message)
