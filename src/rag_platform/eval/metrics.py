"""Deterministic stdlib-only retrieval metrics (plan v2.4 Phase 8; 9.1/9.4).

Every metric here is a pure function over plain data: ranked chunk IDs and
gold labels. No NumPy, no sklearn, no network, no model. Hand-calculated
test cases pin each formula (``tests/eval/test_metrics.py``).

Definitions (frozen; denominators are explicit and errors stay visible):

- **Recall@k** - ``|gold ∩ top-k| / |gold|`` over answerable questions
  with a non-empty gold set. A question whose retrieval errored counts
  recall 0 for that question (errors never leave the denominator).
- **Precision@k** - ``|gold ∩ top-k| / k`` (missing slots count as
  non-relevant - the plan's served-set precision).
- **MRR** - reciprocal rank of the first gold chunk in the full ranked
  list; 1/inf = 0 when no gold chunk is served.
- **nDCG@10** - binary relevance; DCG = sum(rel_i / log2(i+1)) over the
  first 10 served; IDCG from the ideal ranking (min(|gold|, 10) ones).
- **Token-F1** - SQuAD-style token overlap F1, computed ONLY when both an
  actual generated answer and a gold answer string exist. With generation
  disabled (the shipped default) this metric is ``not_measured`` -
  never zero, never one hundred.
- **Abstention correctness** - unanswerable questions where the response
  carried an *evidence-level* abstention (no_candidates /
  insufficient_evidence / no_usable_evidence). ``generation_unavailable``
  is NOT a correct abstention (it says nothing about evidence) and is
  reported separately as ``generation_unavailable_count``.
- **False-refusal rate** - answerable questions that received an
  evidence-level abstention anyway.
- **Retrieval-only rate** - responses whose generation outcome is
  ``unavailable`` (the default configuration is generation-disabled).
- **Citation validity** - citations present whose (chunk_id, char range)
  resolves inside the served passages. Only measurable when citations
  exist (generation-enabled runs).
- **Latency p50/p95** - nearest-rank percentiles over per-query boundary
  milliseconds: sorted values, index = ceil(p*n) - 1. Measured values,
  single machine, documented as volatile.

Unmeasured metrics carry ``status="not_measured"`` and ``value=None``.
Z8: nothing is inferred, nothing is defaulted to 0 or 100.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

#: Abstention reasons that count as an evidence-level refusal (I12 paths).
EVIDENCE_ABSTENTION_REASONS = frozenset(
    {"no_candidates", "insufficient_evidence", "no_usable_evidence"}
)

MetricStatus = Literal["measured", "not_measured"]

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class MetricValue:
    """One metric row: value + status; ``value`` is None only when
    ``status`` is ``not_measured`` (never zero-filled by accident)."""

    name: str
    value: float | None
    status: MetricStatus
    notes: str = ""

    def as_dict(self) -> dict[str, str | float | None]:
        return {
            "name": self.name,
            "value": self.value,
            "status": self.status,
            "notes": self.notes,
        }


def _measured(name: str, value: float, notes: str = "") -> MetricValue:
    return MetricValue(name=name, value=value, status="measured", notes=notes)


def _not_measured(name: str, notes: str) -> MetricValue:
    return MetricValue(name=name, value=None, status="not_measured", notes=notes)


# --- ranking metrics ---------------------------------------------------------


def recall_at_k(gold: Sequence[str], ranked: Sequence[str], k: int) -> float:
    """``|gold ∩ top-k| / |gold|``; a non-empty gold set is required."""
    if not gold:
        raise ValueError("recall_at_k requires a non-empty gold set")
    top_k = set(ranked[:k])
    return len(top_k.intersection(gold)) / len(gold)


def precision_at_k(gold: Sequence[str], ranked: Sequence[str], k: int) -> float:
    """``|gold ∩ top-k| / k`` - missing slots count as non-relevant."""
    if k < 1:
        raise ValueError("precision_at_k requires k >= 1")
    top_k = ranked[:k]
    hits = sum(1 for chunk in top_k if chunk in set(gold))
    return hits / k


def reciprocal_rank(gold: Sequence[str], ranked: Sequence[str]) -> float:
    """Reciprocal rank of the first gold chunk; 0.0 when none is served."""
    gold_set = set(gold)
    for position, chunk in enumerate(ranked, start=1):
        if chunk in gold_set:
            return 1.0 / position
    return 0.0


def ndcg_at_10(gold: Sequence[str], ranked: Sequence[str]) -> float:
    """Binary-relevance nDCG@10 (plan section 9.4 retrieval row)."""
    if not gold:
        raise ValueError("ndcg_at_10 requires a non-empty gold set")
    dcg = 0.0
    for position, chunk in enumerate(ranked[:10], start=1):
        if chunk in set(gold):
            dcg += 1.0 / math.log2(position + 1)
    ideal_count = min(len(gold), 10)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_count + 1))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def nearest_rank_percentile(values: Sequence[float], p: float) -> float | None:
    """Nearest-rank percentile: sorted[ceil(p*n) - 1]; None when empty."""
    if not values:
        return None
    if not 0.0 < p <= 1.0:
        raise ValueError("percentile p must be in (0, 1]")
    ordered = sorted(values)
    index = max(math.ceil(p * len(ordered)) - 1, 0)
    return ordered[index]


def token_f1(predicted: str, gold: str) -> float:
    """SQuAD-style token-overlap F1 (answer lexical overlap only)."""
    predicted_tokens = _TOKEN_RE.findall(predicted.lower())
    gold_tokens = _TOKEN_RE.findall(gold.lower())
    if not predicted_tokens or not gold_tokens:
        return 1.0 if predicted_tokens == gold_tokens else 0.0
    common = Counter(predicted_tokens) & Counter(gold_tokens)
    overlap = sum(common.values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(predicted_tokens)
    recall = overlap / len(gold_tokens)
    return 2.0 * precision * recall / (precision + recall)


# --- per-question record -----------------------------------------------------


@dataclass(frozen=True)
class QuestionRecord:
    """One evaluated question: gold + served + outcome, no raw text.

    The harness never places question text or answer text in records that
    reach reports; only IDs, chunk IDs, enums and numbers. ``gold_answer``
    and ``predicted_answer`` exist solely for token-F1 computation in
    memory and are excluded from report payloads.
    """

    question_id: str
    question_type: str  # single-hop | multi-hop | unanswerable | conflict-aware | injection-adjacent
    expected_outcome: str  # answer | abstain | flag-conflict
    gold_chunk_ids: tuple[str, ...]
    served_chunk_ids: tuple[str, ...]  # ranked, as served
    retrieval_outcome: str  # ok | no_candidates | insufficient_evidence
    generation_outcome: str  # answered | abstained | unavailable
    abstention_reason: str | None  # enum value or None
    retrieval_error: bool = False  # errors stay in denominators (recall 0)
    gold_answer: str | None = None
    predicted_answer: str | None = None
    citations_valid: int | None = None  # None = no citations to validate
    citations_total: int | None = None
    latency_ms: float = 0.0


@dataclass(frozen=True)
class EvalMetrics:
    """Aggregate metric rows for one run (deterministic section)."""

    recall_at_5: MetricValue
    recall_at_10: MetricValue
    precision_at_5: MetricValue
    precision_at_10: MetricValue
    mrr: MetricValue
    ndcg_at_10: MetricValue
    token_f1: MetricValue
    abstention_correctness: MetricValue
    false_refusal_rate: MetricValue
    retrieval_only_rate: MetricValue
    citation_validity: MetricValue
    latency_p50_ms: MetricValue
    latency_p95_ms: MetricValue
    answerable_count: int
    unanswerable_count: int
    generation_unavailable_count: int
    retrieval_error_count: int

    def rows(self) -> list[MetricValue]:
        return [
            self.recall_at_5,
            self.recall_at_10,
            self.precision_at_5,
            self.precision_at_10,
            self.mrr,
            self.ndcg_at_10,
            self.token_f1,
            self.abstention_correctness,
            self.false_refusal_rate,
            self.retrieval_only_rate,
            self.citation_validity,
            self.latency_p50_ms,
            self.latency_p95_ms,
        ]


def _is_evidence_abstention(reason: str | None) -> bool:
    return reason in EVIDENCE_ABSTENTION_REASONS


def compute_metrics(records: Sequence[QuestionRecord]) -> EvalMetrics:
    """Aggregate per-question records into deterministic metric rows.

    Aggregation policy (frozen):

    - Ranking metrics macro-average over *answerable* questions (those
      with a non-empty gold set). A retrieval error contributes 0.0 for
      that question - errors never leave the denominator.
    - Abstention metrics use the expected-outcome labels: abstain-labeled
      questions measure correctness; answer-labeled questions measure
      false refusals. ``flag-conflict`` questions have no M1 conflict
      detector (ledger is M3) and are excluded from abstention metrics,
      with the reason recorded.
    - Token-F1 and citation validity are computed only when the needed
      actual outputs exist; otherwise ``not_measured``.
    """
    answerable = [r for r in records if r.gold_chunk_ids]
    unanswerable = [r for r in records if not r.gold_chunk_ids]

    def _macro(fn) -> float | None:  # noqa: ANN001 - small local helper
        if not answerable:
            return None
        return sum(fn(r) for r in answerable) / len(answerable)

    def _recall(r: QuestionRecord, k: int) -> float:
        if r.retrieval_error:
            return 0.0
        return recall_at_k(r.gold_chunk_ids, r.served_chunk_ids, k)

    def _precision(r: QuestionRecord, k: int) -> float:
        if r.retrieval_error:
            return 0.0
        return precision_at_k(r.gold_chunk_ids, r.served_chunk_ids, k)

    def _mrr(r: QuestionRecord) -> float:
        if r.retrieval_error:
            return 0.0
        return reciprocal_rank(r.gold_chunk_ids, r.served_chunk_ids)

    def _ndcg(r: QuestionRecord) -> float:
        if r.retrieval_error:
            return 0.0
        return ndcg_at_10(r.gold_chunk_ids, r.served_chunk_ids)

    r5 = _macro(lambda r: _recall(r, 5))
    r10 = _macro(lambda r: _recall(r, 10))
    p5 = _macro(lambda r: _precision(r, 5))
    p10 = _macro(lambda r: _precision(r, 10))
    mrr_value = _macro(_mrr)
    ndcg = _macro(_ndcg)

    no_answerable = not answerable
    recall_5 = (
        _not_measured("recall_at_5", "no answerable questions in this split/run")
        if no_answerable
        else _measured("recall_at_5", r5, "macro-average over answerable questions")
    )
    recall_10 = (
        _not_measured("recall_at_10", "no answerable questions in this split/run")
        if no_answerable
        else _measured("recall_at_10", r10, "macro-average over answerable questions")
    )
    precision_5 = (
        _not_measured("precision_at_5", "no answerable questions in this split/run")
        if no_answerable
        else _measured(
            "precision_at_5", p5, "served-set precision; missing slots non-relevant"
        )
    )
    precision_10 = (
        _not_measured("precision_at_10", "no answerable questions in this split/run")
        if no_answerable
        else _measured(
            "precision_at_10", p10, "served-set precision; missing slots non-relevant"
        )
    )
    mrr_metric = (
        _not_measured("mrr", "no answerable questions in this split/run")
        if no_answerable
        else _measured("mrr", mrr_value, "reciprocal rank of first gold chunk")
    )
    ndcg_metric = (
        _not_measured("ndcg_at_10", "no answerable questions in this split/run")
        if no_answerable
        else _measured("ndcg_at_10", ndcg, "binary relevance, log2 discount")
    )

    # Token-F1: only when BOTH actual answers and gold strings exist.
    f1_pairs = [
        (r.predicted_answer, r.gold_answer)
        for r in records
        if r.predicted_answer is not None and r.gold_answer is not None
    ]
    if f1_pairs:
        token_f1_metric = _measured(
            "token_f1",
            sum(token_f1(p or "", g or "") for p, g in f1_pairs) / len(f1_pairs),
            f"macro-average over {len(f1_pairs)} questions with actual answers",
        )
    else:
        token_f1_metric = _not_measured(
            "token_f1",
            "no generated answers in this configuration "
            "(generation disabled; needs an authorized generation-enabled run)",
        )

    # Abstention correctness: expected abstain; evidence-level only.
    expected_abstain = [r for r in records if r.expected_outcome == "abstain"]
    if expected_abstain:
        correct = sum(
            1 for r in expected_abstain if _is_evidence_abstention(r.abstention_reason)
        )
        abstention_metric = _measured(
            "abstention_correctness",
            correct / len(expected_abstain),
            "evidence-level abstentions only; generation_unavailable excluded "
            f"({sum(1 for r in expected_abstain if r.abstention_reason == 'generation_unavailable')} "
            "responses were generation_unavailable and counted incorrect)",
        )
    else:
        abstention_metric = _not_measured(
            "abstention_correctness", "no abstain-labeled questions in this run"
        )

    # False refusals: expected answer, received an evidence-level abstention.
    expected_answer = [r for r in records if r.expected_outcome == "answer"]
    if expected_answer:
        refusals = sum(
            1 for r in expected_answer if _is_evidence_abstention(r.abstention_reason)
        )
        false_refusal_metric = _measured(
            "false_refusal_rate",
            refusals / len(expected_answer),
            "answerable questions refused at the evidence level",
        )
    else:
        false_refusal_metric = _not_measured(
            "false_refusal_rate", "no answer-labeled questions in this run"
        )

    if records:
        retrieval_only_metric = _measured(
            "retrieval_only_rate",
            sum(1 for r in records if r.generation_outcome == "unavailable")
            / len(records),
            "responses served without generation (default configuration)",
        )
    else:
        retrieval_only_metric = _not_measured(
            "retrieval_only_rate", "no questions evaluated in this run"
        )

    # Citation validity: only when citations exist.
    cited = [
        r for r in records if r.citations_total is not None and r.citations_total > 0
    ]
    if cited:
        valid_total = sum(r.citations_valid or 0 for r in cited)
        all_citations = sum(r.citations_total or 0 for r in cited)
        citation_metric = _measured(
            "citation_validity",
            valid_total / all_citations if all_citations else 0.0,
            "citations resolving inside served passages; coverage is not "
            "correctness (motivates the M3 ledger)",
        )
    else:
        citation_metric = _not_measured(
            "citation_validity",
            "no citations produced in this configuration (generation disabled)",
        )

    latencies = [r.latency_ms for r in records]
    p50 = nearest_rank_percentile(latencies, 0.50)
    p95 = nearest_rank_percentile(latencies, 0.95)
    latency_p50 = (
        _not_measured("latency_p50_ms", "no queries in this run")
        if p50 is None
        else _measured(
            "latency_p50_ms", p50, "nearest-rank; single-machine measured value"
        )
    )
    latency_p95 = (
        _not_measured("latency_p95_ms", "no queries in this run")
        if p95 is None
        else _measured(
            "latency_p95_ms", p95, "nearest-rank; single-machine measured value"
        )
    )

    return EvalMetrics(
        recall_at_5=recall_5,
        recall_at_10=recall_10,
        precision_at_5=precision_5,
        precision_at_10=precision_10,
        mrr=mrr_metric,
        ndcg_at_10=ndcg_metric,
        token_f1=token_f1_metric,
        abstention_correctness=abstention_metric,
        false_refusal_rate=false_refusal_metric,
        retrieval_only_rate=retrieval_only_metric,
        citation_validity=citation_metric,
        latency_p50_ms=latency_p50,
        latency_p95_ms=latency_p95,
        answerable_count=len(answerable),
        unanswerable_count=len(unanswerable),
        generation_unavailable_count=sum(
            1 for r in records if r.generation_outcome == "unavailable"
        ),
        retrieval_error_count=sum(1 for r in records if r.retrieval_error),
    )


def served_by_distribution(records: Sequence[QuestionRecord]) -> Mapping[str, int]:
    """Distribution of generation outcomes (served_by proxy at M1:
    every default run is ``retrieval_only``)."""
    counts: Counter[str] = Counter(r.generation_outcome for r in records)
    return dict(sorted(counts.items()))
