"""Phase 5 lexical retrieval tests: FTS5 ranking over a Phase 4 corpus,
deterministic query sanitization against the full metacharacter surface,
token/length bounds, and honest error contracts.

Offline and deterministic: one contained SQLite file per test (real
migrations), tiny synthetic documents, no network, no model, no services.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from rag_platform.db import initialize_database
from rag_platform.ingestion.injection import scan_text
from rag_platform.ingestion.store import (
    ChunkInput,
    CorpusStore,
    chunk_id,
    document_id,
)
from rag_platform.retrieval.errors import LexicalIndexError, QueryValidationError
from rag_platform.retrieval.lexical import build_match_query, lexical_search

_MAX_CHARS = 512


@pytest.fixture
def corpus_db(storage_tmp: Path) -> Iterator[sqlite3.Connection]:
    conn = initialize_database(storage_tmp / "corpus.db")
    try:
        yield conn
    finally:
        conn.close()


def _ingest(conn: sqlite3.Connection, rel_path: str, text: str) -> str:
    """Persist *text* as one chunk via the real Phase 4 store path."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    outcome = CorpusStore(conn).upsert_document(
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
    return chunk_id(outcome.doc_id, 0)


def test_lexical_matches_only_documents_containing_the_term(
    corpus_db: sqlite3.Connection,
) -> None:
    walrus = _ingest(corpus_db, "a.txt", "The walrus swims past the ice floe.")
    _ingest(corpus_db, "b.txt", "Elephants wander the savanna at dawn.")
    hits = lexical_search(corpus_db, "walrus", limit=10, max_query_chars=_MAX_CHARS)
    assert [h.chunk_id for h in hits] == [walrus]
    assert hits[0].rank == 1
    assert hits[0].bm25 < 0.0  # bm25(): negative-better ranking signal


def test_lexical_ranks_bm25_ascending(corpus_db: sqlite3.Connection) -> None:
    term_heavy_short = _ingest(
        corpus_db, "short.txt", "Walrus. Walrus. Walrus."
    )  # term-heavy and short -> best bm25
    _ingest(
        corpus_db,
        "long.txt",
        "The walrus naps. " + "Filler words pad this long document. " * 8,
    )
    hits = lexical_search(corpus_db, "walrus", limit=10, max_query_chars=_MAX_CHARS)
    assert len(hits) >= 2
    assert hits[0].chunk_id == term_heavy_short
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))
    assert all(hits[i].bm25 <= hits[i + 1].bm25 for i in range(len(hits) - 1))


def test_lexical_breaks_bm25_ties_by_chunk_id(corpus_db: sqlite3.Connection) -> None:
    """Phase 8 determinism: tied BM25 scores order by ``chunk_id ASC``.

    The pair is chosen so ingestion (rowid) order *contradicts* chunk_id
    order: the larger chunk_id is inserted first. An implementation that
    orders ties only by score therefore returns index order and fails
    this test; the ``chunk_id ASC`` secondary key makes the served order
    content-derived and stable (plan section 9.1).
    """
    text = "The walrus patrols the frozen inlet every winter night."
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    # Precompute content-derived chunk ids for candidate paths, then keep
    # the two whose ids straddle: ingest the LARGER id first so rowid
    # order is the reverse of the required chunk_id ASC order.
    ordered = sorted(
        (chunk_id(document_id(path, digest), 0), path)
        for path in (f"tie{i}.txt" for i in range(12))
    )
    (small_id, small_path), (large_id, large_path) = ordered[0], ordered[-1]
    assert small_id < large_id  # by construction of sorted()

    large = _ingest(corpus_db, large_path, text)  # rowid 1: larger chunk_id
    small = _ingest(corpus_db, small_path, text)  # rowid 2: smaller chunk_id
    assert large > small  # rowid order now opposes chunk_id order

    hits = lexical_search(corpus_db, "walrus", limit=10, max_query_chars=_MAX_CHARS)
    assert [h.chunk_id for h in hits] == [small, large]  # chunk_id ASC, not rowid
    assert hits[0].bm25 == hits[1].bm25  # genuinely tied scores
    assert [h.rank for h in hits] == [1, 2]

    # Determinism across repeated evaluations of the same query.
    again = lexical_search(corpus_db, "walrus", limit=10, max_query_chars=_MAX_CHARS)
    assert [(h.chunk_id, h.rank, h.bm25) for h in again] == [
        (h.chunk_id, h.rank, h.bm25) for h in hits
    ]


def test_lexical_is_case_insensitive(corpus_db: sqlite3.Connection) -> None:
    walrus = _ingest(corpus_db, "a.txt", "The walrus sings at dusk.")
    assert [
        h.chunk_id
        for h in lexical_search(
            corpus_db, "WALRUS", limit=5, max_query_chars=_MAX_CHARS
        )
    ] == [walrus]


def test_lexical_supports_cjk_query_terms(corpus_db: sqlite3.Connection) -> None:
    # unicode61 (no ICU segmentation) treats a contiguous CJK run as ONE
    # token; the sanitized quoted phrase matches iff it equals that run.
    doc = _ingest(corpus_db, "cjk.txt", "海象 在浮冰边游泳。唱歌。")
    hits = lexical_search(corpus_db, "海象", limit=5, max_query_chars=_MAX_CHARS)
    assert [h.chunk_id for h in hits] == [doc]


def test_contiguous_cjk_run_is_one_token_documented_limitation(
    corpus_db: sqlite3.Connection,
) -> None:
    # Deterministic documented limitation (CJK caveat family, plan/Phase 4):
    # a query covering only part of a contiguous CJK token does not match.
    _ingest(corpus_db, "cjk.txt", "海象在浮冰边游泳。")
    assert lexical_search(corpus_db, "海象", limit=5, max_query_chars=_MAX_CHARS) == []
    assert (
        lexical_search(
            corpus_db, "海象在浮冰边游泳", limit=5, max_query_chars=_MAX_CHARS
        )
        != []
    )


def test_build_match_query_quotes_every_token() -> None:
    assert build_match_query("big walrus", max_query_chars=_MAX_CHARS) == (
        '"big" OR "walrus"'
    )


@pytest.mark.parametrize(
    "query",
    [
        "AND",
        "OR walrus",
        "NEAR(ice floe)",
        "walrus*",
        "^walrus",
        "chunk_id:abc doc_id:def",
        '"unbalanced quote',
        'wa"lrus',
        "a; DROP TABLE chunks; --",
        "(walrus) NOT elephant",
        "walrus  elephant",  # repeated separator
        "emoji \U0001f9ca test",
        "  ",
    ],
)
def test_sanitized_queries_never_raise_or_leak_syntax(
    corpus_db: sqlite3.Connection, query: str
) -> None:
    _ingest(corpus_db, "a.txt", "The walrus swims past the ice floe.")
    result = lexical_search(corpus_db, query, limit=10, max_query_chars=_MAX_CHARS)
    assert isinstance(result, list)  # deterministic outcome, typed or empty


def test_tokenless_query_is_deterministic_empty(corpus_db: sqlite3.Connection) -> None:
    _ingest(corpus_db, "a.txt", "The walrus swims past the ice floe.")
    assert build_match_query("!!! ??? ...", max_query_chars=_MAX_CHARS) == ""
    assert lexical_search(corpus_db, "!!! ??? ...", limit=5, max_query_chars=512) == []


def test_over_cap_query_raises_typed_error_without_echoing_text(
    corpus_db: sqlite3.Connection,
) -> None:
    with pytest.raises(QueryValidationError) as excinfo:
        lexical_search(corpus_db, "walrus secret-text", limit=5, max_query_chars=10)
    message = str(excinfo.value)
    assert "18" in message and "10" in message
    assert "walrus" not in message and "secret-text" not in message


def test_token_count_is_bounded() -> None:
    query = " ".join(f"token{i}" for i in range(40))
    match = build_match_query(query, max_query_chars=2000)
    assert match.count('"') // 2 == 32  # _MAX_TOKENS emitted phrases


def test_empty_index_returns_empty(corpus_db: sqlite3.Connection) -> None:
    assert (
        lexical_search(corpus_db, "walrus", limit=5, max_query_chars=_MAX_CHARS) == []
    )


def test_invalid_limit_is_rejected(corpus_db: sqlite3.Connection) -> None:
    for bad in (0, -3, True, 2.5):  # type: ignore[arg-type]
        with pytest.raises(QueryValidationError):
            lexical_search(corpus_db, "x", limit=bad, max_query_chars=_MAX_CHARS)  # type: ignore[arg-type]


def test_non_string_query_is_rejected() -> None:
    with pytest.raises(QueryValidationError):
        build_match_query(123, max_query_chars=_MAX_CHARS)  # type: ignore[arg-type]


def test_closed_connection_surfaces_typed_error(corpus_db: sqlite3.Connection) -> None:
    _ingest(corpus_db, "a.txt", "The walrus swims past the ice floe.")
    corpus_db.close()
    with pytest.raises(LexicalIndexError) as excinfo:
        lexical_search(corpus_db, "walrus", limit=5, max_query_chars=_MAX_CHARS)
    assert "ProgrammingError" in str(excinfo.value)  # class name only, no message
    assert isinstance(excinfo.value.__cause__, sqlite3.Error)
