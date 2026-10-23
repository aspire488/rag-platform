"""Phase 6 BG-1b proof (plan v2.4 section 10.3 step 7): the retrieval-to-
generation handoff under guards.

Variant A proves the shipped default state end to end: real retrieval,
zero socket attempts of any kind, and one typed retrieval-only
``AnswerResponse``.

Variant B drives the real ``OllamaClient`` against a dead loopback port
(bind ``127.0.0.1:0`` then close - the port is verified refused, never a
listener): typed ``UNREACHABLE``, <= 3 HTTP calls, inside the 60 s
deadline, with the retrieval evidence still served. All socket activity
stays on loopback; nothing non-loopback is ever attempted.

Staging note: ``/query`` API staging is a Phase 7 deliverable. This
phase proves the handoff at the ``answer_query`` level - the exact
future response object the endpoint will return - without creating any
route.

Offline and deterministic: contained SQLite corpus (FTS5 only), fake
guards over ``socket.connect``/``socket.getaddrinfo``, dead loopback
port only - no model, no provider, no live server, no network.
"""

from __future__ import annotations

import hashlib
import socket
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from rag_platform.config import PROJECT_ROOT, Settings
from rag_platform.db import initialize_database
from rag_platform.generation import (
    MAX_DEADLINE_S,
    AnswerResponse,
    FailureCategory,
    GenerationOutcome,
    answer_query,
    build_client,
)
from rag_platform.ingestion.injection import scan_text
from rag_platform.ingestion.store import ChunkInput, CorpusStore
from rag_platform.retrieval import HybridRetriever

_WALRUS_DOC = "The walrus swims slowly past the ice floe."


def _settings(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {"_env_file": None}
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


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


@pytest.fixture
def corpus_db(storage_tmp: Path) -> Iterator[sqlite3.Connection]:
    conn = initialize_database(storage_tmp / "corpus.db")
    try:
        yield conn
    finally:
        conn.close()


def _lexical_retriever(conn: sqlite3.Connection, settings: Settings) -> HybridRetriever:
    return HybridRetriever(conn, settings=settings, embedder=None, store=None)


def _install_socket_guard(monkeypatch: pytest.MonkeyPatch) -> tuple[list, list]:
    """Record EVERY connect()/getaddrinfo() - loopback or not."""
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
    host = address[0] if isinstance(address, tuple) and address else str(address)
    return (
        str(host) in ("localhost", "127.0.0.1", "::1")
        or str(host).startswith("127.")
        or str(host).startswith("::1")
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


def _assert_retrieval_only(
    response: AnswerResponse, *, category: FailureCategory
) -> None:
    """The retrieval-only shape every unavailable path must present."""
    assert isinstance(response, AnswerResponse)
    assert response.generation.outcome is GenerationOutcome.UNAVAILABLE
    assert response.generation.failure_category is category
    assert response.generation.text is None
    assert response.generation_available is False
    assert response.citations == ()
    assert response.abstention is not None
    # Retrieval evidence still served with citation-grade provenance.
    assert response.passages
    for passage in response.passages:
        assert passage.chunk_id
        assert passage.doc_id
        assert passage.char_end > passage.char_start
        assert str(PROJECT_ROOT) not in passage.source_path  # relative only
    assert response.passages == tuple(response.retrieval.results)


# --- Variant A: default state -------------------------------------------


def test_bg1b_variant_a_default_state_serves_retrieval_only_with_zero_sockets(
    corpus_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    settings = _settings()  # shipped defaults: generation off, egress off
    assert build_client(settings) is None
    connects, dns = _install_socket_guard(monkeypatch)

    response = answer_query(settings, _lexical_retriever(corpus_db, settings), "walrus")

    # Zero socket attempts of ANY kind - not merely non-loopback.
    assert connects == []
    assert dns == []
    _assert_retrieval_only(response, category=FailureCategory.DISABLED)
    assert response.abstention is not None
    assert response.abstention.detail == "config:disabled"
    assert response.generation.attempts == 0


def test_bg1b_variant_a_enabled_without_model_is_not_configured_zero_sockets(
    corpus_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    settings = _settings(local_generation_enabled=True)  # no explicit model
    assert build_client(settings) is None
    connects, dns = _install_socket_guard(monkeypatch)

    response = answer_query(settings, _lexical_retriever(corpus_db, settings), "walrus")

    assert connects == [] and dns == []
    _assert_retrieval_only(response, category=FailureCategory.NOT_CONFIGURED)
    assert response.abstention is not None
    assert response.abstention.detail == "config:model_unset"


# --- Variant B: enabled, unreachable provider on a dead loopback port ----


def test_bg1b_variant_b_unreachable_provider_is_typed_bounded_and_loopback_only(
    corpus_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    dead_port = _dead_loopback_port()
    settings = _settings(
        local_generation_enabled=True,
        generation_model="test-model",
        ollama_base_url=f"http://127.0.0.1:{dead_port}",
    )
    assert build_client(settings) is not None
    connects, dns = _install_socket_guard(monkeypatch)

    started = time.monotonic()
    response = answer_query(settings, _lexical_retriever(corpus_db, settings), "walrus")
    elapsed = time.monotonic() - started

    # Egress: every attempt stayed on loopback; nothing left the host.
    assert connects, "the adapter must actually attempt the local socket"
    assert all(_is_loopback(address) for address in connects)
    assert not [a for a in connects if not _is_loopback(a)]
    assert all(_is_loopback(host) for host in dns)

    # Section 10.1 caps: <= 3 calls (1 + <=2 retries), <= 60 s total.
    assert len(connects) == 3
    assert response.generation.attempts == 3
    assert elapsed < MAX_DEADLINE_S
    assert response.generation.latency_s < MAX_DEADLINE_S

    # Typed, honest failure with retrieval still served (section 4.2).
    _assert_retrieval_only(response, category=FailureCategory.UNREACHABLE)
    assert response.generation.provider == "ollama"  # configured, attempted
    assert response.generation.model == "test-model"
    assert response.abstention is not None
    assert response.abstention.detail == "generation:unreachable"
    # The canonical object is exactly what /query will stage in Phase 7.
    assert isinstance(response, AnswerResponse)
