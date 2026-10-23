"""Phase 4 loader tests (plan v2.4 section 15, Phase 4 row; assumption A7).

Offline, deterministic, tiny: every file is generated inside the project's
``data/tmp`` workspace (fixture-asserted containment). No network, no
models, no services, no downloaded fixtures. Error-reporting tests assert
that failures never leak absolute paths or document content (I16-style).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pdf_fixtures import malformed_pdf, minimal_pdf
from rag_platform.config import PROJECT_ROOT
from rag_platform.ingestion.errors import (
    EmptyDocumentError,
    FileTooLargeError,
    MalformedDocumentError,
    NoExtractableTextError,
    PathTraversalError,
    UnreadableFileError,
    UnsupportedFormatError,
)
from rag_platform.ingestion.loaders import load_document

_CAP = 50 * 1024 * 1024  # plan v1 section 6.2 per-file ingest cap


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


# --- valid extraction --------------------------------------------------------


def test_valid_txt_document(storage_tmp: Path) -> None:
    text = "alpha beta gamma\nsecond line\n"
    path = _write(storage_tmp / "doc.txt", text.encode("utf-8"))
    document = load_document(path, max_bytes=_CAP)
    assert document.format == "txt"
    assert document.text == text  # verbatim extraction
    assert document.content_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert document.size_bytes == len(text.encode("utf-8"))
    assert document.source_name == "doc.txt"
    assert document.source_rel_path == path.relative_to(PROJECT_ROOT).as_posix()
    assert document.source_mtime.endswith("Z") and "T" in document.source_mtime


def test_markdown_variants_extract_verbatim(storage_tmp: Path) -> None:
    markdown = "# Title\n\nSome **bold** text.\n"
    for name in ("notes.md", "notes.markdown"):
        path = _write(storage_tmp / name, markdown.encode("utf-8"))
        document = load_document(path, max_bytes=_CAP)
        assert document.format == "md"
        assert document.text == markdown


def test_text_based_pdf_extraction(storage_tmp: Path) -> None:
    path = _write(storage_tmp / "paper.pdf", minimal_pdf("Hello Phase 4 pdf text"))
    document = load_document(path, max_bytes=_CAP)
    assert document.format == "pdf"
    assert "Hello Phase 4 pdf text" in document.text


def test_bom_is_tolerated(storage_tmp: Path) -> None:
    path = _write(storage_tmp / "bom.txt", b"\xef\xbb\xbfhello there")
    assert load_document(path, max_bytes=_CAP).text == "hello there"


def test_uppercase_extension_routes_to_txt(storage_tmp: Path) -> None:
    path = _write(storage_tmp / "UPPER.TXT", b"upper case extension")
    assert load_document(path, max_bytes=_CAP).format == "txt"


def test_loads_are_deterministic(storage_tmp: Path) -> None:
    path = _write(storage_tmp / "twice.txt", b"deterministic content")
    first = load_document(path, max_bytes=_CAP)
    second = load_document(path, max_bytes=_CAP)
    assert first == second


# --- typed failures ----------------------------------------------------------


def test_pdf_without_extractable_text_rejected(storage_tmp: Path) -> None:
    path = _write(storage_tmp / "scan.pdf", minimal_pdf(None))
    with pytest.raises(NoExtractableTextError) as excinfo:
        load_document(path, max_bytes=_CAP)
    assert excinfo.value.code == "no_extractable_text"


def test_malformed_pdf_rejected(storage_tmp: Path) -> None:
    path = _write(storage_tmp / "broken.pdf", malformed_pdf())
    with pytest.raises(MalformedDocumentError):
        load_document(path, max_bytes=_CAP)


@pytest.mark.parametrize("name", ["doc.docx", "doc.exe", "doc.tar.gz", "noext"])
def test_unsupported_extensions_rejected(storage_tmp: Path, name: str) -> None:
    path = _write(storage_tmp / name, b"whatever")
    with pytest.raises(UnsupportedFormatError) as excinfo:
        load_document(path, max_bytes=_CAP)
    assert excinfo.value.code == "unsupported_format"
    assert "supported" in excinfo.value.detail


@pytest.mark.parametrize("data", [b"", b" \r\n\t   \n"])
def test_empty_and_whitespace_only_files_rejected(
    storage_tmp: Path, data: bytes
) -> None:
    path = _write(storage_tmp / "empty.txt", data)
    with pytest.raises(EmptyDocumentError):
        load_document(path, max_bytes=_CAP)


def test_invalid_utf8_rejected(storage_tmp: Path) -> None:
    path = _write(storage_tmp / "binary.txt", b"\xff\xfe\x00\x01 not text")
    with pytest.raises(MalformedDocumentError) as excinfo:
        load_document(path, max_bytes=_CAP)
    assert "UTF-8" in excinfo.value.detail


def test_size_limit_exact_boundary(storage_tmp: Path) -> None:
    raw = b"x" * 64
    path = _write(storage_tmp / "edge.txt", raw)
    assert load_document(path, max_bytes=64).size_bytes == 64  # at the cap: ok
    with pytest.raises(FileTooLargeError) as excinfo:
        load_document(path, max_bytes=63)  # one byte over: rejected by stat
    assert "64" in excinfo.value.detail and "63" in excinfo.value.detail


def test_directory_input_rejected(storage_tmp: Path) -> None:
    folder = storage_tmp / "folder.txt"
    folder.mkdir()
    with pytest.raises(UnreadableFileError):
        load_document(folder, max_bytes=_CAP)


def test_missing_file_rejected(storage_tmp: Path) -> None:
    with pytest.raises(UnreadableFileError):
        load_document(storage_tmp / "absent.txt", max_bytes=_CAP)


def test_unreadable_file_is_typed(
    storage_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(storage_tmp / "locked.txt", b"content")
    original = Path.read_bytes

    def _denied(self: Path) -> bytes:
        if self == path:
            raise PermissionError(13, "denied")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", _denied)
    with pytest.raises(UnreadableFileError) as excinfo:
        load_document(path, max_bytes=_CAP)
    assert excinfo.value.code == "unreadable_file"


def test_file_growing_during_read_is_rejected(
    storage_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # TOCTOU: stat reports a small file, then the file grows before (or
    # during) read_bytes. The post-read re-check must detect the over-read
    # and reject it with the same typed error - detection and rejection, not
    # a claim that the transient allocation was prevented.
    path = _write(storage_tmp / "grow.txt", b"small on disk")
    original = Path.read_bytes

    def _grown(self: Path) -> bytes:
        if self == path:
            return b"x" * 65  # 65 bytes read against the 64-byte cap below
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", _grown)
    with pytest.raises(FileTooLargeError) as excinfo:
        load_document(path, max_bytes=64)
    assert excinfo.value.code == "file_too_large"
    # Path-safe, content-free message: byte counts and basename only.
    assert "65" in excinfo.value.detail and "64" in excinfo.value.detail
    message = str(excinfo.value)
    assert str(PROJECT_ROOT) not in message
    assert "D:\\" not in message
    assert "small on disk" not in message


# --- containment (Z9) and safe reporting (I16-style) -------------------------


@pytest.mark.parametrize(
    "escape",
    [r"..\..\..\..\outside.txt", r"D:\other\outside.txt", r"C:\Windows\win.ini"],
)
def test_paths_outside_project_root_rejected(storage_tmp: Path, escape: str) -> None:
    with pytest.raises(PathTraversalError) as excinfo:
        load_document(storage_tmp / escape, max_bytes=_CAP)
    message = str(excinfo.value)
    assert "D:" not in message and "C:" not in message
    assert str(PROJECT_ROOT) not in message


def test_error_messages_never_leak_paths_or_content(storage_tmp: Path) -> None:
    secret_content = b"secret-content-marker-8721"
    cases: list[Exception] = []
    oversized = _write(storage_tmp / "big.txt", secret_content * 40)
    try:
        load_document(oversized, max_bytes=8)
    except FileTooLargeError as exc:
        cases.append(exc)
    malformed = _write(storage_tmp / "latin1.txt", b"\xe4\xe5\xe6 latin1 bytes")
    try:
        load_document(malformed, max_bytes=_CAP)
    except MalformedDocumentError as exc:
        cases.append(exc)
    assert cases  # both failure modes exercised
    for exc in cases:
        message = str(exc)
        assert str(PROJECT_ROOT) not in message
        assert "D:\\" not in message and "C:\\" not in message
        assert "secret-content-marker-8721" not in message
