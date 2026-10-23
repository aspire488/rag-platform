"""Phase 4 declared-date tests (plan v2.4 section 4.1; implementation-defined
format rules documented in ``rag_platform.ingestion.dates``).

The declared date is an author field only - file mtime is never used to
infer it (asserted end-to-end in ``test_ingestion_pipeline.py``). Offline,
deterministic, no I/O.
"""

from __future__ import annotations

from rag_platform.ingestion.dates import LEADING_BLOCK_LINES, extract_declared_date


def test_valid_iso_date_captured() -> None:
    result = extract_declared_date("date: 2026-03-04\n\nBody text follows.")
    assert result.value == "2026-03-04"
    assert result.status == "ok"


def test_date_line_is_case_and_whitespace_tolerant() -> None:
    for text in (
        "  Date: 2026-01-02",
        "DATE:2026-01-02",
        "# Doc\n\n date:  2026-01-02",
    ):
        result = extract_declared_date(text)
        assert (result.value, result.status) == ("2026-01-02", "ok"), text


def test_missing_declaration() -> None:
    result = extract_declared_date("Just prose, no metadata here.")
    assert (result.value, result.status) == (None, "missing")


def test_calendar_dates_only_are_accepted() -> None:
    for raw in (
        "date: yesterday",
        "date: 2026-13-40",
        "date:",
        "date: 03/04/2026",
        "date: 2026-3-4",
    ):
        result = extract_declared_date(raw + "\nbody")
        assert result.status == "malformed", raw
        assert result.value is None


def test_conflicting_declarations_are_ambiguous_not_resolved() -> None:
    text = "date: 2026-01-01\n\ndate: 2026-02-02\n\nbody"
    result = extract_declared_date(text)
    assert (result.value, result.status) == (None, "ambiguous")


def test_repeated_identical_declarations_count_as_one() -> None:
    text = "date: 2026-01-01\n\ndate: 2026-01-01\n\nbody"
    result = extract_declared_date(text)
    assert (result.value, result.status) == ("2026-01-01", "ok")


def test_declaration_deeper_in_document_is_ordinary_content() -> None:
    filler = "\n".join(f"Line {i} of ordinary prose." for i in range(25))
    text = f"{filler}\ndate: 2026-05-05\n"
    result = extract_declared_date(text)
    assert (result.value, result.status) == (None, "missing")


def test_declaration_within_leading_block_is_captured() -> None:
    heading = "# Title\n\nIntro sentence.\n"
    text = heading + "date: 2026-06-06\n\n" + "more prose"
    result = extract_declared_date(text)
    assert (result.value, result.status) == ("2026-06-06", "ok")


def test_leading_block_size_is_bounded() -> None:
    assert LEADING_BLOCK_LINES > 0  # documented scan bound exists
    # Declaration on the LAST line of the leading block is captured...
    inside = "\n".join(
        [f"Filler line {i}." for i in range(LEADING_BLOCK_LINES - 1)]
        + ["date: 2026-07-07"]
    )
    assert extract_declared_date(inside).status == "ok"
    # ...but one line beyond the block is ordinary content.
    outside = "\n".join(
        [f"Filler line {i}." for i in range(LEADING_BLOCK_LINES)] + ["date: 2026-07-07"]
    )
    assert extract_declared_date(outside).status == "missing"


def test_extraction_is_deterministic() -> None:
    text = "date: 2026-08-08\nbody date: not-a-declaration in prose"
    assert extract_declared_date(text) == extract_declared_date(text)
