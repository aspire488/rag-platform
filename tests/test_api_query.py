"""Phase 7 API tests (plan v2.4 section 15 Phase 7 row): /query contract,
canonical Phase 6 response shape, truthful retrieval-only/abstention
outcomes, safe error envelope, health inertness, and trace sanitization
with the documented best-effort failure policy.

Offline and deterministic: in-process ASGI test client, contained SQLite
corpus under ``data/tmp`` (D:), fake clients, and dead loopback ports only
- no server process, no model, no provider, no external service.
"""

from __future__ import annotations

import ast
import hashlib
import json
import socket
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rag_platform.api import QueryResponse, run_query
from rag_platform.config import PROJECT_ROOT, Settings
from rag_platform.db import initialize_database
from rag_platform.generation import (
    AnswerResponse,
    FailureCategory,
    GenerationOutcome,
    GenerationResult,
    OllamaClient,
)
from rag_platform.ingestion.injection import scan_text
from rag_platform.ingestion.store import ChunkInput, CorpusStore
from rag_platform.main import create_app
from rag_platform.retrieval import RetrievalReport, RetrievedChunk

_WALRUS_DOC = "The walrus swims slowly past the ice floe."
_QUERY = "walrus"
_SENTINEL_PROMPT = "PROMPT_SENTINEL_XYZZY no such words should leak"


def _settings(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {"_env_file": None}
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


@pytest.fixture
def corpus_db(storage_tmp: Path) -> Iterator[sqlite3.Connection]:
    conn = initialize_database(storage_tmp / "corpus.db")
    try:
        yield conn
    finally:
        conn.close()


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


def _client(
    storage_tmp: Path, settings: Settings | None = None
) -> tuple[TestClient, Path]:
    db_path = storage_tmp / "corpus.db"
    app = create_app(settings if settings is not None else _settings(), db_path=db_path)
    return TestClient(app), db_path


def _install_socket_guard(monkeypatch: pytest.MonkeyPatch) -> tuple[list, list]:
    """Record EVERY connect()/getaddrinfo() - loopback included."""
    connects: list = []
    dns: list = []
    original_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self, address: object) -> object:
        connects.append(address)
        return original_connect(self, address)  # type: ignore[arg-type]

    def guarded_getaddrinfo(
        host: object, port: object, *args: object, **kwargs: object
    ) -> object:
        dns.append(host)
        return original_getaddrinfo(host, port, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    return connects, dns


def _dead_loopback_port() -> int:
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    finally:
        probe.close()
    return port


# --- 1. canonical response shape ----------------------------------------


def test_query_returns_canonical_phase6_shape(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    client, _ = _client(storage_tmp)
    http = client.post("/query", json={"query": _QUERY})
    assert http.status_code == 200
    body = http.json()
    # Explicit wire schema, canonical fields plus the correlation id.
    assert set(body) == {
        "request_id",
        "retrieval",
        "passages",
        "generation",
        "abstention",
        "citations",
    }
    assert set(body["retrieval"]) == {
        "mode",
        "lexical_only_reason",
        "outcome",
        "top_k",
        "lexical_hits",
        "dense_hits",
        "fused_candidates",
        "below_floor_excluded",
        "unhydrated_skipped",
        "post_verify_dropped",
        "score_floor",
        "results",
    }
    assert set(body["passages"][0]) == {
        "chunk_id",
        "doc_id",
        "ordinal",
        "source_path",
        "content",
        "char_start",
        "char_end",
        "rank",
        "fused_score",
        "dense_cosine_similarity",
        "lexical_bm25",
        "injection_quarantined",
        "injection_finding_count",
    }
    assert set(body["generation"]) == {
        "provider",
        "model",
        "text",
        "outcome",
        "failure_category",
        "latency_s",
        "attempts",
    }
    # Canonical invariant preserved on the wire: passages ARE the results.
    assert body["passages"] == body["retrieval"]["results"]
    # Correlation id is echoed in the header (I2: server-derived).
    assert http.headers["X-Request-ID"] == body["request_id"]
    assert len(body["request_id"]) == 16


def test_query_response_is_a_valid_canonical_answerresponse(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    """The projection maps 1:1 back onto the Phase 6 contracts."""
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    client, db_path = _client(storage_tmp)
    http = client.post("/query", json={"query": _QUERY})
    assert http.status_code == 200
    body = http.json()
    run = run_query(_settings(), _QUERY, db_path=db_path)
    expected = QueryResponse.from_run(run).model_dump(mode="json")
    assert {k: v for k, v in body.items() if k != "request_id"} == {
        k: v for k, v in expected.items() if k != "request_id"
    }
    assert isinstance(run.response, AnswerResponse)


# --- 2/3. retrieval-only success and abstention reasons ------------------


def test_default_configuration_serves_retrieval_only_with_200(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    client, _ = _client(storage_tmp)  # shipped defaults: generation off
    body = client.post("/query", json={"query": _QUERY}).json()
    assert body["generation"]["outcome"] == "unavailable"
    assert body["generation"]["failure_category"] == "disabled"
    assert body["generation"]["text"] is None
    assert body["abstention"]["reason"] == "generation_unavailable"
    assert body["passages"][0]["content"] == _WALRUS_DOC


def test_no_candidates_abstention_end_to_end(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    client, _ = _client(storage_tmp)  # empty corpus
    http = client.post("/query", json={"query": _QUERY})
    assert http.status_code == 200
    body = http.json()
    assert body["retrieval"]["outcome"] == "no_candidates"
    assert body["abstention"]["reason"] == "no_candidates"
    assert body["generation"]["outcome"] == "abstained"
    assert body["passages"] == []


def test_generation_unavailable_abstention_end_to_end(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    client, _ = _client(storage_tmp)
    body = client.post("/query", json={"query": _QUERY}).json()
    assert body["abstention"]["reason"] == "generation_unavailable"
    assert body["abstention"]["detail"] == "config:disabled"


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


def _unavailable_result(category: FailureCategory) -> GenerationResult:
    return GenerationResult(
        provider=None,
        model=None,
        text=None,
        outcome=GenerationOutcome.UNAVAILABLE,
        failure_category=category,
        latency_s=0.0,
        attempts=0,
    )


@pytest.mark.parametrize(
    ("reason", "detail"),
    [
        ("no_candidates", "retrieval:no_candidates"),
        ("insufficient_evidence", "retrieval:insufficient_evidence"),
        ("no_usable_evidence", "context:budget_exhausted"),
        ("generation_unavailable", "config:disabled"),
    ],
)
def test_every_abstention_reason_projects_faithfully(reason: str, detail: str) -> None:
    """All four contract reasons serialize correctly. Reachability note:
    the Phase 7 API surface is lexical-only (dense leg gated behind
    BG-1a/D3), so ``insufficient_evidence``/``no_usable_evidence`` are not
    reachable end-to-end here by design - their projection is proven at the
    boundary, and their pipeline behavior is proven by the Phase 6 tests."""
    from rag_platform.generation.types import Abstention, AbstentionReason

    response = AnswerResponse(
        retrieval=_retrieval_report(()),
        passages=(),
        generation=(
            GenerationResult(
                provider=None,
                model=None,
                text=None,
                outcome=GenerationOutcome.ABSTAINED,
                failure_category=None,
                latency_s=0.0,
                attempts=0,
            )
            if reason != "generation_unavailable"
            else _unavailable_result(FailureCategory.DISABLED)
        ),
        abstention=Abstention(reason=AbstentionReason(reason), detail=detail),
        citations=(),
    )
    from rag_platform.api import QueryRun

    body = QueryResponse.from_run(
        QueryRun(response=response, request_id="a" * 16, total_ms=1.0)
    ).model_dump(mode="json")
    assert body["abstention"] == {"reason": reason, "detail": detail}
    assert body["generation"]["text"] is None
    assert body["citations"] == []


# --- 4. disabled/unconfigured generation never constructs a client -------


def test_generation_disabled_or_unconfigured_never_constructs_a_client(
    corpus_db: sqlite3.Connection, storage_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("an OllamaClient must not be constructed")

    monkeypatch.setattr(OllamaClient, "__init__", boom)
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    for settings in (
        _settings(),  # disabled
        _settings(local_generation_enabled=True),  # enabled, no model
    ):
        client, _ = _client(storage_tmp, settings)
        http = client.post("/query", json={"query": _QUERY})
        assert http.status_code == 200
    # monkeypatch teardown restores the real constructor


def test_enabled_without_model_is_not_configured(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    client, _ = _client(storage_tmp, _settings(local_generation_enabled=True))
    body = client.post("/query", json={"query": _QUERY}).json()
    assert body["generation"]["failure_category"] == "not_configured"
    assert body["abstention"]["detail"] == "config:model_unset"


# --- 5. provider-unavailable and invalid-citation stay truthful ----------


def test_unreachable_provider_is_200_truthful_retrieval_only(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    dead_port = _dead_loopback_port()
    client, _ = _client(
        storage_tmp,
        _settings(
            local_generation_enabled=True,
            generation_model="test-model",
            ollama_base_url=f"http://127.0.0.1:{dead_port}",
        ),
    )
    started = time.monotonic()
    http = client.post("/query", json={"query": _QUERY})
    elapsed = time.monotonic() - started
    assert http.status_code == 200  # a provider failure is NOT an HTTP 500
    body = http.json()
    assert body["generation"]["outcome"] == "unavailable"
    assert body["generation"]["failure_category"] == "unreachable"
    assert body["generation"]["attempts"] == 3  # bounded, section 10.1
    assert elapsed < 60.0
    assert body["passages"]  # retrieval evidence still served


def test_invalid_citation_answer_is_200_truthful_retrieval_only(
    corpus_db: sqlite3.Connection, storage_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)

    class FabricatingClient:
        def generate(self, request):  # noqa: ANN001 - protocol shape
            return GenerationResult(
                provider="scripted",
                model="scripted-model",
                text="The model invented this citation. [C99]",
                outcome=GenerationOutcome.ANSWERED,
                failure_category=None,
                latency_s=0.01,
                attempts=1,
            )

        def describe(self):
            from rag_platform.generation import ProviderInfo

            return ProviderInfo(provider="scripted", model="scripted-model")

    import rag_platform.generation.pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module, "build_client", lambda settings: FabricatingClient()
    )
    # generation_model must be set: the pipeline checks it before it ever
    # reaches build_client (the NOT_CONFIGURED short-circuit, plan 5.1).
    client, _ = _client(
        storage_tmp,
        _settings(local_generation_enabled=True, generation_model="test-model"),
    )
    http = client.post("/query", json={"query": _QUERY})
    assert http.status_code == 200
    body = http.json()
    assert body["generation"]["failure_category"] == "invalid_citation"
    assert body["generation"]["text"] is None  # never partially served
    assert body["citations"] == []
    assert body["passages"]


# --- 6/7. malformed, oversized, and leak-free errors ---------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},  # missing query
        {"query": ""},  # empty
        {"query": 420042},  # wrong type
        {"query": ["walrus"]},  # wrong type
        {"query": "walrusXYZ", "extra": "extraFIELD"},  # extra field forbidden
    ],
)
def test_malformed_requests_are_422_without_echoing_input(
    corpus_db: sqlite3.Connection, storage_tmp: Path, payload: dict
) -> None:
    client, _ = _client(storage_tmp)
    http = client.post("/query", json=payload)
    assert http.status_code == 422
    body = http.json()
    assert body["error"]["category"] == "invalid_request"
    # No field values are echoed (I6) - distinctive inputs never appear.
    assert "walrusXYZ" not in http.text
    assert "extraFIELD" not in http.text
    assert "420042" not in http.text
    assert "Traceback" not in http.text
    assert str(PROJECT_ROOT) not in http.text


def test_invalid_json_body_is_422_safe(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    client, _ = _client(storage_tmp)
    http = client.post(
        "/query",
        content=b'{"query": "unterminated',
        headers={"Content-Type": "application/json"},
    )
    assert http.status_code == 422
    assert http.json()["error"]["category"] == "invalid_request"
    assert "unterminated" not in http.text  # the raw body is never echoed


def test_oversized_query_over_the_retrieval_cap_is_422(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    client, _ = _client(storage_tmp)  # default cap: 512 chars
    sentinel = "Z" * 600
    http = client.post("/query", json={"query": sentinel})
    assert http.status_code == 422
    body = http.json()
    assert body["error"]["category"] == "invalid_request"
    assert "Z" * 10 not in http.text  # the oversized prompt is never echoed


def test_oversized_body_is_413_before_parsing(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    client, _ = _client(storage_tmp)
    http = client.post("/query", json={"query": "Q" * (20 * 1024)})
    assert http.status_code == 413
    assert http.json()["error"]["category"] == "request_too_large"
    assert "Q" * 10 not in http.text


def test_get_query_is_405_and_unknown_route_is_404(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    client, _ = _client(storage_tmp)
    assert client.get("/query").status_code == 405
    assert client.get("/nope").status_code == 404


def test_storage_failure_is_503_safe(storage_tmp: Path) -> None:
    # A db_path that IS a directory cannot be opened: typed storage failure.
    blocked = storage_tmp / "blocked"
    blocked.mkdir()
    client = TestClient(create_app(_settings(), db_path=blocked))
    http = client.post("/query", json={"query": _QUERY})
    assert http.status_code == 503
    body = http.json()
    assert body["error"]["category"] == "storage_unavailable"
    assert str(PROJECT_ROOT) not in http.text  # paths never echoed


def test_unexpected_internal_error_is_500_class_only(
    corpus_db: sqlite3.Connection,
    storage_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)

    def explode(settings, query, *, db_path, request_id=None):  # noqa: ANN001
        raise RuntimeError("INTERNAL_SENTINEL D:\\secret\\token")

    monkeypatch.setattr("rag_platform.main.run_query", explode)
    client, _ = _client(storage_tmp)
    with caplog.at_level("WARNING", logger="rag_platform"):
        http = client.post("/query", json={"query": _QUERY})
    assert http.status_code == 500
    assert http.json()["error"] == {
        "category": "internal_error",
        "detail": "unexpected internal error",
    }
    assert "INTERNAL_SENTINEL" not in http.text
    assert "Traceback" not in http.text
    # The class name is logged, never the message (I6).
    assert "RuntimeError" in caplog.text
    assert "INTERNAL_SENTINEL" not in caplog.text


# --- 8. health and query path: no sockets, no model imports --------------


def test_health_and_default_query_open_no_sockets(
    corpus_db: sqlite3.Connection, storage_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    client, _ = _client(storage_tmp)
    # Enter the TestClient context BEFORE the guard: starting its blocking
    # portal spins up the asyncio event loop, whose Windows self-pipe is a
    # loopback-only socketpair() connect of the TEST HARNESS - not the app.
    # Inside the guarded region the portal is reused, so every socket
    # operation the /health and /query requests perform is recorded.
    with client:
        connects, dns = _install_socket_guard(monkeypatch)
        health = client.get("/health")
        http = client.post("/query", json={"query": _QUERY})
        assert health.status_code == 200
        assert http.status_code == 200
        assert connects == [] and dns == []


def test_health_contract_is_unchanged_and_probes_nothing(
    corpus_db: sqlite3.Connection, storage_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(storage_tmp)
    with client:  # portal (event loop self-pipe) starts before the guard
        connects, dns = _install_socket_guard(monkeypatch)
        response = client.get("/health")
        assert response.status_code == 200
        assert connects == [] and dns == []
        text = response.text
        assert "://" not in text and "\\" not in text and "11434" not in text


def test_api_surface_never_imports_models_or_vector_store() -> None:
    """The Phase 7 API surface is lexical-only by construction (I3/BG-1a)."""
    sources = [
        Path(rag_platform_module.__file__)
        for rag_platform_module in (
            __import__("rag_platform.api", fromlist=["x"]),
            __import__("rag_platform.main", fromlist=["x"]),
            __import__("rag_platform.cli", fromlist=["x"]),
            __import__("rag_platform.tracing", fromlist=["x"]),
        )
    ]
    banned = (
        "rag_platform.embeddings",
        "rag_platform.vectorstore",
        "sentence_transformers",
        "qdrant_client",
        "torch",
    )
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not any(
                    name == bad or name.startswith(bad + ".") for bad in banned
                ), f"{source.name} imports {name}"


# --- 9. trace sanitization and the documented failure policy -------------

_TRACE_TABLES = ("traces", "trace_stages", "trace_authz")


def _dump_trace_tables(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        rows: list = []
        for table in _TRACE_TABLES:
            rows.extend(conn.execute(f"SELECT * FROM {table}").fetchall())  # noqa: S608
        return json.dumps([list(row) for row in rows])
    finally:
        conn.close()


def test_query_writes_a_sanitized_trace(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    client, db_path = _client(storage_tmp)
    query = _QUERY
    http = client.post("/query", json={"query": query})
    assert http.status_code == 200
    request_id = http.json()["request_id"]

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        trace = conn.execute(
            "SELECT * FROM traces WHERE request_id = ?", (request_id,)
        ).fetchone()
        assert trace is not None
        assert trace["served_by"] == "retrieval_only"
        assert trace["degradation_reason"] == "disabled"
        assert trace["cache_status"] == "not_implemented"
        assert trace["tenant_id"] is None  # M2 has not built identity yet
        stages = conn.execute(
            "SELECT stage, outcome, timing_ms, inputs_digest, outputs_digest, "
            "scores_json, excerpt FROM trace_stages WHERE trace_id = ? ORDER BY id",
            (trace["id"],),
        ).fetchall()
        assert [stage["stage"] for stage in stages] == [
            "retrieval",
            "generation",
            "response",
        ]
        assert stages[0]["outcome"] == "ok"
        assert stages[1]["outcome"] == "unavailable"
        assert stages[2]["outcome"] == "unavailable"
        assert stages[2]["timing_ms"] >= 0.0
        scores = json.loads(stages[0]["scores_json"])
        assert set(scores) == {
            "top_k",
            "lexical_hits",
            "dense_hits",
            "fused_candidates",
            "below_floor_excluded",
            "score_floor",
        }
        # Digests are64-hex SHA-256 forms; content never appears.
        for stage in stages:
            assert stage["excerpt"] is None  # never excerpted pre-M2 (I10)
            for digest in (stage["inputs_digest"], stage["outputs_digest"]):
                if digest is not None:
                    assert len(digest) == 64 and int(digest, 16) >= 0
        # No authorization decisions exist to record before M2.
        assert conn.execute("SELECT count(*) FROM trace_authz").fetchone()[0] == 0
    finally:
        conn.close()

    dumped = _dump_trace_tables(db_path)
    assert query not in dumped  # raw queries are never persisted
    assert _WALRUS_DOC not in dumped  # retrieved content never enters traces
    assert str(PROJECT_ROOT) not in dumped


def test_trace_failure_never_alters_the_response(
    corpus_db: sqlite3.Connection,
    storage_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Documented policy: trace persistence is best-effort and non-blocking."""
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    client, db_path = _client(storage_tmp)
    baseline = client.post("/query", json={"query": _QUERY}).json()

    def failing_trace(conn, **kwargs):  # noqa: ANN001
        raise RuntimeError("TRACE_SENTINEL must never surface")

    monkeypatch.setattr("rag_platform.api.record_query_trace", failing_trace)
    with caplog.at_level("WARNING", logger="rag_platform"):
        http = client.post("/query", json={"query": _QUERY})
    assert http.status_code == 200  # the response is NOT converted to an error
    body = http.json()
    assert "TRACE_SENTINEL" not in http.text
    assert "RuntimeError" in caplog.text  # class name logged only
    assert "TRACE_SENTINEL" not in caplog.text
    # And the response is field-for-field the same as the traced run.
    assert {k: v for k, v in body.items() if k != "request_id"} == {
        k: v for k, v in baseline.items() if k != "request_id"
    }
    # No trace row was written by the failing run.
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT count(*) FROM traces").fetchone()[0]
    finally:
        conn.close()
    assert count == 1  # only the baseline run's trace exists
