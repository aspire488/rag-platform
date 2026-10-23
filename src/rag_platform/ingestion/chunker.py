"""Bounded, sentence-aware, deterministic chunker (plan v2.4 Phase 4;
operational bounds carried from plan v1 section 6.2: chunk ~1000 chars /
overlap ~150, sentence-aware, hard max 2000/400).

Algorithm (pure function of its input - identical text and parameters
always produce identical chunks):

- Chunk *ends* snap to the last sentence boundary (``.``/``!``/``?``/``…``
  with optional closing quotes/brackets, followed by whitespace) or
  paragraph boundary (blank line) that fits inside the size window;
  when no boundary fits (a sentence longer than the whole window), the
  chunk hard-cuts at exactly ``size`` characters so progress is guaranteed.
- The next chunk starts at exactly ``previous_end - overlap`` characters
  (verbatim character overlap; when the overlap would not advance the
  cursor - e.g. a chunk shorter than the overlap - the chunker falls back
  to the chunk end, degrading overlap to zero rather than looping).
- Content is verbatim: only trailing whitespace is trimmed from a chunk's
  end, so ``text[char_start:char_end] == content`` always holds and chunk
  boundaries trace exactly back into the source document.

Offset semantics (citations depend on this): ``char_start``/``char_end``
are **Unicode code-point offsets** (Python ``str`` indices) into the
*extracted text* that was chunked - not byte offsets into the original
file, not UTF-16 units, and not token offsets. SQLite's ``length()`` counts
code points too, so the schema's ``length(content) <= 2000`` check and this
chunker measure the same unit. For ``.txt``/``.md`` the extracted text is
the decoded file content (BOM stripped); for PDFs it is a **lossy
page-text reconstruction** (per-page text joined with a blank line,
whitespace-only pages dropped) - offsets therefore never identify PDF byte
positions or visual layout, only positions within the extracted string.

Guarantees: every emitted chunk is non-empty (at least one non-whitespace
character) and at most ``size`` characters; chunks are ordered with
strictly increasing spans (a window fully contained in the previous
chunk's span - possible when a document tail falls inside the overlap
region - is skipped as a pure-overlap duplicate); the loop provably
terminates (the cursor strictly increases every iteration - invariant I7:
no unbounded growth, no infinite loops). No intermediate copies beyond the
input text and the emitted slices.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """One chunk: verbatim span of the source text plus its ordinal.

    ``char_start``/``char_end`` are Unicode code-point offsets into the
    extracted text (document-relative); see the module docstring for the
    PDF lossiness caveat.
    """

    ordinal: int
    content: str
    char_start: int
    char_end: int


# Sentence end: terminator (+ optional closers) followed by whitespace or EOF.
# ASCII and full-width CJK terminators are recognized; in scripts written
# without whitespace between sentences no boundary matches and the chunker
# deterministically hard-cuts at the size limit (documented limitation).
_SENTENCE_BOUNDARY = re.compile(r"[.!?…。！？]+[\"'”’)\]]*(?=\s|$)")
# Paragraph boundary: a blank line; anchored at its first newline so the
# chunk content never includes the separating blank line.
_PARAGRAPH_BOUNDARY = re.compile(r"\n[ \t\r]*\n")


def _validate_bounds(size: int, overlap: int) -> None:
    if size < 1:
        raise ValueError(f"chunk size must be >= 1, got {size}")
    if overlap < 0:
        raise ValueError(f"chunk overlap must be >= 0, got {overlap}")
    if overlap >= size:
        raise ValueError(
            f"chunk overlap ({overlap}) must be smaller than chunk size ({size}) "
            "so chunking always makes progress"
        )


def _boundary_ends(text: str) -> list[int]:
    """Sorted, de-duplicated candidate end offsets (sentence/paragraph)."""
    ends = [match.end() for match in _SENTENCE_BOUNDARY.finditer(text)]
    ends.extend(match.start() for match in _PARAGRAPH_BOUNDARY.finditer(text))
    ordered = sorted(position for position in ends if 0 < position <= len(text))
    return list(dict.fromkeys(ordered))


def _select_end(boundaries: list[int], start: int, hard_end: int) -> int:
    """Last boundary in ``(start, hard_end]``; otherwise the hard cut."""
    index = bisect.bisect_right(boundaries, hard_end) - 1
    if index >= 0 and boundaries[index] > start:
        return boundaries[index]
    return hard_end


def chunk_text(text: str, *, size: int, overlap: int) -> list[Chunk]:
    """Split *text* into bounded, ordered, sentence-aware chunks.

    *size* and *overlap* are explicit (plan bounds live in
    :class:`rag_platform.config.Settings` as the single validated source);
    the chunker re-validates them because it is a public entry point.
    """
    _validate_bounds(size, overlap)
    chunks: list[Chunk] = []
    length = len(text)
    if length == 0:
        return chunks
    boundaries = _boundary_ends(text)
    cursor = 0
    while cursor < length:
        start = cursor
        hard_end = min(start + size, length)
        end = _select_end(boundaries, start, hard_end)
        window = text[start:end]
        if window.strip():  # whitespace-only windows are skipped, never emitted
            content = window.rstrip()
            content_end = start + len(content)
            # A window that extends no further than the previous chunk's end
            # is a pure-overlap duplicate (chunk ends are non-decreasing);
            # skipping it keeps chunks strictly growing. Progress is
            # unaffected: the cursor advance below is independent of emission.
            if not (chunks and content_end <= chunks[-1].char_end):
                chunks.append(
                    Chunk(
                        ordinal=len(chunks),
                        content=content,
                        char_start=start,
                        char_end=content_end,
                    )
                )
        else:
            content_end = start
        if end >= length:
            break
        # Strict progress: the next cursor is always > start. The overlap
        # point is measured from the emitted (trimmed) chunk end; when that
        # would not advance the cursor (chunk shorter than the overlap, or a
        # whitespace-only window) the chunker falls back to the untrimmed
        # boundary end, degrading overlap to zero rather than looping.
        progress_end = content_end if content_end > start else end
        next_cursor = progress_end - overlap
        if next_cursor <= start:
            next_cursor = progress_end if progress_end > start else end
        cursor = next_cursor
    return chunks
