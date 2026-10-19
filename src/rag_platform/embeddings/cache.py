"""MiniLM hub-cache copy, C: -> D: (plan v2.4 Phase 3; decision D2
*Approved 2026-10-19*: "Copy MiniLM cache C: -> D:; project-local HF_HOME
(no system env changes)"; section 10.2 "MiniLM cache copied C:->D: (D2 - a
read from C:, all writes land on D:)"; Z9 (C: is read-only for the project),
Z6 (no downloads - the existing cache is reused, never re-fetched).

Rules this module enforces:

- **Read-only on the source.** The source is an *existing* Hugging Face hub
  model cache (the user cache on C:). Files are opened for reading and
  hashing only; nothing is ever written, moved, or deleted at the source.
- **Writes strictly contained (Z9).** The destination is resolved through
  :func:`rag_platform.db.sqlite.resolve_under_project` *before* any
  directory or file is created; an escaped destination raises
  :class:`~rag_platform.db.sqlite.PathContainmentError` and leaves nothing
  behind.
- **No download, no model load.** Only ``hashlib``/``shutil`` file
  operations on already-present artifacts; this module never imports an
  embedding library and never opens a socket.
- **Full-replica copy with hash verification.** Every file in the model
  directory (``refs/``, ``snapshots/``, ``blobs/``, ``.no_exist/``) is
  copied byte-for-byte (``copy2``, metadata preserved) and each destination
  file is SHA256-verified against the source immediately after copying; a
  final independent verification pass re-checks the whole tree and the
  ``refs``/``snapshots`` revision consistency.
- **Idempotent.** A destination file whose size and SHA256 already match
  the source is skipped, so re-running is cheap and safe. Divergent
  destination files are re-copied (the original cache is the source of
  truth; the destination is a derived artifact).
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from rag_platform.db.sqlite import resolve_under_project
from rag_platform.embeddings.minilm import EmbeddingError


class CacheCopyError(EmbeddingError):
    """Cache source layout invalid, or a copied file failed verification."""


@dataclass(frozen=True)
class CacheCopyReport:
    """Result of a copy or verification run (no secrets, hash summaries only)."""

    source: Path
    destination: Path
    snapshot_revision: str | None
    file_count: int
    total_bytes: int
    copied: int
    skipped_identical: int
    verified: bool


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(root: Path) -> Iterator[Path]:
    """All regular files under *root*, sorted for deterministic ordering."""
    yield from sorted(path for path in root.rglob("*") if path.is_file())


def _validate_source(source: Path) -> tuple[str | None, list[Path]]:
    """Validate the hub model-cache layout; return (revision, files).

    Raises :class:`CacheCopyError` for anything that is not a well-formed
    model cache - a missing or ambiguous source stops the operation instead
    of copying a partial tree or substituting another model.
    """
    if not source.exists():
        raise CacheCopyError(f"cache source does not exist: {source}")
    if not source.is_dir():
        raise CacheCopyError(f"cache source is not a directory: {source}")
    refs = source / "refs"
    snapshots = source / "snapshots"
    if not refs.is_dir() or not snapshots.is_dir():
        raise CacheCopyError(
            f"not a Hugging Face hub model cache (missing refs/ or "
            f"snapshots/): {source}"
        )
    revision: str | None = None
    main_ref = refs / "main"
    if main_ref.is_file():
        revision = main_ref.read_text(encoding="utf-8").strip() or None
    if revision is not None:
        snapshot_dir = snapshots / revision
        if not snapshot_dir.is_dir():
            raise CacheCopyError(
                f"refs/main revision {revision!r} has no matching snapshot "
                f"directory under {snapshots} (inconsistent cache)"
            )
        if not any(path.is_file() for path in snapshot_dir.rglob("*")):
            raise CacheCopyError(
                f"snapshot {revision!r} contains no files (incomplete cache): "
                f"{snapshot_dir}"
            )
    elif not any(path.is_file() for path in snapshots.rglob("*")):
        raise CacheCopyError(
            f"snapshots/ contains no files (incomplete cache): {snapshots}"
        )
    files = list(_iter_files(source))
    if not files:
        raise CacheCopyError(f"cache source contains no files: {source}")
    return revision, files


def copy_model_cache(source: str | Path, destination: str | Path) -> CacheCopyReport:
    """Copy the model cache at *source* to *destination* (both model dirs).

    *source* is read-only; *destination* must resolve strictly under the
    project root (validated first). Every copied file is SHA256-verified,
    followed by a full independent verification pass - the returned report
    always has ``verified=True`` or the function has raised.
    """
    source_path = Path(source)
    revision, files = _validate_source(source_path)
    dest_path = resolve_under_project(destination)  # Z9: before any mkdir
    if dest_path.exists() and not dest_path.is_dir():
        raise CacheCopyError(f"destination exists and is not a directory: {dest_path}")

    copied = 0
    skipped = 0
    total_bytes = 0
    # Replicate the full directory tree first, including empty directories
    # (an empty blobs/ is part of the hub layout); containment was validated
    # above, so every mkdir stays under the project root.
    for src_dir in sorted(path for path in source_path.rglob("*") if path.is_dir()):
        (dest_path / src_dir.relative_to(source_path)).mkdir(
            parents=True, exist_ok=True
        )
    for src_file in files:
        relative = src_file.relative_to(source_path)
        dst_file = dest_path / relative
        size = src_file.stat().st_size
        total_bytes += size
        if (
            dst_file.is_file()
            and dst_file.stat().st_size == size
            and _sha256_file(dst_file) == _sha256_file(src_file)
        ):
            skipped += 1
            continue
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        if _sha256_file(dst_file) != _sha256_file(src_file):
            raise CacheCopyError(f"copied file failed SHA256 verification: {relative}")
        copied += 1

    report = verify_cache_copy(source_path, dest_path)
    if not report.verified:  # defensive: copy loop already checks each file
        raise CacheCopyError(
            f"post-copy verification failed for {dest_path}; "
            f"mismatched files remain - re-run the copy"
        )
    return CacheCopyReport(
        source=report.source,
        destination=report.destination,
        snapshot_revision=revision,
        file_count=report.file_count,
        total_bytes=report.total_bytes,
        copied=copied,
        skipped_identical=skipped,
        verified=True,
    )


def verify_cache_copy(source: str | Path, destination: str | Path) -> CacheCopyReport:
    """Read-only verification: destination must mirror the source exactly.

    Checks file-set equality (no missing and no extra files), per-file size
    and SHA256 equality, and ``refs``/``snapshots`` revision consistency.
    Never writes anything.
    """
    source_path = Path(source)
    revision, files = _validate_source(source_path)
    dest_path = Path(destination)
    if not dest_path.is_dir():
        raise CacheCopyError(f"destination is not a directory: {dest_path}")

    total_bytes = 0
    ok = True
    for src_file in files:
        relative = src_file.relative_to(source_path)
        dst_file = dest_path / relative
        size = src_file.stat().st_size
        total_bytes += size
        if not dst_file.is_file():
            ok = False
            continue
        if dst_file.stat().st_size != size or _sha256_file(dst_file) != _sha256_file(
            src_file
        ):
            ok = False
    expected = {file.relative_to(source_path) for file in files}
    actual = {path.relative_to(dest_path) for path in _iter_files(dest_path)}
    if actual != expected:
        ok = False  # extra or missing files at the destination

    return CacheCopyReport(
        source=source_path,
        destination=dest_path,
        snapshot_revision=revision,
        file_count=len(files),
        total_bytes=total_bytes,
        copied=0,
        skipped_identical=0,
        verified=ok,
    )
