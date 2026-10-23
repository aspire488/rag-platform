"""Reciprocal rank fusion (plan v2.4 Phase 5: dense + FTS5 + RRF).

RRF(d) = sum over the legs r that retrieved d of 1 / (k + rank_r(d)), with
the standard constant k = 60 (Cormack et al. 2009; documented, frozen, and
asserted with hand-computed expectations in the tests - never tuned
silently).

Fusion combines *ranked lists only*: raw cosine similarities and raw FTS5
BM25 values are carried through for provenance but are never added,
averaged, or compared across legs (the two score spaces are
incommensurate; plan section 5 row for Phase 5).

Determinism (tested): scores are pure arithmetic over integer ranks, so
identical inputs always produce identical floats. Ordering is total:
fused score descending, then lexical rank ascending (dense-only candidates
- no lexical rank - sort after any lexical rank), then chunk_id
ascending. Duplicate chunk ids within one leg are a caller contract
violation; defensively the first occurrence wins, deterministically.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from rag_platform.retrieval.types import DenseHit, FusedChunk, LexicalHit

#: RRF smoothing constant (the documented standard value; I7 - fixed).
RRF_K = 60

#: Sort placeholder: dense-only candidates (no lexical rank) order after
#: every lexical rank at equal fused score.
_NO_LEXICAL_RANK = 2**31 - 1


def reciprocal_rank_fusion(
    lexical: Sequence[LexicalHit] = (),
    dense: Sequence[DenseHit] = (),
    *,
    k: int = RRF_K,
) -> list[FusedChunk]:
    """Fuse the ranked legs into one deterministically ordered candidate list.

    Empty legs are legal: two empty legs -> ``[]``; one empty leg -> the
    other leg's ranking with RRF scores over its ranks alone.
    """
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError(f"k must be a positive int; got {k!r}")
    fused: dict[str, FusedChunk] = {}
    # Lexical first so its rank wins the documented duplicate-tie rule.
    for hit in lexical:
        if hit.chunk_id in fused:
            continue
        fused[hit.chunk_id] = FusedChunk(
            chunk_id=hit.chunk_id,
            doc_id=hit.doc_id,
            fused_score=1.0 / (k + hit.rank),
            lexical_rank=hit.rank,
            lexical_bm25=hit.bm25,
            dense_rank=None,
            dense_cosine_similarity=None,
        )
    for hit in dense:
        existing = fused.get(hit.chunk_id)
        if existing is None:
            fused[hit.chunk_id] = FusedChunk(
                chunk_id=hit.chunk_id,
                doc_id=hit.doc_id,
                fused_score=1.0 / (k + hit.rank),
                lexical_rank=None,
                lexical_bm25=None,
                dense_rank=hit.rank,
                dense_cosine_similarity=hit.cosine_similarity,
            )
        elif existing.dense_rank is None:  # first dense occurrence wins
            fused[hit.chunk_id] = replace(
                existing,
                fused_score=existing.fused_score + 1.0 / (k + hit.rank),
                dense_rank=hit.rank,
                dense_cosine_similarity=hit.cosine_similarity,
            )
    return sorted(
        fused.values(),
        key=lambda candidate: (
            -candidate.fused_score,
            candidate.lexical_rank
            if candidate.lexical_rank is not None
            else _NO_LEXICAL_RANK,
            candidate.chunk_id,
        ),
    )
