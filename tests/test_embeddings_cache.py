"""Phase 3 cache-copy tests (plan v2.4 decision D2; section 10.2; Z9).

Small synthetic fixtures only, always under the project's ``data\tmp``
(storage_tmp). No network, no embedding library, no model - the source
fixture is a miniature hub-layout directory tree built by the test itself.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from rag_platform.db.sqlite import PathContainmentError
from rag_platform.embeddings import (
    CacheCopyError,
    copy_model_cache,
    verify_cache_copy,
)

_REVISION = "rev123abc"


def _build_source(root: Path) -> Path:
    """Build a miniature, well-formed hub model cache under *root*."""
    source = root / "src_hub" / "models--test--encoder"
    (source / "refs").mkdir(parents=True)
    (source / "refs" / "main").write_text(f"{_REVISION}\n", encoding="utf-8")
    snapshot = source / "snapshots" / _REVISION
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text('{"dim": 8}', encoding="utf-8")
    (snapshot / "sub").mkdir()
    (snapshot / "sub" / "weights.bin").write_bytes(bytes(range(256)) * 8)
    (source / "blobs").mkdir()
    (source / ".no_exist" / _REVISION).mkdir(parents=True)
    (source / ".no_exist" / _REVISION / "x.json").write_text("", encoding="utf-8")
    return source


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot_files(source: Path) -> list[Path]:
    return sorted(p for p in source.rglob("*") if p.is_file())


def test_copy_replicates_and_verifies(storage_tmp: Path) -> None:
    source = _build_source(storage_tmp)
    destination = storage_tmp / "dst_hub" / "models--test--encoder"

    before = {p.relative_to(source): _sha256(p) for p in _snapshot_files(source)}
    report = copy_model_cache(source, destination)

    assert report.verified is True
    assert report.snapshot_revision == _REVISION
    assert report.file_count == 4  # refs/main + 2 snapshot files + .no_exist
    assert report.copied == 4
    assert report.skipped_identical == 0
    assert report.total_bytes == sum(p.stat().st_size for p in _snapshot_files(source))

    after = {p.relative_to(source): _sha256(p) for p in _snapshot_files(source)}
    assert after == before  # source (C:-style original) never modified

    # full replica, hash-identical, including blobs/ dir and .no_exist/
    for relative, digest in before.items():
        copied_file = destination / relative
        assert copied_file.is_file()
        assert _sha256(copied_file) == digest
    assert (destination / "blobs").is_dir()
    assert (destination / ".no_exist" / _REVISION / "x.json").is_file()


def test_copy_is_idempotent(storage_tmp: Path) -> None:
    source = _build_source(storage_tmp)
    destination = storage_tmp / "dst_hub" / "models--test--encoder"

    first = copy_model_cache(source, destination)
    second = copy_model_cache(source, destination)

    assert first.copied == 4 and first.skipped_identical == 0
    assert second.copied == 0
    assert second.skipped_identical == 4
    assert second.verified is True


def test_copy_recopies_divergent_file_and_verify_detects_tamper(
    storage_tmp: Path,
) -> None:
    source = _build_source(storage_tmp)
    destination = storage_tmp / "dst_hub" / "models--test--encoder"
    copy_model_cache(source, destination)

    target = destination / "snapshots" / _REVISION / "config.json"
    target.write_text('{"dim": 666}', encoding="utf-8")

    assert verify_cache_copy(source, destination).verified is False

    repair = copy_model_cache(source, destination)
    assert repair.copied == 1
    assert repair.skipped_identical == 3
    assert repair.verified is True
    assert verify_cache_copy(source, destination).verified is True


def test_verify_flags_extra_destination_files(storage_tmp: Path) -> None:
    source = _build_source(storage_tmp)
    destination = storage_tmp / "dst_hub" / "models--test--encoder"
    copy_model_cache(source, destination)

    (destination / "stray.tmp").write_text("unexpected", encoding="utf-8")
    assert verify_cache_copy(source, destination).verified is False


def test_destination_outside_project_rejected(storage_tmp: Path) -> None:
    source = _build_source(storage_tmp)
    escaped = Path("C:/Windows/Temp/phase3_cache_must_not_exist")
    with pytest.raises(PathContainmentError):
        copy_model_cache(source, escaped)
    assert not escaped.exists()


@pytest.mark.parametrize(
    "kind", ["missing", "not_a_dir", "no_refs", "bad_revision", "empty"]
)
def test_malformed_sources_rejected(storage_tmp: Path, kind: str) -> None:
    if kind == "missing":
        bad = storage_tmp / "does_not_exist"
    elif kind == "not_a_dir":
        bad = storage_tmp / "a_file"
        bad.write_text("file, not dir", encoding="utf-8")
    elif kind == "no_refs":
        bad = storage_tmp / "bare"
        (bad / "snapshots").mkdir(parents=True)
    elif kind == "bad_revision":
        bad = storage_tmp / "mismatch"
        (bad / "refs").mkdir(parents=True)
        (bad / "refs" / "main").write_text("rev_absent\n", encoding="utf-8")
        (bad / "snapshots").mkdir()
    else:  # "empty": valid layout, but no files at all
        bad = storage_tmp / "empty"
        (bad / "refs").mkdir(parents=True)
        (bad / "snapshots" / _REVISION).mkdir(parents=True)
        (bad / "refs" / "main").write_text(f"{_REVISION}\n", encoding="utf-8")

    with pytest.raises(CacheCopyError):
        copy_model_cache(bad, storage_tmp / "dst" / "models--test--encoder")
    assert not (storage_tmp / "dst").exists()
