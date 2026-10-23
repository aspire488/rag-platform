"""Deterministic, local-only prompt-injection pattern scanner (plan v2.4
Phase 4; invariants I8/I11: advisory signals only).

Stance (plan-faithful and deliberately narrow):

- Document text is always **untrusted data**. Nothing here executes,
  obeys, or interprets it; the scanner only matches bounded regular
  expressions against the text and records findings.
- Findings are **advisory**: a quarantined chunk is still stored verbatim
  and remains retrievable. Quarantine flags never make authorization
  decisions (I8) and are surfaced in structured metadata only.
- Pattern scanning is a **defensive signal, not a guarantee**: it catches
  known instruction-like phrasings (documented categories below) and will
  never detect every injection. False positives are expected on text that
  merely quotes or discusses such phrasings; the honest response to a
  finding is human-visible metadata, never silent content removal.
- Bounded (I7): at most :data:`MAX_FINDINGS_PER_CHUNK` findings are
  recorded per chunk, with an explicit ``truncated`` marker when more
  matched. No LLM judging, no remote moderation, no model loading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Hard cap on stored findings per chunk (I7 bounded everything).
MAX_FINDINGS_PER_CHUNK = 50

#: (category, compiled pattern). Categories are stable identifiers stored in
#: chunk metadata; patterns are anchored on distinctive instruction-like
#: phrasings with bounded gap quantifiers (no unbounded scans).
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_previous_instructions",
        re.compile(
            r"(?i)\b(?:ignore|disregard|forget)\s+"
            r"(?:all\s+|any\s+|the\s+|your\s+|previous\b|prior\b|above\b|earlier\b|preceding\b)"
        ),
    ),
    (
        "role_override",
        re.compile(
            r"(?i)\b(?:you\s+are\s+now\b|pretend\s+(?:to\s+be|you\s+are)\b|"
            r"act\s+as\s+(?:an?\s+)?(?:evil|unrestricted|unfiltered|uncensored))"
        ),
    ),
    (
        "system_prompt_exfiltration",
        re.compile(
            r"(?i)\b(?:reveal|print|repeat|output|show|display|leak)\b"
            r"[^.!?\n]{0,40}\b(?:system\s+prompt|hidden\s+instructions?)"
        ),
    ),
    (
        "fake_system_message",
        re.compile(
            r"(?i)\b(?:new\s+instructions?\s*:|system\s+message\s*:|developer\s+mode)"
        ),
    ),
    (
        "jailbreak_marker",
        re.compile(
            r"(?i)\b(?:DAN\s+mode|jailbreak|disable\s+your\s+(?:rules|restrictions))"
        ),
    ),
    (
        "credential_harvest",
        re.compile(
            r"(?i)\b(?:api[\s_-]?key|password|passwd|token|secret)\b\s*(?:=|:)\s*\S"
        ),
    ),
    (
        "exfiltration_request",
        re.compile(
            r"(?i)\b(?:send|post|upload|forward|exfiltrate)\b"
            r"[^.!?\n]{0,40}\b(?:https?://|externally?|to\s+an?\s+(?:remote|external)\b)"
        ),
    ),
)


@dataclass(frozen=True)
class InjectionFinding:
    """One advisory finding: stable category plus verbatim char offsets.

    Offsets are **chunk-relative**: they index the scanned chunk text (the
    same space as ``Chunk.content``), unlike ``chunks.char_start``/
    ``char_end`` in the database, which are document-relative offsets into
    the extracted document text. The two offset spaces are not
    interchangeable.
    """

    category: str
    start: int
    end: int

    def to_json_dict(self) -> dict[str, object]:
        return {"category": self.category, "start": self.start, "end": self.end}


@dataclass(frozen=True)
class ScanResult:
    """Scan outcome for one chunk of untrusted text."""

    findings: tuple[InjectionFinding, ...]
    truncated: bool

    @property
    def quarantined(self) -> bool:
        return bool(self.findings)


def scan_text(text: str) -> ScanResult:
    """Scan *text* (untrusted) against the bounded pattern set.

    Deterministic: findings are ordered by ``(start, category)``; offsets
    index into *text* verbatim. When called from the pipeline *text* is one
    chunk, so findings are chunk-relative (never document-relative).
    Never raises on any input.
    """
    findings: list[InjectionFinding] = []
    truncated = False
    for category, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if len(findings) >= MAX_FINDINGS_PER_CHUNK:
                truncated = True
                break
            findings.append(
                InjectionFinding(
                    category=category, start=match.start(), end=match.end()
                )
            )
    findings.sort(key=lambda finding: (finding.start, finding.category))
    return ScanResult(findings=tuple(findings), truncated=truncated)
