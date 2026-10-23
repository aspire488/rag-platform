"""Phase 4 chunker tests (plan v2.4 Phase 4; bounds carried from plan v1
section 6.2: chunk ~1000/150 sentence-aware, hard max 2000/400).

Pure-function tests: no files, no database, no network, no models - just
deterministic strings and the documented invariants (bounded, ordered,
non-empty, offset-faithful, terminating).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rag_platform.config import Settings
from rag_platform.ingestion.chunker import chunk_text

_SENTENCED = " ".join(f"Sentence number {i:02d} ends here." for i in range(40))


def _base(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {"_env_file": None}
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


def _assert_invariants(text: str, chunks: list, size: int) -> None:
    previous_start = -1
    for expected_ordinal, chunk in enumerate(chunks):
        assert chunk.ordinal == expected_ordinal
        assert chunk.content.strip()  # non-empty (whitespace never emitted)
        assert len(chunk.content) <= size  # hard cap
        assert 0 <= chunk.char_start < chunk.char_end <= len(text)
        assert chunk.char_start > previous_start  # ordered, strictly increasing
        previous_start = chunk.char_start
        assert text[chunk.char_start : chunk.char_end] == chunk.content  # verbatim


# --- configuration bounds (single source of truth: Settings) -----------------


def test_settings_carry_plan_bounds_and_hard_caps() -> None:
    defaults = _base()
    assert defaults.chunk_size == 1000
    assert defaults.chunk_overlap == 150
    assert defaults.max_input_file_bytes == 50 * 1024 * 1024
    with pytest.raises(ValidationError):  # hard max 2000 (plan v1 6.2)
        _base(chunk_size=2001)
    with pytest.raises(ValidationError):  # hard max 400
        _base(chunk_overlap=401)
    with pytest.raises(ValidationError):  # progress precondition
        _base(chunk_size=100, chunk_overlap=100)


# --- core invariants ---------------------------------------------------------


def test_chunks_are_bounded_ordered_and_offset_faithful() -> None:
    chunks = chunk_text(_SENTENCED, size=120, overlap=30)
    assert len(chunks) > 3
    _assert_invariants(_SENTENCED, chunks, size=120)


def test_overlap_is_exact_verbatim_characters() -> None:
    overlap = 30
    chunks = chunk_text(_SENTENCED, size=120, overlap=overlap)
    for first, second in zip(chunks, chunks[1:]):
        assert second.content[:overlap] == first.content[-overlap:]


def test_chunk_ends_snap_to_sentence_boundaries() -> None:
    text = "One two three. Four five six. Seven eight nine ten."
    chunks = chunk_text(text, size=20, overlap=0)
    assert chunks[0].content == "One two three."  # not a mid-sentence hard cut
    assert chunks[1].content.strip() == "Four five six."


def test_paragraph_boundaries_are_chunk_ends() -> None:
    text = "Para one line.\n\nPara two line."
    chunks = chunk_text(text, size=25, overlap=0)
    assert len(chunks) == 2
    assert chunks[0].content == "Para one line."
    assert chunks[1].content.strip() == "Para two line."


def test_long_sentence_hard_splits_without_stalling() -> None:
    text = "A" * 2500  # one sentence, no boundaries at all
    chunks = chunk_text(text, size=1000, overlap=0)
    assert [len(chunk.content) for chunk in chunks] == [1000, 1000, 500]
    assert "".join(chunk.content for chunk in chunks) == text


def test_long_sentence_overlap_still_progresses() -> None:
    text = "A" * 2500
    chunks = chunk_text(text, size=1000, overlap=100)
    _assert_invariants(text, chunks, size=1000)
    assert chunks[-1].char_end == 2500  # full coverage to the end
    for first, second in zip(chunks, chunks[1:]):
        assert second.char_start == first.char_end - 100  # exact overlap step


@pytest.mark.parametrize("data", ["", "   ", "\r\n\t\n  "])
def test_blank_input_produces_no_chunks(data: str) -> None:
    assert chunk_text(data, size=100, overlap=10) == []


@pytest.mark.parametrize("size", [10, 37, 120])
def test_deterministic_replay(size: int) -> None:
    first = chunk_text(_SENTENCED, size=size, overlap=7)
    second = chunk_text(_SENTENCED, size=size, overlap=7)
    assert first == second


@pytest.mark.parametrize(
    "length,expected",
    [(99, [99]), (100, [100]), (101, [100, 1])],
)
def test_exact_size_boundaries(length: int, expected: list[int]) -> None:
    text = "X" * length
    chunks = chunk_text(text, size=100, overlap=0)
    assert [len(chunk.content) for chunk in chunks] == expected
    assert "".join(chunk.content for chunk in chunks) == text


def test_unicode_text_is_bounded_and_deterministic() -> None:
    text = (
        "这是一个测试。这是另一个测试。 "  # CJK with full-width terminators
        "🚀🚀 launch notes café naïve ‍💻\n\n"
        "Final paragraph with emoji 😀 ends here."
    )
    chunks = chunk_text(text, size=32, overlap=6)
    assert chunks
    _assert_invariants(text, chunks, size=32)
    assert chunks == chunk_text(text, size=32, overlap=6)


@pytest.mark.parametrize(
    "text",
    [
        "." * 100,
        "\n" * 100,
        "a. " * 100,
        "。" * 100,  # CJK terminators without whitespace: hard-cut fallback
        "🙂" * 100,
        "a" * 17,
        "Mixed 。 text. with 。 separators. 🚀",
    ],
)
@pytest.mark.parametrize("size,overlap", [(1, 0), (2, 1), (3, 2), (7, 3), (50, 49)])
def test_pathological_inputs_terminate_with_invariants(
    text: str, size: int, overlap: int
) -> None:
    chunks = chunk_text(text, size=size, overlap=overlap)  # must terminate
    _assert_invariants(text, chunks, size=size)


def test_pure_overlap_tail_chunk_is_deduplicated() -> None:
    # Document tail inside the overlap region: the naive window would repeat
    # the previous chunk's span exactly; it must be skipped, not re-emitted.
    text = "Hello there. Hi"
    chunks = chunk_text(text, size=10, overlap=5)
    spans = [(chunk.char_start, chunk.char_end) for chunk in chunks]
    assert spans == [(0, 10), (5, 12), (12, 15)]
    assert [chunk.content for chunk in chunks] == ["Hello ther", " there.", " Hi"]
    ends = [chunk.char_end for chunk in chunks]
    assert ends == sorted(set(ends))  # strictly growing, no duplicate tails


def test_invalid_parameters_rejected() -> None:
    with pytest.raises(ValueError, match="size must be >= 1"):
        chunk_text("abc", size=0, overlap=0)
    with pytest.raises(ValueError, match="overlap must be >= 0"):
        chunk_text("abc", size=10, overlap=-1)
    with pytest.raises(ValueError, match="makes progress"):
        chunk_text("abc", size=10, overlap=10)
