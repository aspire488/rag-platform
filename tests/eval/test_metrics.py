"""Hand-calculated metric tests (plan v2.4 section 9.4; Phase 8).

Every expectation below is worked out by hand from the frozen definitions
(binary relevance, nearest-rank percentiles, macro-averaging over
answerable questions) - never by calling the implementation twice.

Offline: pure functions, no I/O, no corpus, no network.
"""

from __future__ import annotations

import math

import pytest

from rag_platform.eval.metrics import (
    EvalMetrics,
    QuestionRecord,
    compute_metrics,
    nearest_rank_percentile,
    ndcg_at_10,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    token_f1,
)


def _record(**overrides: object) -> QuestionRecord:
    base: dict[str, object] = {
        "question_id": "q",
        "question_type": "single-hop",
        "expected_outcome": "answer",
        "gold_chunk_ids": (),
        "served_chunk_ids": (),
        "retrieval_outcome": "ok",
        "generation_outcome": "unavailable",
        "abstention_reason": None,
    }
    base.update(overrides)
    return QuestionRecord(**base)  # type: ignore[arg-type]


# --- ranking primitives ------------------------------------------------------


def test_recall_at_k_is_macro_gold_overlap() -> None:
    # top-1 = [x] (not gold) -> 0/2; top-2 = [x, a] -> 1/2
    assert recall_at_k(("a", "b"), ("x", "a", "y", "b"), 1) == 0.0
    assert recall_at_k(("a", "b"), ("x", "a", "y", "b"), 2) == pytest.approx(0.5)
    assert recall_at_k(("a", "b"), ("x", "a", "y", "b"), 10) == pytest.approx(1.0)
    assert recall_at_k(("a",), ("x", "y"), 10) == 0.0
    with pytest.raises(ValueError):
        recall_at_k((), ("a",), 10)


def test_precision_at_k_uses_k_as_denominator() -> None:
    # top-3 = [a, x, b]; hits = 2 -> 2/3 (missing slots are non-relevant)
    assert precision_at_k(("a", "b"), ("a", "x", "b", "y"), 3) == pytest.approx(
        2.0 / 3.0
    )
    # serving fewer than k items still divides by k
    assert precision_at_k(("a",), ("x",), 5) == 0.0
    assert precision_at_k(("a",), ("a",), 5) == pytest.approx(0.2)
    with pytest.raises(ValueError):
        precision_at_k(("a",), ("a",), 0)


def test_reciprocal_rank_first_gold_only() -> None:
    # first gold at rank 2 -> 1/2
    assert reciprocal_rank(("b",), ("x", "b", "y")) == pytest.approx(0.5)
    # gold set order is irrelevant: first served position wins
    assert reciprocal_rank(("a", "b"), ("b", "x", "a")) == 1.0
    assert reciprocal_rank(("a",), ("x", "y")) == 0.0


def test_ndcg_at_10_hand_computed() -> None:
    # ranked [x, g1, g2]: DCG = 1/log2(3) + 1/log2(4); IDCG = 1/log2(2) + 1/log2(3)
    dcg = 1.0 / math.log2(3) + 1.0 / math.log2(4)
    idcg = 1.0 / math.log2(2) + 1.0 / math.log2(3)
    assert ndcg_at_10(("g1", "g2"), ("x", "g1", "g2")) == pytest.approx(dcg / idcg)
    # ...which equals 0.6934264..., asserted against the literal value too
    assert ndcg_at_10(("g1", "g2"), ("x", "g1", "g2")) == pytest.approx(
        0.6934264, rel=1e-6
    )
    # perfect ranking -> 1.0
    assert ndcg_at_10(("g1", "g2"), ("g1", "g2")) == 1.0
    # gold ranked after position 10 contributes nothing
    assert ndcg_at_10(("g1",), tuple(["x"] * 10 + ["g1"])) == 0.0
    # IDCG is bounded at 10 ideals: >10 gold chunks, all served -> 1.0
    twelve = tuple(f"g{i}" for i in range(12))
    assert ndcg_at_10(twelve, twelve) == 1.0
    with pytest.raises(ValueError):
        ndcg_at_10((), ("a",))


def test_nearest_rank_percentile() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    # index = ceil(0.5 * 10) - 1 = 4 -> sorted[4] = 5
    assert nearest_rank_percentile(values, 0.50) == 5.0
    # index = ceil(0.95 * 10) - 1 = 9 -> sorted[9] = 10
    assert nearest_rank_percentile(values, 0.95) == 10.0
    # index = ceil(0.5 * 4) - 1 = 1 -> sorted[1] = 2
    assert nearest_rank_percentile([4.0, 1.0, 3.0, 2.0], 0.50) == 2.0
    assert nearest_rank_percentile([7.5], 0.95) == 7.5
    # unmeasured, never zero
    assert nearest_rank_percentile([], 0.50) is None
    with pytest.raises(ValueError):
        nearest_rank_percentile([1.0], 0.0)


def test_token_f1_overlap() -> None:
    assert token_f1("the cat sat", "the cat sat") == 1.0
    # 2 of 3 tokens overlap on both sides -> F1 = 2/3
    assert token_f1("the cat sat", "the cat ran") == pytest.approx(2.0 / 3.0)
    assert token_f1("completely different words", "no shared tokens here") == 0.0
    # only both-empty counts as equal; one-sided empty is zero
    assert token_f1("", "") == 1.0
    assert token_f1("", "something") == 0.0


# --- aggregation -------------------------------------------------------------


def _mixed_records() -> list[QuestionRecord]:
    return [
        # A: gold at rank 2 -> recall 1, precision@5 1/5, rr 1/2
        _record(
            question_id="A",
            gold_chunk_ids=("g1",),
            served_chunk_ids=("x", "g1"),
        ),
        # B: one of two gold served at rank 1
        _record(
            question_id="B",
            question_type="multi-hop",
            gold_chunk_ids=("g1", "g2"),
            served_chunk_ids=("g1",),
        ),
        # C: retrieval error - stays in the denominator as 0.0
        _record(
            question_id="C",
            gold_chunk_ids=("g1",),
            served_chunk_ids=(),
            retrieval_error=True,
        ),
    ]


def test_compute_metrics_macro_averages_hand_checked() -> None:
    metrics = compute_metrics(_mixed_records())
    # recall@5 = (1.0 + 0.5 + 0.0) / 3
    assert metrics.recall_at_5.value == pytest.approx(0.5)
    assert metrics.recall_at_5.status == "measured"
    # precision@5 = (1/5 + 1/5 + 0) / 3
    assert metrics.precision_at_5.value == pytest.approx((0.2 + 0.2 + 0.0) / 3)
    # mrr = (1/2 + 1 + 0) / 3
    assert metrics.mrr.value == pytest.approx(0.5)
    # ndcg@10 = (0.6309297535714574 + 1/1.6309297535714574 + 0) / 3
    ndcg_a = 1.0 / math.log2(3)
    ndcg_b = 1.0 / (1.0 + 1.0 / math.log2(3))
    assert metrics.ndcg_at_10.value == pytest.approx((ndcg_a + ndcg_b) / 3)
    # counting rule: three answerable questions, none dropped
    assert metrics.answerable_count == 3
    assert metrics.retrieval_error_count == 1


def test_not_measured_when_no_answerable_questions() -> None:
    records = [_record(question_id="u", expected_outcome="abstain")]
    metrics = compute_metrics(records)
    # Unmeasured metrics are never reported as zero or one hundred percent.
    for row in (
        metrics.recall_at_5,
        metrics.recall_at_10,
        metrics.precision_at_5,
        metrics.precision_at_10,
        metrics.mrr,
        metrics.ndcg_at_10,
    ):
        assert row.status == "not_measured"
        assert row.value is None


def test_abstention_correctness_evidence_level_only() -> None:
    records = [
        _record(question_id="a1", expected_outcome="abstain", abstention_reason=None),
        _record(
            question_id="a2",
            expected_outcome="abstain",
            abstention_reason="no_candidates",
        ),
        # generation_unavailable is NOT a correct abstention
        _record(
            question_id="a3",
            expected_outcome="abstain",
            abstention_reason="generation_unavailable",
        ),
        _record(
            question_id="a4",
            expected_outcome="abstain",
            abstention_reason="insufficient_evidence",
        ),
        # flag-conflict has no M1 conflict detector - excluded entirely
        _record(
            question_id="f1",
            question_type="conflict-aware",
            expected_outcome="flag-conflict",
            abstention_reason=None,
        ),
    ]
    metrics = compute_metrics(records)
    # 2 of 4 abstain-labeled records abstained at the evidence level
    assert metrics.abstention_correctness.value == pytest.approx(0.5)
    assert metrics.abstention_correctness.status == "measured"
    assert "generation_unavailable" in metrics.abstention_correctness.notes


def test_abstention_correctness_not_measured_without_abstain_labels() -> None:
    metrics = compute_metrics([_record(question_id="only")])
    assert metrics.abstention_correctness.status == "not_measured"
    assert metrics.abstention_correctness.value is None


def test_false_refusal_rate_counts_only_answer_labels() -> None:
    records = [
        _record(question_id="r1", abstention_reason="no_candidates"),
        _record(question_id="ok1"),
        _record(question_id="ok2", expected_outcome="abstain"),
        _record(question_id="conf", expected_outcome="flag-conflict"),
    ]
    metrics = compute_metrics(records)
    # 1 of 2 answer-labeled questions refused at the evidence level
    assert metrics.false_refusal_rate.value == pytest.approx(0.5)
    assert metrics.false_refusal_rate.status == "measured"


def test_retrieval_only_rate_over_all_records() -> None:
    records = [
        _record(question_id="u1", generation_outcome="unavailable"),
        _record(question_id="u2", generation_outcome="unavailable"),
        _record(question_id="u3", generation_outcome="abstained"),
    ]
    metrics = compute_metrics(records)
    assert metrics.retrieval_only_rate.value == pytest.approx(2.0 / 3.0)
    assert metrics.generation_unavailable_count == 2


def test_citation_validity_only_when_citations_exist() -> None:
    no_citations = compute_metrics(
        [_record(question_id="c0", citations_valid=None, citations_total=None)]
    )
    assert no_citations.citation_validity.status == "not_measured"
    assert no_citations.citation_validity.value is None

    mixed = compute_metrics(
        [
            _record(question_id="c1", citations_valid=3, citations_total=4),
            _record(question_id="c2", citations_valid=0, citations_total=2),
            _record(question_id="c3"),  # no citations: excluded, not a zero
        ]
    )
    # (3 + 0) / (4 + 2)
    assert mixed.citation_validity.value == pytest.approx(0.5)
    assert mixed.citation_validity.status == "measured"


def test_token_f1_not_measured_without_generated_answers() -> None:
    disabled = compute_metrics(
        [
            _record(question_id="t1", gold_answer="42 bushels", predicted_answer=None),
        ]
    )
    assert disabled.token_f1.status == "not_measured"
    assert disabled.token_f1.value is None

    answered = compute_metrics(
        [
            _record(
                question_id="t2",
                gold_answer="42 bushels",
                predicted_answer="the yield was 42 bushels",
            ),
            _record(
                question_id="t3",
                gold_answer="17 crates",
                predicted_answer="17 crates",
            ),
        ]
    )
    # hand check: first pair = "the yield was 42 bushels" vs "42 bushels"
    # tokens: p = 5, g = 2, overlap = 2 -> P = 0.4, R = 1 -> F1 = 0.571428...
    assert answered.token_f1.value == pytest.approx((0.5714285714285715 + 1.0) / 2)
    assert answered.token_f1.status == "measured"


def test_latency_percentiles_nearest_rank() -> None:
    metrics = compute_metrics(
        [
            _record(question_id="l1", latency_ms=10.0),
            _record(question_id="l2", latency_ms=20.0),
            _record(question_id="l3", latency_ms=30.0),
            _record(question_id="l4", latency_ms=40.0),
        ]
    )
    # p50 -> sorted[ceil(0.5*4)-1] = sorted[1] = 20
    assert metrics.latency_p50_ms.value == pytest.approx(20.0)
    # p95 -> sorted[ceil(0.95*4)-1] = sorted[3] = 40
    assert metrics.latency_p95_ms.value == pytest.approx(40.0)


def test_latency_not_measured_when_no_queries() -> None:
    metrics = compute_metrics([])
    assert metrics.latency_p50_ms.status == "not_measured"
    assert metrics.latency_p50_ms.value is None
    assert metrics.retrieval_only_rate.status == "not_measured"
    assert metrics.retrieval_only_rate.value is None


def test_rows_covers_every_metric_row_exactly_once() -> None:
    metrics = compute_metrics(_mixed_records())
    rows = metrics.rows()
    names = [row.name for row in rows]
    assert len(names) == len(set(names)) == 13
    assert set(names) == {
        "recall_at_5",
        "recall_at_10",
        "precision_at_5",
        "precision_at_10",
        "mrr",
        "ndcg_at_10",
        "token_f1",
        "abstention_correctness",
        "false_refusal_rate",
        "retrieval_only_rate",
        "citation_validity",
        "latency_p50_ms",
        "latency_p95_ms",
    }
    assert all(isinstance(row.status, str) for row in rows)
    assert isinstance(metrics, EvalMetrics)
