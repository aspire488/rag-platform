"""Citation mapping and fabrication prevention (Phase 6; I14 / section 5.4).

Citations are **constructive**: the generated answer may only *reference*
evidence - a 1-based served-evidence ordinal, a served chunk id, and an
optional absolute character sub-range. The resolved ``Citation`` is built
exclusively from the served ``EvidenceSpan`` records, so chunk ids, doc
ids, and ranges always come from OUR evidence, never from model text:
fabricated provenance is structurally impossible.

Marker grammar (bracketed, deliberate):

- ``[C<n>]`` - the n-th served evidence line (full served span).
- ``[C<n>@<start>-<end>]`` - absolute document offsets inside that span.
- ``[C<chunk_id>]`` / ``[C<chunk_id>@<start>-<end>]`` - direct id form.

Validation is all-or-nothing: any bracketed ``[C...]`` marker that is
malformed (``[C]``, ``[C3@abc]``), unresolvable (unknown index or unknown
chunk id - "the model invented a citation"), or out of the served span
rejects the ENTIRE answer with a typed reason and an empty citation
tuple - a partially resolvable answer is never returned as grounded.
Zero citations is also a failure (an ungrounded answer, ``no_citations``).
Identical duplicate citations are de-duplicated (citing one chunk twice
is normal prose, not fabrication).

Documented boundary: only well-formed ``[C...]`` bracket closures are
considered citation claims; an unclosed ``[C1`` fragment is prose. Real
prose containing a bracketed "C..." token that is not an evidence
reference would be rejected conservatively - safe by direction (an
honest answer lost, never a fabricated citation served).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from rag_platform.generation.errors import GenerationRequestError
from rag_platform.generation.types import (
    Citation,
    CitationValidation,
    EvidenceSpan,
    InvalidCitationReason,
)

#: Citation claims: ``[C<payload>]`` with optional ``@start-end`` span.
_CITATION_RE = re.compile(r"\[C([^\]]*)\]")
_SPAN_RE = re.compile(r"^(\d+)-(\d+)$")


def validate_citations(
    text: str, evidence: Sequence[EvidenceSpan]
) -> CitationValidation:
    """Validate every citation claim in *text* against the served *evidence*.

    Pure and deterministic: same inputs, same verdict. Never raises for
    untrusted answer content - invalidity is a typed result, not an
    exception; malformed *inputs* (wrong types) raise the typed contract
    error.
    """
    if not isinstance(text, str):
        raise GenerationRequestError(
            f"citation validation requires answer text; got {type(text).__name__}"
        )
    if isinstance(evidence, (str, bytes)) or not isinstance(evidence, Sequence):
        raise GenerationRequestError(
            f"evidence must be a sequence of EvidenceSpan; got {type(evidence).__name__}"
        )
    spans: list[EvidenceSpan] = []
    for span in evidence:
        if not isinstance(span, EvidenceSpan):
            raise GenerationRequestError(
                "evidence must contain only EvidenceSpan items; got "
                f"{type(span).__name__}"
            )
        spans.append(span)

    by_id = {span.chunk_id: (index, span) for index, span in enumerate(spans, start=1)}

    claims = _CITATION_RE.findall(text)
    if not claims:
        return CitationValidation(
            valid=False, citations=(), reason=InvalidCitationReason.NO_CITATIONS
        )

    resolved: dict[tuple[str, int, int], Citation] = {}
    for raw_payload in claims:
        payload = raw_payload.strip()
        span_payload: str | None = None
        if "@" in payload:
            payload, _, span_payload = payload.partition("@")
            payload = payload.strip()
        if not payload:
            return _invalid(InvalidCitationReason.MALFORMED)

        # Resolve the reference: ordinal or served chunk id (never trust
        # model-supplied provenance - lookup only).
        if payload.isdigit():
            index = int(payload)
            if not 1 <= index <= len(spans):
                return _invalid(InvalidCitationReason.UNKNOWN_REFERENCE)
            span = spans[index - 1]
        else:
            located = by_id.get(payload)
            if located is None:
                # Syntactically broken reference vs plausible-but-unknown id:
                # both invalidate; the reason distinguishes them.
                if payload in ("-",) or " " in payload:
                    return _invalid(InvalidCitationReason.MALFORMED)
                return _invalid(InvalidCitationReason.UNKNOWN_REFERENCE)
            index, span = located

        # Optional sub-range: must lie strictly inside the served span.
        if span_payload is not None:
            match = _SPAN_RE.match(span_payload.strip())
            if match is None:
                return _invalid(InvalidCitationReason.MALFORMED)
            char_start, char_end = int(match.group(1)), int(match.group(2))
            if not (span.char_start <= char_start < char_end <= span.char_end):
                return _invalid(InvalidCitationReason.SPAN_OUT_OF_RANGE)
        else:
            char_start, char_end = span.char_start, span.char_end

        citation = Citation(
            index=index,
            chunk_id=span.chunk_id,
            doc_id=span.doc_id,
            char_start=char_start,
            char_end=char_end,
        )
        resolved.setdefault(
            (citation.chunk_id, citation.char_start, citation.char_end), citation
        )

    if not resolved:
        return _invalid(InvalidCitationReason.NO_CITATIONS)
    return CitationValidation(
        valid=True, citations=tuple(resolved.values()), reason=None
    )


def _invalid(reason: InvalidCitationReason) -> CitationValidation:
    return CitationValidation(valid=False, citations=(), reason=reason)
