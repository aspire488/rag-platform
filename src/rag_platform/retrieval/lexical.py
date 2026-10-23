"""SQLite FTS5 lexical retrieval (plan v2.4 Phase 5; D5 storage, R20).

Queries the standalone ``chunk_fts`` table that Phase 4 ingestion populates
in the same transaction as the corpus (columns: content, chunk_id
UNINDEXED, doc_id UNINDEXED, unicode61 tokenizer). BM25 is used strictly
as a ranking signal: ``bm25(chunk_fts)`` returns negative-better values,
results are ordered ascending, and the raw value is never treated as a
calibrated relevance score or compared to the dense score floor.

Query sanitization (deterministic, I7-bounded):

- The query's length is capped by ``max_query_chars`` (validated
  configuration) - over-cap raises :class:`QueryValidationError` whose
  message carries the two character counts only, never the query text.
- The query is split into maximal runs of alphanumeric characters (a
  documented approximation of the unicode61 token class; CJK ideographs
  are alphanumeric and therefore form ordinary tokens) and each token is
  re-emitted as a *quoted FTS5 string phrase*. Tokens are alphanumeric by
  construction, so they cannot contain quotes or syntax characters:
  ``AND``/``OR``/``NEAR``/``*``/``^``/``:``/unbalanced quotes all become
  plain search terms. The MATCH argument stays a bound SQL parameter and
  the SQL statement shape is a fixed literal in this module - user input
  can never alter statement structure or execute SQL.
- Tokens are deduplicated (order-preserving) and bounded (``_MAX_TOKENS``);
  the emitted phrase list is joined with ``OR``: the lexical leg
  optimizes recall for rank fusion, and BM25 ordering supplies precision.
- A query with no tokens (empty, whitespace-only, punctuation-only, or
  entirely non-alphanumeric) returns ``[]`` WITHOUT touching the index - a
  deterministic empty result, never an error.

Any ``sqlite3`` failure surfaces as :class:`LexicalIndexError` (exception
class name only, cause chained) - nothing is swallowed, and unrelated
database errors are never hidden. Per the logging invariant in
``rag_platform.ingestion.errors``, never log these with ``exc_info``.
"""

from __future__ import annotations

import sqlite3

from rag_platform.retrieval.errors import LexicalIndexError, QueryValidationError
from rag_platform.retrieval.types import LexicalHit

#: I7 bound on tokens emitted per query (single source for the cap).
_MAX_TOKENS = 32

#: Joiner for the sanitized quoted phrases (documented recall choice).
_TOKEN_JOINER = " OR "


def _tokenize(query: str) -> list[str]:
    """Maximal alphanumeric runs; order-preserving dedupe; token cap."""
    tokens: list[str] = []
    seen: set[str] = set()
    current: list[str] = []
    for ch in query:
        if ch.isalnum():
            current.append(ch)
            continue
        if current:
            token = "".join(current)
            current = []
            if token not in seen:
                seen.add(token)
                tokens.append(token)
            if len(tokens) >= _MAX_TOKENS:
                break
    if current and len(tokens) < _MAX_TOKENS:
        token = "".join(current)
        if token not in seen:
            tokens.append(token)
    return tokens[:_MAX_TOKENS]


def build_match_query(query: str, *, max_query_chars: int) -> str:
    """Sanitized FTS5 MATCH expression for *query* ('' when tokenless).

    Deterministic and safe-by-construction: every token becomes a quoted
    phrase, so no user input can change the expression's structure.
    Raises :class:`QueryValidationError` for a non-string query or one
    longer than *max_query_chars*.
    """
    if isinstance(query, bool) or not isinstance(query, str):
        raise QueryValidationError(
            f"query must be a string; got {type(query).__name__}"
        )
    if len(query) > max_query_chars:
        raise QueryValidationError(
            f"query length {len(query)} exceeds the configured limit of "
            f"{max_query_chars} characters"
        )
    tokens = _tokenize(query)
    return _TOKEN_JOINER.join(f'"{token}"' for token in tokens)


def lexical_search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    max_query_chars: int,
) -> list[LexicalHit]:
    """Ranked FTS5 results for *query* (1-based bm25 order, bounded).

    Contract: tokenless/empty query -> ``[]`` without touching the index;
    an empty index -> ``[]``; the SQL shape is fixed and every value is a
    bound parameter; SQL failures -> typed :class:`LexicalIndexError`.
    The upper bound on *limit* is the validated configuration
    (``retrieval_lexical_candidates``) - config stays the single source of
    truth; this layer validates only that the limit is a positive int.

    Tie policy (Phase 8, plan section 9.1 determinism): equal BM25 scores
    are ordered by ``chunk_id ASC``. Chunk IDs are content-derived
    (sha256), so the secondary key is stable across runs, processes, and
    machines - equal-scoring candidates never depend on SQLite's internal
    row order, and every downstream consumer (RRF fusion, metrics, eval
    reports) sees one deterministic served order.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise QueryValidationError(f"limit must be a positive int; got {limit!r}")
    match = build_match_query(query, max_query_chars=max_query_chars)
    if not match:
        return []
    try:
        rows = conn.execute(
            "SELECT chunk_id, doc_id, bm25(chunk_fts) AS lexical_bm25 "
            "FROM chunk_fts WHERE chunk_fts MATCH ? "
            "ORDER BY lexical_bm25 ASC, chunk_id ASC LIMIT ?",
            (match, limit),
        ).fetchall()
    except sqlite3.Error as exc:
        raise LexicalIndexError(
            f"FTS5 query failed ({exc.__class__.__name__})"
        ) from exc
    return [
        LexicalHit(
            chunk_id=row["chunk_id"],
            doc_id=row["doc_id"],
            rank=index + 1,
            bm25=float(row["lexical_bm25"]),
        )
        for index, row in enumerate(rows)
    ]
