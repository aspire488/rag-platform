"""Phase 6 pipeline tests: canonical order (retrieve -> pre-generation
abstention -> bounded context -> availability -> client -> citations ->
one response), zero-client-call abstention, retrieval-only fallback for
every failure path, and redaction.

Offline and deterministic: real ``HybridRetriever`` over a contained
SQLite corpus (FTS5 only - no embedder, no store, no model), scripted
protocol-compliant fake clients, and one dense-store case for the floor
abstention - no network, no services.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from rag_platform.config import Settings
from rag_platform.db import initialize_database
from rag_platform.generation import (
    AnswerResponse,
    FailureCategory,
    GenerationOutcome,
    GenerationRequestError,
    GenerationResult,
    ProviderInfo,
    answer_query,
    build_client,
)
from rag_platform.generation.types import AbstentionReason
from rag_platform.ingestion.injection import scan_text
from rag_platform.ingestion.store import ChunkInput, CorpusStore
from rag_platform.retrieval import HybridRetriever
from rag_platform.retrieval.indexer import reconcile_index
from rag_platform.vectorstore.qdrant_local import QdrantLocalStore

_WALRUS_DOC = "The walrus swims slowly past the ice floe."


class ScriptedClient:
    """Protocol-compliant fake: scripted result (or exception), call log."""

    def __init__(self, script: GenerationResult | BaseException) -> None:
        self.calls = 0
        self.requests: list = []
        self._script = script

    def generate(self, request) -> GenerationResult:
        self.calls += 1
        self.requests.append(request)
        if isinstance(self._script, BaseException):
            raise self._script
        return self._script

    def describe(self) -> ProviderInfo:
        return ProviderInfo(provider="scripted", model="scripted-model")


class AllBelowEmbedder:
    """Query -> [1, 0]; every corpus text -> [0, 1] (cosine 0.0)."""

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0] if text == "walrus" else [0.0, 1.0] for text in texts]

    @property
    def is_loaded(self) -> bool:
        return True


def _settings(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {"_env_file": None}
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


def _answered(text: str) -> GenerationResult:
    return GenerationResult(
        provider="scripted",
        model="scripted-model",
        text=text,
        outcome=GenerationOutcome.ANSWERED,
        failure_category=None,
        latency_s=0.01,
        attempts=1,
    )


@pytest.fixture
def corpus_db(storage_tmp: Path) -> Iterator[sqlite3.Connection]:
    conn = initialize_database(storage_tmp / "corpus.db")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def store(storage_tmp: Path) -> Iterator[QdrantLocalStore]:
    local = QdrantLocalStore(storage_tmp / "vectors", vector_size=2)
    try:
        yield local
    finally:
        local.close()


def _ingest(conn: sqlite3.Connection, rel_path: str, text: str) -> None:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    CorpusStore(conn).upsert_document(
        source_rel_path=rel_path,
        source_name=Path(rel_path).name,
        file_format="txt",
        content_sha256=digest,
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


def _lexical_retriever(conn: sqlite3.Connection, settings: Settings) -> HybridRetriever:
    """FTS5-only retrieval (no embedder/store): the default-state shape."""
    return HybridRetriever(conn, settings=settings, embedder=None, store=None)


# --- happy path: generated, cited, one canonical response ----------------


def test_answered_path_builds_cited_response_from_served_evidence(
    corpus_db: sqlite3.Connection,
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    settings = _settings(local_generation_enabled=True)
    client = ScriptedClient(_answered("It swims past the ice floe. [C1]"))
    response = answer_query(
        settings, _lexical_retriever(corpus_db, settings), "walrus", client=client
    )
    assert isinstance(response, AnswerResponse)
    assert response.generation.outcome is GenerationOutcome.ANSWERED
    assert response.generation_available is True
    assert response.abstention is None
    assert response.citations and response.citations[0].index == 1
    assert response.passages == tuple(response.retrieval.results)
    assert client.calls == 1

    # The request was bounded, deadline-capped, and path-free (I16).
    (request,) = client.requests
    assert request.deadline_s == 60.0
    assert request.max_tokens == settings.generation_max_tokens
    assert len(request.prompt) <= 8000
    assert request.evidence
    assert "a.txt" not in request.prompt  # source paths never enter prompts
    assert "walrus" in request.prompt


def test_sequential_calls_are_stateless_and_deterministic(
    corpus_db: sqlite3.Connection,
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    settings = _settings(local_generation_enabled=True)
    client = ScriptedClient(_answered("Yes. [C1]"))
    retriever = _lexical_retriever(corpus_db, settings)
    first = answer_query(settings, retriever, "walrus", client=client)
    second = answer_query(settings, retriever, "walrus", client=client)
    assert client.calls == 2
    assert first == second  # same inputs -> identical response objects


# --- pre-generation abstention (I12): zero client calls ------------------


def test_no_candidates_abstains_before_any_client_call(
    corpus_db: sqlite3.Connection,
) -> None:
    # Empty corpus: outcome no_candidates; even an ENABLED, injected
    # client must never be called (abstention is not overridable).
    settings = _settings(local_generation_enabled=True, generation_model="m")
    client = ScriptedClient(_answered("should never be served [C1]"))
    response = answer_query(
        settings, _lexical_retriever(corpus_db, settings), "walrus", client=client
    )
    assert client.calls == 0
    assert response.generation.outcome is GenerationOutcome.ABSTAINED
    assert response.abstention is not None
    assert response.abstention.reason is AbstentionReason.NO_CANDIDATES
    assert response.generation.attempts == 0
    assert response.generation.text is None
    assert response.passages == ()


def test_insufficient_evidence_after_floor_abstains_without_client(
    corpus_db: sqlite3.Connection, store: QdrantLocalStore
) -> None:
    # Every dense candidate below the applied floor -> insufficient
    # evidence; the client (enabled, injected) is never consulted.
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    embedder = AllBelowEmbedder()
    reconcile_index(corpus_db, store, embedder, collection="chunks")
    settings = _settings(
        retrieval_score_floor=0.5, local_generation_enabled=True, generation_model="m"
    )
    retriever = HybridRetriever(
        corpus_db, settings=settings, embedder=embedder, store=store
    )
    report = retriever.retrieve("walrus")
    assert report.outcome == "insufficient_evidence"
    client = ScriptedClient(_answered("fabricated [C1]"))
    response = answer_query(settings, retriever, "walrus", client=client)
    assert client.calls == 0
    assert response.generation.outcome is GenerationOutcome.ABSTAINED
    assert response.abstention is not None
    assert response.abstention.reason is AbstentionReason.INSUFFICIENT_EVIDENCE


def test_post_verify_empty_results_abstain_as_no_usable_evidence(
    corpus_db: sqlite3.Connection,
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    settings = _settings(local_generation_enabled=True)
    retriever = HybridRetriever(
        corpus_db,
        settings=settings,
        embedder=None,
        store=None,
        post_verify=lambda results: [],  # drops everything
    )
    client = ScriptedClient(_answered("grounded? [C1]"))
    response = answer_query(settings, retriever, "walrus", client=client)
    assert client.calls == 0
    assert response.generation.outcome is GenerationOutcome.ABSTAINED
    assert response.abstention is not None
    assert response.abstention.reason is AbstentionReason.NO_USABLE_EVIDENCE


def test_lexical_only_evidence_with_unset_floor_is_usable_not_abstained(
    corpus_db: sqlite3.Connection,
) -> None:
    # Floor is None (shipped default): dense is dormant, but lexical
    # evidence is real evidence - it must NOT be reinterpreted as
    # floor-rejected. Generation proceeds when enabled.
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    settings = _settings(local_generation_enabled=True)
    assert settings.retrieval_score_floor is None
    retriever = _lexical_retriever(corpus_db, settings)
    assert retriever.retrieve("walrus").mode == "lexical_only"
    client = ScriptedClient(_answered("It swims. [C1]"))
    response = answer_query(settings, retriever, "walrus", client=client)
    assert client.calls == 1
    assert response.generation.outcome is GenerationOutcome.ANSWERED


def test_context_budget_exhaustion_abstains_without_client(
    corpus_db: sqlite3.Connection,
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    settings = _settings(local_generation_enabled=True, generation_prompt_chars=5)
    client = ScriptedClient(_answered("anything [C1]"))
    response = answer_query(
        settings, _lexical_retriever(corpus_db, settings), "walrus", client=client
    )
    assert client.calls == 0
    assert response.generation.outcome is GenerationOutcome.ABSTAINED
    assert response.abstention is not None
    assert response.abstention.reason is AbstentionReason.NO_USABLE_EVIDENCE
    assert response.abstention.detail == "context:budget_exhausted"


# --- availability: disabled / not configured (default state) ------------


def test_default_configuration_is_retrieval_only_and_constructs_nothing(
    corpus_db: sqlite3.Connection,
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    settings = _settings()  # shipped defaults
    assert build_client(settings) is None  # no object capable of a socket
    response = answer_query(settings, _lexical_retriever(corpus_db, settings), "walrus")
    assert response.generation.outcome is GenerationOutcome.UNAVAILABLE
    assert response.generation.failure_category is FailureCategory.DISABLED
    assert response.generation.provider is None
    assert response.generation.attempts == 0
    assert response.generation.text is None
    assert response.abstention is not None
    assert response.abstention.detail == "config:disabled"
    # Retrieval still served its passages with citation-grade provenance.
    assert response.passages
    passage = response.passages[0]
    assert passage.chunk_id and passage.doc_id
    assert passage.char_end > passage.char_start
    assert not response.citations


def test_enabled_without_explicit_model_is_not_configured(
    corpus_db: sqlite3.Connection,
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    settings = _settings(local_generation_enabled=True)  # model unset
    assert build_client(settings) is None  # never guesses a model (5.1)
    response = answer_query(settings, _lexical_retriever(corpus_db, settings), "walrus")
    assert response.generation.failure_category is FailureCategory.NOT_CONFIGURED
    assert response.abstention is not None
    assert response.abstention.detail == "config:model_unset"


def test_configuration_master_switch_disables_an_injected_client(
    corpus_db: sqlite3.Connection,
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    disabled = _settings()  # local_generation_enabled stays False
    client = ScriptedClient(_answered("must not run [C1]"))
    response = answer_query(
        disabled, _lexical_retriever(corpus_db, disabled), "walrus", client=client
    )
    assert client.calls == 0
    assert response.generation.failure_category is FailureCategory.DISABLED


# --- client failure containment -----------------------------------------


def test_raising_client_is_contained_with_a_redacted_typed_result(
    corpus_db: sqlite3.Connection,
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    settings = _settings(local_generation_enabled=True)
    sentinel = "SENTINEL_DETAIL D:\\private\\token-value"
    client = ScriptedClient(RuntimeError(sentinel))
    response = answer_query(
        settings, _lexical_retriever(corpus_db, settings), "walrus", client=client
    )
    assert client.calls == 1
    assert response.generation.outcome is GenerationOutcome.UNAVAILABLE
    assert response.generation.failure_category is FailureCategory.CLIENT_ERROR
    assert response.abstention is not None
    assert response.abstention.detail == "client:RuntimeError"
    # Redaction: the exception message never escapes into the response.
    rendered = repr(response) + str(response.abstention.detail)
    assert "SENTINEL_DETAIL" not in rendered
    assert "token-value" not in rendered
    assert response.generation.text is None  # retrieval-only, no answer


def test_non_contract_client_result_is_a_typed_contract_violation(
    corpus_db: sqlite3.Connection,
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    settings = _settings(local_generation_enabled=True)

    class WrongTyped:
        def generate(self, request):  # noqa: ANN001 - deliberately wrong
            return "a bare string, not a GenerationResult"

        def describe(self):
            return ProviderInfo(provider="wrong", model="m")

    response = answer_query(
        settings,
        _lexical_retriever(corpus_db, settings),
        "walrus",
        client=WrongTyped(),  # type: ignore[arg-type]
    )
    assert response.generation.failure_category is FailureCategory.CLIENT_ERROR
    assert response.abstention is not None
    assert response.abstention.detail == "client:contract_violation"


def test_client_returning_abstained_is_a_contract_violation(
    corpus_db: sqlite3.Connection,
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    settings = _settings(local_generation_enabled=True)
    client = ScriptedClient(
        GenerationResult(
            provider=None,
            model=None,
            text=None,
            outcome=GenerationOutcome.ABSTAINED,
            failure_category=None,
            latency_s=0.0,
            attempts=0,
        )
    )
    response = answer_query(
        settings, _lexical_retriever(corpus_db, settings), "walrus", client=client
    )
    # Abstention is pipeline-owned: a provider abstaining never serves.
    assert response.generation.outcome is GenerationOutcome.UNAVAILABLE
    assert response.generation.failure_category is FailureCategory.CLIENT_ERROR
    assert response.abstention is not None
    assert response.abstention.detail == "client:abstained"


def test_client_unavailable_outcome_passes_through_with_its_category(
    corpus_db: sqlite3.Connection,
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    settings = _settings(local_generation_enabled=True)
    client = ScriptedClient(
        GenerationResult(
            provider="scripted",
            model="scripted-model",
            text=None,
            outcome=GenerationOutcome.UNAVAILABLE,
            failure_category=FailureCategory.TIMEOUT,
            latency_s=1.5,
            attempts=3,
        )
    )
    response = answer_query(
        settings, _lexical_retriever(corpus_db, settings), "walrus", client=client
    )
    assert response.generation.failure_category is FailureCategory.TIMEOUT
    assert response.generation.attempts == 3  # honest attempt accounting
    assert response.abstention is not None
    assert response.abstention.detail == "generation:timeout"
    assert response.passages  # passages still served


# --- citation validation gates the answer --------------------------------


@pytest.mark.parametrize(
    ("text", "detail"),
    [
        ("The model invented a citation. [C99]", "citation:unknown_reference"),
        ("No citations at all, just prose.", "citation:no_citations"),
        ("Broken marker [C].", "citation:malformed"),
    ],
)
def test_invalid_citations_reject_the_whole_answer(
    corpus_db: sqlite3.Connection, text: str, detail: str
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    settings = _settings(local_generation_enabled=True)
    client = ScriptedClient(_answered(text))
    response = answer_query(
        settings, _lexical_retriever(corpus_db, settings), "walrus", client=client
    )
    assert client.calls == 1  # the call happened; the answer was rejected
    assert response.generation.outcome is GenerationOutcome.UNAVAILABLE
    assert response.generation.failure_category is FailureCategory.INVALID_CITATION
    assert response.generation.text is None  # never partially served
    assert response.citations == ()
    assert response.abstention is not None
    assert response.abstention.detail == detail
    assert response.passages  # retrieval evidence still served


def test_injected_client_must_implement_the_protocol(
    corpus_db: sqlite3.Connection,
) -> None:
    settings = _settings(local_generation_enabled=True)
    with pytest.raises(Exception) as excinfo:
        answer_query(
            settings,
            _lexical_retriever(corpus_db, settings),
            "walrus",
            client=object(),
        )
    from rag_platform.generation import GenerationError

    assert isinstance(excinfo.value, GenerationError)
