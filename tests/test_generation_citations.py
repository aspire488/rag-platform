"""Phase 6 citation-validation tests: constructive mapping, fabricated
reference rejection (unknown ids/indexes), span containment, malformed
markers, de-duplication, and the all-or-nothing verdict.

Offline and deterministic: pure functions over constructed
``EvidenceSpan`` values - no model, no network, no I/O.
"""

from __future__ import annotations

import pytest

from rag_platform.generation.citations import validate_citations
from rag_platform.generation.errors import GenerationRequestError
from rag_platform.generation.types import (
    EvidenceSpan,
    InvalidCitationReason,
)

_CHUNK_A = "a" * 64
_CHUNK_B = "b" * 64
_EVIDENCE = (
    EvidenceSpan(chunk_id=_CHUNK_A, doc_id="doc-a", char_start=10, char_end=90),
    EvidenceSpan(chunk_id=_CHUNK_B, doc_id="doc-b", char_start=0, char_end=40),
)


def test_valid_ordinal_citation_resolves_from_served_evidence_only() -> None:
    result = validate_citations("The walrus swims. [C1]", _EVIDENCE)
    assert result.valid and result.reason is None
    (citation,) = result.citations
    assert citation.index == 1
    assert citation.chunk_id == _CHUNK_A
    assert citation.doc_id == "doc-a"  # from OUR evidence, not the text
    assert (citation.char_start, citation.char_end) == (10, 90)


def test_valid_direct_chunk_id_citation_resolves() -> None:
    result = validate_citations(f"See [C{_CHUNK_B}].", _EVIDENCE)
    assert result.valid
    (citation,) = result.citations
    assert citation.chunk_id == _CHUNK_B
    assert citation.doc_id == "doc-b"
    assert (citation.char_start, citation.char_end) == (0, 40)


def test_span_form_citation_must_lie_inside_the_served_span() -> None:
    valid = validate_citations("[C1@20-50]", _EVIDENCE)
    assert valid.valid
    (citation,) = valid.citations
    assert (citation.char_start, citation.char_end) == (20, 50)
    # Boundaries are inclusive of the served range edges.
    at_edges = validate_citations("[C1@10-90]", _EVIDENCE)
    assert at_edges.valid
    # Anything outside the served span is fabricated provenance.
    outside = validate_citations("[C1@10-91]", _EVIDENCE)
    assert not outside.valid
    assert outside.reason is InvalidCitationReason.SPAN_OUT_OF_RANGE
    assert outside.citations == ()  # never partially grounded
    before = validate_citations("[C1@9-20]", _EVIDENCE)
    assert before.reason is InvalidCitationReason.SPAN_OUT_OF_RANGE


@pytest.mark.parametrize("text", ["[C1@50-20]", "[C1@20-20]", "[C1@0-0]"])
def test_inverted_or_empty_spans_are_rejected(text: str) -> None:
    result = validate_citations(text, _EVIDENCE)
    assert not result.valid
    assert result.reason is InvalidCitationReason.SPAN_OUT_OF_RANGE
    assert result.citations == ()


@pytest.mark.parametrize("text", ["[C99]", "[C0]", "[C10]"])
def test_unknown_ordinals_are_rejected(text: str) -> None:
    result = validate_citations(text, _EVIDENCE)
    assert not result.valid
    assert result.reason is InvalidCitationReason.UNKNOWN_REFERENCE
    assert result.citations == ()


def test_invented_chunk_id_is_rejected() -> None:
    invented = "f" * 64
    result = validate_citations(f"The model made this up: [C{invented}].", _EVIDENCE)
    assert not result.valid
    assert result.reason is InvalidCitationReason.UNKNOWN_REFERENCE
    assert result.citations == ()


@pytest.mark.parametrize("text", ["[C]", "[C1@abc]", "[C1@1]", "[C@]"])
def test_malformed_markers_are_rejected(text: str) -> None:
    result = validate_citations(text, _EVIDENCE)
    assert not result.valid
    assert result.reason is InvalidCitationReason.MALFORMED
    assert result.citations == ()


@pytest.mark.parametrize("text", ["no citations at all", "just prose [1] (2)", ""])
def test_answers_without_citations_are_ungrounded(text: str) -> None:
    result = validate_citations(text, _EVIDENCE)
    assert not result.valid
    assert result.reason is InvalidCitationReason.NO_CITATIONS
    assert result.citations == ()


def test_duplicates_deduplicate_but_mixed_valid_plus_fabricated_rejects_all() -> None:
    repeated = validate_citations("[C1] and again [C1].", _EVIDENCE)
    assert repeated.valid
    assert len(repeated.citations) == 1
    # The same evidence via two reference forms still deduplicates.
    by_id = validate_citations(f"[C1] [C{_CHUNK_A}]", _EVIDENCE)
    assert by_id.valid
    assert len(by_id.citations) == 1
    # One valid + one fabricated: the WHOLE answer is rejected.
    mixed = validate_citations("[C1] but also [C99]", _EVIDENCE)
    assert not mixed.valid
    assert mixed.reason is InvalidCitationReason.UNKNOWN_REFERENCE
    assert mixed.citations == ()


def test_model_supplied_provenance_cannot_override_served_evidence() -> None:
    # Text claims a different document/range; resolution still uses ours.
    result = validate_citations("As shown in doc TOTALLY-FAKE [C2@0-40].", _EVIDENCE)
    assert result.valid
    (citation,) = result.citations
    assert citation.doc_id == "doc-b"  # served doc, never the text's claim
    assert citation.char_start == 0 and citation.char_end == 40


def test_invalid_inputs_raise_the_typed_contract_error() -> None:
    with pytest.raises(GenerationRequestError):
        validate_citations(None, _EVIDENCE)  # type: ignore[arg-type]
    with pytest.raises(GenerationRequestError):
        validate_citations("[C1]", "not-sequence")  # type: ignore[arg-type]
    with pytest.raises(GenerationRequestError):
        validate_citations("[C1]", ["not-a-span"])  # type: ignore[list-item]
