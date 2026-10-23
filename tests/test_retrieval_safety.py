"""Phase 5 offline-safety tests: the retrieval package imports no model,
network, process, or provider modules (AST-verified), a full hybrid query
opens no sockets and loads no model, and the public facade is explicit.

These tests are the retrieval half of the no-model/no-network guarantees
mirrored from Phases 2-4 (module scans + socket blocking + sys.modules
observation)."""

from __future__ import annotations

import ast
import hashlib
import sqlite3
import socket
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from rag_platform.config import Settings
from rag_platform.db import initialize_database
from rag_platform.ingestion.injection import scan_text
from rag_platform.ingestion.store import ChunkInput, CorpusStore
from rag_platform.retrieval import (
    DEFAULT_COLLECTION,
    RRF_K,
    HybridRetriever,
    build_match_query,
    lexical_search,
    pass_through_post_verify,
    point_id_for_chunk,
    reciprocal_rank_fusion,
    reconcile_index,
)
from rag_platform.vectorstore.qdrant_local import QdrantLocalStore

#: Modules retrieval sources must never import (model/network/process/
#: provider surface). Typed-error imports from rag_platform.embeddings
#: are allowed - their model import is lazy inside the loader.
_FORBIDDEN_IMPORT_ROOTS = (
    "sentence_transformers",
    "torch",
    "httpx",
    "requests",
    "urllib",
    "subprocess",
    "socket",
    "ollama",
    "rag_platform.generation",
    "rag_platform.api",
)


def _imported_modules(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_retrieval_sources_import_no_forbidden_modules() -> None:
    import rag_platform.retrieval as retrieval_package

    package_dir = Path(retrieval_package.__file__).parent
    sources = sorted(package_dir.glob("*.py"))
    assert len(sources) >= 8  # facade + the seven planned modules
    for source in sources:
        for module in _imported_modules(source):
            for forbidden in _FORBIDDEN_IMPORT_ROOTS:
                assert not module.startswith(forbidden), (
                    f"{source.name} imports {module!r}"
                )


class FakeEmbedder:
    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    @property
    def is_loaded(self) -> bool:
        return True


@pytest.fixture
def corpus_db(storage_tmp: Path) -> Iterator[sqlite3.Connection]:
    conn = initialize_database(storage_tmp / "corpus.db")
    try:
        yield conn
    finally:
        conn.close()


def test_full_hybrid_query_opens_no_sockets_and_loads_no_model(
    corpus_db: sqlite3.Connection,
    storage_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access attempted during retrieval")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    text = "The walrus swims past the ice floe."
    CorpusStore(corpus_db).upsert_document(
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
    with QdrantLocalStore(storage_tmp / "vectors", vector_size=2) as store:
        embedder = FakeEmbedder()
        reconcile_index(corpus_db, store, embedder, collection=DEFAULT_COLLECTION)
        settings = Settings(_env_file=None, retrieval_score_floor=0.5)
        report = HybridRetriever(
            corpus_db, settings=settings, embedder=embedder, store=store
        ).retrieve("walrus")
    assert report.outcome == "ok"  # completed without any socket use
    assert "sentence_transformers" not in sys.modules
    assert "torch" not in sys.modules


def test_public_facade_is_explicit() -> None:
    assert RRF_K == 60
    assert DEFAULT_COLLECTION == "chunks"
    assert callable(reciprocal_rank_fusion)
    assert callable(lexical_search)
    assert callable(build_match_query)
    assert callable(reconcile_index)
    assert callable(point_id_for_chunk)
    assert pass_through_post_verify([]) == []
    assert HybridRetriever.__doc__  # documented, not anonymous
