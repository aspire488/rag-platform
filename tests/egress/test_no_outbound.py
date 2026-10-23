"""Phase 1 egress instrumentation (plan v2.4 section 12: egress-off row, stage 1).

With ``EGRESS_MODE=off`` the app must perform zero non-loopback socket
operations. The ASGI test transport is in-process, so any ``connect()`` or
``getaddrinfo()`` aimed at a non-loopback host would indicate the application
itself reaching out - this test fails loudly if it ever happens. The full
socket-instrumented suite across the whole test run arrives at Phase 6 (M1 gate
8); this is the Phase 1 config-rejection stage of that check.

Phase 6 rows (appended): the retrieval-to-generation path under a socket guard
that records EVERY attempt - default state must open none at all, an enabled
unreachable provider must stay strictly on loopback inside its attempt/deadline
caps, and a mocked provider response must complete with zero sockets.
"""

from __future__ import annotations

import hashlib
import socket
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from rag_platform.config import Settings
from rag_platform.db import initialize_database
from rag_platform.generation import (
    MAX_DEADLINE_S,
    AnswerResponse,
    FailureCategory,
    GenerationOutcome,
    OllamaClient,
    answer_query,
)
from rag_platform.ingestion.injection import scan_text
from rag_platform.ingestion.store import ChunkInput, CorpusStore
from rag_platform.main import create_app
from rag_platform.retrieval import HybridRetriever


def _is_loopback_host(host: str) -> bool:
    return (
        host in ("localhost", "127.0.0.1", "::1")
        or host.startswith("127.")
        or host.startswith("::1")
    )


def test_health_performs_no_outbound_sockets_with_egress_off(monkeypatch) -> None:
    connect_attempts: list[object] = []
    dns_attempts: list[object] = []

    original_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self, address: object) -> object:
        host = address[0] if isinstance(address, tuple) and address else str(address)
        if not _is_loopback_host(str(host)):
            connect_attempts.append(address)
        return original_connect(self, address)  # type: ignore[arg-type]

    def guarded_getaddrinfo(
        host: object, port: object, *args: object, **kwargs: object
    ) -> object:
        if not _is_loopback_host(str(host)):
            dns_attempts.append(host)
        return original_getaddrinfo(host, port, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)

    # App construction happens under the guard too: config validation itself
    # must open no sockets.
    app = create_app(Settings(_env_file=None, egress_mode="off"))
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert connect_attempts == []
    assert dns_attempts == []


# --- Phase 6 rows: retrieval -> generation under the socket guard --------

_WALRUS_DOC = "The walrus swims slowly past the ice floe."


def _install_total_guard(monkeypatch) -> tuple[list, list]:
    """Record EVERY ``connect()``/``getaddrinfo()`` - loopback included."""
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


def _is_loopback(address: object) -> bool:
    host = str(address[0]) if isinstance(address, tuple) and address else str(address)
    return (
        host in ("localhost", "127.0.0.1", "::1")
        or host.startswith("127.")
        or host.startswith("::1")
    )


def _dead_loopback_port() -> int:
    """Bind an ephemeral loopback port, then close it: verified refused."""
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    finally:
        probe.close()
    return port


def _corpus_with_walrus(storage_tmp: Path) -> sqlite3.Connection:
    conn = initialize_database(storage_tmp / "egress-corpus.db")
    text = _WALRUS_DOC
    CorpusStore(conn).upsert_document(
        source_rel_path="a.txt",
        source_name="a.txt",
        file_format="txt",
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
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
    return conn


def test_retrieval_to_generation_default_path_opens_no_sockets_at_all(
    storage_tmp: Path, monkeypatch
) -> None:
    """Egress row: shipped defaults -> retrieval runs, generation is inert."""
    conn = _corpus_with_walrus(storage_tmp)
    try:
        settings = Settings(_env_file=None, egress_mode="off")
        retriever = HybridRetriever(conn, settings=settings, embedder=None, store=None)
        connects, dns = _install_total_guard(monkeypatch)

        response = answer_query(settings, retriever, "walrus")

        # Not merely non-loopback: the default path attempts nothing.
        assert connects == []
        assert dns == []
        assert isinstance(response, AnswerResponse)
        assert response.generation.outcome is GenerationOutcome.UNAVAILABLE
        assert response.generation.failure_category is FailureCategory.DISABLED
        assert response.generation.text is None
        assert response.passages  # evidence still served, retrieval-only
    finally:
        conn.close()


def test_enabled_unreachable_generation_stays_loopback_and_within_caps(
    storage_tmp: Path, monkeypatch
) -> None:
    """Egress row: enabled local provider -> only loopback, <= 3 calls, <= 60 s."""
    conn = _corpus_with_walrus(storage_tmp)
    try:
        dead_port = _dead_loopback_port()
        settings = Settings(
            _env_file=None,
            egress_mode="off",
            local_generation_enabled=True,
            generation_model="test-model",
            ollama_base_url=f"http://127.0.0.1:{dead_port}",
        )
        retriever = HybridRetriever(conn, settings=settings, embedder=None, store=None)
        connects, dns = _install_total_guard(monkeypatch)

        started = time.monotonic()
        response = answer_query(settings, retriever, "walrus")
        elapsed = time.monotonic() - started

        # Zero non-loopback activity of any kind.
        assert [a for a in connects if not _is_loopback(a)] == []
        assert [h for h in dns if not _is_loopback(h)] == []
        # Section 10.1 caps: bounded attempts and bounded wall time.
        assert 1 <= len(connects) <= 3
        assert elapsed < MAX_DEADLINE_S
        # The failure degraded to retrieval-only - never a fabricated answer.
        assert response.generation.outcome is GenerationOutcome.UNAVAILABLE
        assert response.generation.failure_category is FailureCategory.UNREACHABLE
        assert response.generation.text is None
        assert response.passages
    finally:
        conn.close()


def test_mocked_provider_response_completes_with_zero_sockets(
    storage_tmp: Path, monkeypatch
) -> None:
    """Egress row: the success path itself opens no sockets (in-process mock)."""
    conn = _corpus_with_walrus(storage_tmp)
    try:
        settings = Settings(
            _env_file=None, egress_mode="off", local_generation_enabled=True
        )
        retriever = HybridRetriever(conn, settings=settings, embedder=None, store=None)
        mock_client = OllamaClient(
            base_url="http://127.0.0.1:11434",
            model="test-model",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(  # noqa: ARG005
                    200, json={"response": "It swims past the ice floe. [C1]"}
                )
            ),
        )
        connects, dns = _install_total_guard(monkeypatch)

        try:
            response = answer_query(settings, retriever, "walrus", client=mock_client)
        finally:
            mock_client.close()

        # The full answered flow ran in-process: zero socket attempts.
        assert connects == []
        assert dns == []
        assert response.generation.outcome is GenerationOutcome.ANSWERED
        assert response.generation.text is not None
        assert response.citations  # cited, grounded in served evidence
        assert response.abstention is None
    finally:
        conn.close()


# --- Phase 7 rows: /query endpoint and CLI under the socket guard --------


def test_api_query_default_path_opens_no_sockets(
    storage_tmp: Path, monkeypatch
) -> None:
    """Egress row: POST /query end to end (SQLite + retrieval + trace) with
    the shipped defaults - zero socket attempts of any kind."""
    conn = _corpus_with_walrus(storage_tmp)
    try:
        app = create_app(
            Settings(_env_file=None, egress_mode="off"),
            db_path=storage_tmp / "egress-corpus.db",
        )
        client = TestClient(app)
        # Enter the client first: its portal (the asyncio event loop whose
        # Windows self-pipe is a loopback socketpair of the test harness)
        # starts before the guard. Inside, the portal is reused, so every
        # socket operation performed while serving /query is recorded.
        with client:
            connects, dns = _install_total_guard(monkeypatch)

            response = client.post("/query", json={"query": "walrus"})

            assert response.status_code == 200
            body = response.json()
            assert body["generation"]["outcome"] == "unavailable"
            assert body["generation"]["failure_category"] == "disabled"
            assert body["passages"]  # retrieval-only, evidence served
            assert connects == []
            assert dns == []
    finally:
        conn.close()


def test_cli_query_default_path_opens_no_sockets(
    storage_tmp: Path, monkeypatch
) -> None:
    """Egress row: the CLI query path is the same bounded, socketless flow."""
    import io

    from rag_platform import cli as cli_module

    conn = _corpus_with_walrus(storage_tmp)
    try:
        connects, dns = _install_total_guard(monkeypatch)
        stdout = io.StringIO()
        code = cli_module.main(
            ["--db", str(storage_tmp / "egress-corpus.db"), "query", "walrus"],
            settings=Settings(_env_file=None, egress_mode="off"),
            stdout=stdout,
        )
        assert code == 0
        assert "generation_outcome: unavailable" in stdout.getvalue()
        assert connects == []
        assert dns == []
    finally:
        conn.close()
