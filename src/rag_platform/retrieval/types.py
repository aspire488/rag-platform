"""Immutable retrieval contracts (plan v2.4 Phase 5).

Score spaces are explicit and never mixed:

- ``dense_cosine_similarity`` - the dense leg's cosine similarity (bounded
  to [-1, 1] for the Cosine collection; the ONLY score space the
  configurable ``retrieval_score_floor`` may compare against).
- ``lexical_bm25`` - raw FTS5 ``bm25()`` output: a *ranking signal* where
  lower (more negative) values rank better. It is unbounded and not a
  calibrated relevance probability; it is NEVER compared to the dense floor.

Provenance is citation-grade: every retrieved chunk carries its stable ids,
its project-relative POSIX source path (never absolute), and the
document-relative Unicode code-point range into the extracted text (owner
decision recorded in the plan change note v2.4, section 10, decision 4).
Injection metadata is advisory (I11): carried, never gating.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class LexicalHit:
    """One FTS5 result: stable ids, 1-based bm25 rank, raw bm25 value."""

    chunk_id: str
    doc_id: str
    rank: int
    bm25: float


@dataclass(frozen=True)
class DenseHit:
    """One dense-search result: stable ids, 1-based rank, cosine value."""

    chunk_id: str
    doc_id: str
    rank: int
    cosine_similarity: float


@dataclass(frozen=True)
class FusedChunk:
    """One RRF-fused candidate with its per-leg ranks and raw scores.

    ``None`` means "that leg did not retrieve this chunk" - the two score
    spaces stay separate and are never averaged or compared.
    """

    chunk_id: str
    doc_id: str
    fused_score: float
    lexical_rank: int | None
    lexical_bm25: float | None
    dense_rank: int | None
    dense_cosine_similarity: float | None


@dataclass(frozen=True)
class RetrievedChunk:
    """A hydrated, citation-ready result (post-fusion, post-floor).

    ``content`` is the verbatim persisted chunk (Phase 4 bounds it to 2000
    chars via schema CHECK); ``char_start``/``char_end`` are document-
    relative code-point offsets into the extracted text such that
    ``content`` equals that extracted-text span for txt/md (and the lossy
    PDF reconstruction, per the recorded owner decision).

    ``rank`` is the 1-based fused position at retrieval time; gaps are
    possible when a stale index id could not be hydrated (see the
    retriever report's ``unhydrated_skipped``).
    """

    chunk_id: str
    doc_id: str
    ordinal: int
    source_path: str  # relative POSIX, never absolute (I16)
    content: str
    char_start: int
    char_end: int
    rank: int
    fused_score: float
    dense_cosine_similarity: float | None  # None: no dense evidence retrieved
    lexical_bm25: float | None  # None: the lexical leg did not retrieve it
    injection_quarantined: bool  # advisory (I11): never gates results
    injection_finding_count: int


@dataclass(frozen=True)
class RetrievalReport:
    """Honest per-query outcome metadata (Z8: never claim unrun work).

    ``outcome`` values (mapping fodder for the Phase 6/8 abstention
    reasons, not a claim that abstention is implemented here):

    - ``ok`` - at least one result survived fusion, the floor, hydration,
      and the post-verification hook.
    - ``no_candidates`` - neither leg produced a candidate (empty corpus,
      tokenless/empty query).
    - ``insufficient_evidence`` - candidates existed but every one with
      dense evidence fell below the applied score floor and none remained
      (plan section 4.2 semantics).

    ``mode``/``lexical_only_reason`` record WHY a query ran lexical-only
    (``no_embedder`` / ``no_vector_store`` / ``score_floor_unset`` /
    ``non_cosine_collection:<Metric>`` / ``embedder_error:<Class>``) - dense evidence is never implied when
    none was gathered. ``score_floor`` is the dense floor actually applied
    to this query's dense candidates (None: unset, dense non-operational,
    or no dense candidates).
    """

    mode: Literal["hybrid", "lexical_only"]
    lexical_only_reason: str | None
    outcome: Literal["ok", "no_candidates", "insufficient_evidence"]
    top_k: int
    lexical_hits: int
    dense_hits: int
    fused_candidates: int
    below_floor_excluded: int
    unhydrated_skipped: int
    post_verify_dropped: int
    score_floor: float | None
    results: tuple[RetrievedChunk, ...]
