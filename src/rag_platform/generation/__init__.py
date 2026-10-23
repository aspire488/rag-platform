"""Provider-neutral generation package (plan v2.4 Phase 6).

Small public interface by design (section 5.1): typed contracts, the
bounded context builder, citation validation, the client ``Protocol`` +
its stingy factory, the inert local Ollama adapter, and the one
orchestration entry point that returns the single canonical response
object. Importing this package constructs nothing and opens no socket -
the shipped default configuration is retrieval-only (Z11/I3).

Owner-gated beyond this phase (never implied by these exports):
Phase 9's provider router (D16), any cloud provider or credential
(D15/Phase 10), BG-1a model loading, ``retrieval_score_floor`` approval,
and the ``/query`` API surface (Phase 7).
"""

from __future__ import annotations

from rag_platform.generation.citations import validate_citations
from rag_platform.generation.client import GenerationClient, build_client
from rag_platform.generation.context import GenerationContext, build_context
from rag_platform.generation.errors import (
    ContextBudgetError,
    GenerationError,
    GenerationRequestError,
)
from rag_platform.generation.ollama import (
    CONNECT_TIMEOUT_S,
    MAX_TRANSIENT_RETRIES,
    TOTAL_TIMEOUT_S,
    OllamaClient,
)
from rag_platform.generation.pipeline import answer_query
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

__all__ = [
    "CONNECT_TIMEOUT_S",
    "HARD_MAX_TOKENS",
    "MAX_DEADLINE_S",
    "MAX_EVIDENCE_CHUNKS",
    "MAX_PROMPT_CHARS",
    "MAX_TRANSIENT_RETRIES",
    "TOTAL_TIMEOUT_S",
    "Abstention",
    "AbstentionReason",
    "AnswerResponse",
    "Citation",
    "CitationValidation",
    "ContextBudgetError",
    "EvidenceSpan",
    "FailureCategory",
    "GenerationClient",
    "GenerationContext",
    "GenerationError",
    "GenerationOutcome",
    "GenerationRequest",
    "GenerationRequestError",
    "GenerationResult",
    "InvalidCitationReason",
    "OllamaClient",
    "ProviderInfo",
    "answer_query",
    "build_client",
    "build_context",
    "validate_citations",
]
