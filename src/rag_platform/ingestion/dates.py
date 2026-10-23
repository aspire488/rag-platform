"""Declared-date capture (plan v2.4 Phase 4; section 4.1 metadata timestamps).

Plan rule: store the **author-declared** ``date:`` field, distinct from the
ingestion timestamp and from file metadata, and never infer a declared date
from the file modification time. The plan does not define a value format or
the behaviour for missing/malformed/ambiguous declarations, so this module
defines the minimal deterministic rules below and labels every outcome
explicitly:

- Only a line shaped ``date: <value>`` (case-insensitive, leading
  whitespace tolerated) inside the document's *leading block* - the first
  :data:`LEADING_BLOCK_LINES` non-empty lines - is considered a
  declaration. A ``date:``-looking line deeper in the document is ordinary
  content, not a declaration.
- The value must be an ISO calendar date ``YYYY-MM-DD`` that exists on the
  calendar; anything else is ``malformed`` (no fuzzy parsing, no locale
  guessing).
- Zero declarations -> ``missing``; two or more *differing* declarations ->
  ``ambiguous`` (no value is stored and no contradiction is auto-resolved -
  plan section 4.1: contradictions are surfaced, never resolved); repeated
  identical declarations count as one.
- The declared date is stored as given (ISO date) and always separately
  from ``ingested_at`` (the ingestion clock) and ``source_mtime`` (file
  metadata).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

#: How many non-empty lines from the document start count as the leading
#: block in which a ``date:`` declaration is recognized.
LEADING_BLOCK_LINES = 20

_DATE_LINE = re.compile(r"^\s*date\s*:\s*(.*?)\s*$", re.IGNORECASE)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DateStatus = Literal["ok", "missing", "malformed", "ambiguous"]


@dataclass(frozen=True)
class DeclaredDate:
    """Outcome of declared-date capture; ``value`` is ISO ``YYYY-MM-DD``."""

    value: str | None
    status: DateStatus


def extract_declared_date(text: str) -> DeclaredDate:
    """Extract the declared ``date:`` field from *text* (untrusted)."""
    values: list[str] = []
    seen_non_empty = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        seen_non_empty += 1
        if seen_non_empty > LEADING_BLOCK_LINES:
            break
        match = _DATE_LINE.match(line)
        if match is not None:
            values.append(match.group(1))

    if not values:
        return DeclaredDate(value=None, status="missing")
    distinct = list(dict.fromkeys(values))
    if len(distinct) > 1:
        return DeclaredDate(value=None, status="ambiguous")
    raw = distinct[0]
    if not _ISO_DATE.match(raw):
        return DeclaredDate(value=None, status="malformed")
    try:
        date.fromisoformat(raw)  # rejects impossible calendar dates
    except ValueError:
        return DeclaredDate(value=None, status="malformed")
    return DeclaredDate(value=raw, status="ok")
