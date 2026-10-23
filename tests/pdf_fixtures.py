"""Minimal, hand-assembled PDF builders for Phase 4 loader tests.

Deterministic PDF 1.4 byte streams with a correct xref table, generated in
the project test area - no downloaded fixtures, no personal documents, no
dependencies beyond the pinned ``pypdf`` the loaders use themselves.
"""

from __future__ import annotations


def minimal_pdf(text: str | None) -> bytes:
    """A valid single-page PDF; ``text=None`` yields a page with no text.

    The text is placed with a single ``Tj`` operator in a standard content
    stream, so ``pypdf`` extracts it as page text. Parentheses and
    backslashes are escaped per the PDF string syntax.
    """
    if text is None:
        content = b""
    else:
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        (
            b"<< /Length "
            + str(len(content)).encode("ascii")
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def malformed_pdf() -> bytes:
    """Bytes with a PDF-ish header but no parseable structure."""
    return b"%PDF-1.4\nthis is not a valid PDF body at all\n%%EOF\n"
