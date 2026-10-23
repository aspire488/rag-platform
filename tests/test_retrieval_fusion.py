"""Phase 5 RRF tests (plan v2.4 section 15, Phase 5 row): hand-computed
scores, duplicate handling, total deterministic ordering, empty/one-sided
legs, and the frozen fusion constant.

Pure functions over synthetic in-memory inputs - no database, no store,
no embedder, no network, no model, no fixtures.
"""

from __future__ import annotations

import pytest

from rag_platform.retrieval.fusion import RRF_K, reciprocal_rank_fusion
from rag_platform.retrieval.types import DenseHit, LexicalHit


def _lex(chunk_id: str, rank: int, bm25: float = -1.0) -> LexicalHit:
    return LexicalHit(chunk_id=chunk_id, doc_id=f"doc-{chunk_id}", rank=rank, bm25=bm25)


def _dense(chunk_id: str, rank: int, sim: float = 0.5) -> DenseHit:
    return DenseHit(
        chunk_id=chunk_id, doc_id=f"doc-{chunk_id}", rank=rank, cosine_similarity=sim
    )


def test_rrf_constant_is_the_documented_standard() -> None:
    assert RRF_K == 60


def test_rrf_hand_computed_two_legs() -> None:
    # A lexical-rank1 only -> 1/61. B in both legs (lex 2, dense 1) ->
    # 1/62 + 1/61. C dense-rank2 only -> 1/62.
    fused = reciprocal_rank_fusion(
        [_lex("A", 1, bm25=-3.2), _lex("B", 2, bm25=-1.1)],
        [_dense("B", 1, sim=0.9), _dense("C", 2, sim=0.5)],
    )
    assert [c.chunk_id for c in fused] == ["B", "A", "C"]
    assert fused[0].fused_score == 1 / 62 + 1 / 61
    assert fused[1].fused_score == 1 / 61
    assert fused[2].fused_score == 1 / 62
    # Per-leg ranks and raw scores stay in their own score spaces.
    assert (fused[0].lexical_rank, fused[0].dense_rank) == (2, 1)
    assert fused[0].lexical_bm25 == -1.1
    assert fused[0].dense_cosine_similarity == 0.9
    assert (fused[1].lexical_rank, fused[1].dense_rank) == (1, None)
    assert fused[1].dense_cosine_similarity is None
    assert (fused[2].lexical_rank, fused[2].dense_rank) == (None, 2)
    assert fused[2].lexical_bm25 is None


def test_rrf_dual_leg_agreement_outranks_single_leg() -> None:
    fused = reciprocal_rank_fusion(
        [_lex("both", 3), _lex("lexonly", 1)],
        [_dense("both", 4)],
    )
    assert fused[0].chunk_id == "both"
    assert fused[0].fused_score == 1 / 63 + 1 / 64
    assert fused[1].chunk_id == "lexonly"


def test_rrf_empty_legs() -> None:
    assert reciprocal_rank_fusion([], []) == []
    one_sided_lex = reciprocal_rank_fusion([_lex("X", 1), _lex("Y", 2)], [])
    assert [c.chunk_id for c in one_sided_lex] == ["X", "Y"]
    assert one_sided_lex[0].fused_score == 1 / 61
    assert one_sided_lex[0].dense_rank is None
    one_sided_dense = reciprocal_rank_fusion([], [_dense("Z", 1)])
    assert one_sided_dense[0].lexical_rank is None
    assert one_sided_dense[0].fused_score == 1 / 61


def test_rrf_duplicate_ids_within_one_leg_first_wins() -> None:
    fused = reciprocal_rank_fusion([_lex("dup", 1), _lex("dup", 2)], [])
    assert len(fused) == 1
    assert fused[0].lexical_rank == 1
    assert fused[0].fused_score == 1 / 61


def test_rrf_tie_break_prefers_lexical_rank_then_dense_only() -> None:
    # A (lexical-only rank 1) and B (dense-only rank 1) tie at 1/61; the
    # lexical rank sorts first by the documented total ordering. A second
    # dense-only candidate at rank 2 sorts after both.
    fused = reciprocal_rank_fusion(
        [_lex("A", 1)],
        [_dense("B", 1), _dense("C", 2)],
    )
    assert [c.chunk_id for c in fused] == ["A", "B", "C"]


def test_rrf_repeated_runs_are_identical() -> None:
    lexical = [_lex("A", 1), _lex("B", 2)]
    dense = [_dense("B", 1), _dense("C", 2), _dense("A", 3)]
    first = reciprocal_rank_fusion(lexical, dense)
    second = reciprocal_rank_fusion(lexical, dense)
    assert first == second


def test_rrf_rejects_invalid_constant() -> None:
    for bad in (0, -1, True, 2.5):  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            reciprocal_rank_fusion([], [], k=bad)  # type: ignore[arg-type]
