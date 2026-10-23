"""Frozen evaluation dataset manifest and verification (plan v2.4 Phase 8;
sections 9.1-9.2, invariants Z8/Z9/R19).

The manifest is the single source of truth for what a dataset *is*. Every
run verifies it before anything is ingested or queried, and any failure
aborts (fail-closed): absolute paths, ``..`` traversal, symlinks, missing
files, size or SHA-256 mismatches, duplicate question IDs, invalid type /
expected-outcome labels, questions referencing unknown splits, and
malformed JSONL all raise :class:`DatasetIntegrityError` before a single
row reaches a run database.

Manifest schema (``manifest.json`` at the dataset root)::

    {
      "schema": "rag-platform-eval-manifest/1",
      "dataset_id": "synthetic_v1",
      "created_at": "2026-10-21T00:00:00Z",
      "author": "...",
      "chunk_size": 1000,
      "chunk_overlap": 150,
      "files": [
        {"path": "docs/topic_a_01.txt", "sha256": "...", "size_bytes": 61234},
        {"path": "questions/questions_dev.jsonl", "sha256": "...", "size_bytes": 12345,
         "split": "dev"},
        {"path": "questions/questions_test.jsonl", "sha256": "...", "size_bytes": 12345,
         "split": "test"}
      ]
    }

- ``dataset_id`` must match ``\\A[a-z0-9][a-z0-9_-]*\\Z``, be at most 32
  characters, and is never normalized (reject-NOT-strip).

- ``path`` values are POSIX relative paths beneath the dataset root; any
  absolute path, backslash, drive letter, or ``..`` component is rejected.
- Question files carry an explicit ``split`` of ``dev`` or ``test``; each
  split must appear exactly once.
- Verification re-hashes every listed file and re-stats every size. Files
  present on disk but *not* listed are ignored (ingest reads only the
  manifest list - no ambient-directory ingestion).

Question JSONL records (one JSON object per line)::

    {"id": "q0001", "type": "single-hop", "expected_outcome": "answer",
     "question": "...", "gold_chunk_ids": ["..."], "gold_answer": "...",
     "gold_answer_span": {"chunk_id": "...", "char_start": 0, "char_end": 12},
     "structures": ["near_duplicate_trap"]}

``gold_answer_span`` is optional and informational at M1 (token-F1 uses
``gold_answer``); it must, when present, resolve inside a gold chunk.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

MANIFEST_SCHEMA = "rag-platform-eval-manifest/1"

QUESTION_TYPES = frozenset(
    {"single-hop", "multi-hop", "unanswerable", "conflict-aware", "injection-adjacent"}
)
EXPECTED_OUTCOMES = frozenset({"answer", "abstain", "flag-conflict"})
SPLITS = ("dev", "test")

_MAX_QUESTION_CHARS = 2000
_MAX_QUESTIONS_PER_FILE = 500

_DATASET_ID_PATTERN = re.compile(r"\A[a-z0-9][a-z0-9_-]*\Z")
# run-id budget derives from the split at runtime:
# 64 - "eval_"(5) - (1 + len(split) + 1 + stamp(16) + 1 + hex(8))
#   -> dev 29 / test 28 for the dataset-id SEGMENT.
MAX_DATASET_ID_CHARS = 32


class DatasetIntegrityError(Exception):
    """Manifest/corpus verification failed; the run is aborted (fail-closed).

    Messages carry file basenames, field names, and digests/counts only -
    never an absolute path and never question or document text (I16).
    """


@dataclass(frozen=True)
class FileEntry:
    """One manifest-listed file: relative path + expected identity."""

    path: str  # POSIX relative, verified beneath the dataset root
    sha256: str
    size_bytes: int
    split: str | None = None  # dev/test for question files; None for documents

    @property
    def is_questions(self) -> bool:
        return self.split is not None


@dataclass(frozen=True)
class DatasetManifest:
    """Verified manifest: every listed file exists, matches, and is confined."""

    dataset_id: str
    created_at: str
    author: str
    chunk_size: int
    chunk_overlap: int
    files: tuple[FileEntry, ...]
    root: Path  # resolved dataset root (project-confined)

    @property
    def document_entries(self) -> tuple[FileEntry, ...]:
        return tuple(f for f in self.files if not f.is_questions)

    @property
    def question_entries(self) -> tuple[FileEntry, ...]:
        return tuple(f for f in self.files if f.is_questions)

    def entry_for_split(self, split: str) -> FileEntry:
        for entry in self.question_entries:
            if entry.split == split:
                return entry
        raise DatasetIntegrityError(f"no question file declared for split {split!r}")

    def manifest_digest(self) -> str:
        """Stable SHA-256 over the identity of every listed file.

        Order-sensitive over the manifest list (the manifest order is part
        of the frozen dataset's identity).
        """
        digest = hashlib.sha256()
        for entry in self.files:
            digest.update(f"{entry.path}\0{entry.sha256}\0{entry.size_bytes}".encode())
        return digest.hexdigest()


@dataclass(frozen=True)
class GoldSpan:
    """Author-declared gold answer span inside one gold chunk.

    Offsets are Unicode code points into the *chunk content* (the same
    convention the chunker and citations use). The harness verifies the
    bounds against the ingested chunk content; the span text itself is
    never stored in reports or traces.
    """

    chunk_id: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class Question:
    """One frozen question with gold labels; no evaluation logic here."""

    question_id: str
    type: str
    expected_outcome: str
    question: str
    gold_chunk_ids: tuple[str, ...]
    gold_answer: str | None = None
    gold_answer_span: GoldSpan | None = None
    structures: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuestionSet:
    """Questions for one split, loaded from a verified manifest file."""

    split: str
    file_sha256: str
    questions: tuple[Question, ...]

    def __len__(self) -> int:
        return len(self.questions)

    def __iter__(self) -> Iterator[Question]:
        return iter(self.questions)


def _reject_unsafe_relative_path(raw: str, *, context: str) -> PurePosixPath:
    """Validate a manifest path: relative, POSIX, confined, no traversal."""
    if not raw or raw.strip() != raw:
        raise DatasetIntegrityError(f"{context}: empty or padded path")
    if "\\" in raw or ":" in raw or raw.startswith("/"):
        raise DatasetIntegrityError(
            f"{context}: path must be a plain POSIX relative path"
        )
    candidate = PurePosixPath(raw)
    if candidate.is_absolute():
        raise DatasetIntegrityError(f"{context}: absolute paths are rejected")
    if ".." in candidate.parts:
        raise DatasetIntegrityError(f"{context}: path traversal is rejected")
    if candidate.parts and candidate.parts[0] == ".":
        raise DatasetIntegrityError(f"{context}: leading '.' component rejected")
    return candidate


def _resolve_beneath(root: Path, relative: PurePosixPath, *, context: str) -> Path:
    """Resolve *relative* beneath *root*, rejecting escapes and symlinks."""
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*relative.parts)
    # Walk every component: symlinks are rejected even when they would
    # stay beneath the root (the dataset must be plain files, nothing else).
    walk = resolved_root
    for part in relative.parts:
        walk = walk / part
        if walk.is_symlink():
            raise DatasetIntegrityError(f"{context}: symlinks are rejected")
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise DatasetIntegrityError(
            f"{context}: cannot resolve ({type(exc).__name__})"
        ) from exc
    if resolved != candidate and not resolved.is_relative_to(resolved_root):
        raise DatasetIntegrityError(f"{context}: path escapes the dataset root")
    if not resolved.is_relative_to(resolved_root):
        raise DatasetIntegrityError(f"{context}: path escapes the dataset root")
    return candidate


def require_beneath(root: Path, candidate: Path, *, what: str) -> Path:
    """Resolve root and candidate; fail closed unless candidate is strictly beneath root.

    Strict-below, matching ``resolve_under_project``: the root itself is
    rejected (``require_beneath(root, root)`` raises), so a candidate can
    only ever name an entry INSIDE the root, never the root directory.

    Resolving both sides here keeps drive-letter case differences from
    mattering (Z9). Returns the resolved candidate. Messages never contain
    absolute paths.
    """
    try:
        resolved_root = root.resolve()
        resolved = candidate.resolve()
    except (OSError, ValueError) as exc:
        raise DatasetIntegrityError(f"{what}: path could not be resolved") from exc
    if resolved == resolved_root or not resolved.is_relative_to(resolved_root):
        raise DatasetIntegrityError(
            f"{what}: resolved outside the {root.name} directory"
        )
    return resolved


def _sha256_file(path: Path, *, context: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 16), b""):
                digest.update(block)
                size += len(block)
    except OSError as exc:
        raise DatasetIntegrityError(
            f"{context}: cannot read file ({type(exc).__name__})"
        ) from exc
    return digest.hexdigest(), size


def load_and_verify_dataset(root: Path) -> DatasetManifest:
    """Load ``manifest.json`` under *root* and verify every listed file.

    Raises :class:`DatasetIntegrityError` on any schema, path, or content
    mismatch. Verification is complete before this returns: callers may
    trust every entry's hash, size, and confinement.
    """
    if root.is_symlink():
        raise DatasetIntegrityError("dataset root: symlinks are rejected")
    if not root.is_dir():
        raise DatasetIntegrityError("dataset root: not a directory")
    manifest_path = root / "manifest.json"
    context = "manifest.json"
    if not manifest_path.is_file():
        raise DatasetIntegrityError("manifest.json: missing")
    if manifest_path.is_symlink():
        raise DatasetIntegrityError("manifest.json: symlinks are rejected")

    try:
        raw = manifest_path.read_text(encoding="utf-8")
        document = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetIntegrityError(
            f"{context}: unreadable or malformed ({type(exc).__name__})"
        ) from exc
    if not isinstance(document, dict):
        raise DatasetIntegrityError(f"{context}: top level must be an object")

    if document.get("schema") != MANIFEST_SCHEMA:
        raise DatasetIntegrityError(f"{context}: schema must be {MANIFEST_SCHEMA!r}")
    dataset_id = document.get("dataset_id")
    if (
        not isinstance(dataset_id, str)
        or len(dataset_id) > MAX_DATASET_ID_CHARS
        or _DATASET_ID_PATTERN.fullmatch(dataset_id) is None
    ):
        raise DatasetIntegrityError(
            f"{context}: dataset_id must match {_DATASET_ID_PATTERN.pattern} "
            f"and be at most {MAX_DATASET_ID_CHARS} characters"
        )
    created_at = document.get("created_at")
    author = document.get("author")
    if not isinstance(created_at, str) or not created_at.strip():
        raise DatasetIntegrityError(f"{context}: created_at must be a non-empty string")
    if not isinstance(author, str) or not author.strip():
        raise DatasetIntegrityError(f"{context}: author must be a non-empty string")
    for field in ("chunk_size", "chunk_overlap"):
        value = document.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DatasetIntegrityError(
                f"{context}: {field} must be a non-negative int"
            )

    files_raw = document.get("files")
    if not isinstance(files_raw, list) or not files_raw:
        raise DatasetIntegrityError(f"{context}: files must be a non-empty list")

    entries: list[FileEntry] = []
    seen_paths: set[str] = set()
    splits_seen: set[str] = set()
    for index, item in enumerate(files_raw):
        item_context = f"{context}: files[{index}]"
        if not isinstance(item, dict):
            raise DatasetIntegrityError(f"{item_context}: must be an object")
        path_raw = item.get("path")
        if not isinstance(path_raw, str):
            raise DatasetIntegrityError(f"{item_context}: path must be a string")
        relative = _reject_unsafe_relative_path(path_raw, context=item_context)
        if path_raw in seen_paths:
            raise DatasetIntegrityError(f"{item_context}: duplicate path {path_raw!r}")
        seen_paths.add(path_raw)
        sha256 = item.get("sha256")
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(c not in "0123456789abcdef" for c in sha256)
        ):
            raise DatasetIntegrityError(
                f"{item_context}: sha256 must be 64 lowercase hex"
            )
        size_bytes = item.get("size_bytes")
        if (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
        ):
            raise DatasetIntegrityError(
                f"{item_context}: size_bytes must be a non-negative int"
            )
        split = item.get("split")
        if split is not None:
            if split not in SPLITS:
                raise DatasetIntegrityError(
                    f"{item_context}: split must be one of {SPLITS}"
                )
            if split in splits_seen:
                raise DatasetIntegrityError(
                    f"{item_context}: split {split!r} declared more than once"
                )
            splits_seen.add(split)
            if not relative.parts or not relative.name.endswith(".jsonl"):
                raise DatasetIntegrityError(
                    f"{item_context}: question files must end with .jsonl"
                )
        entries.append(
            FileEntry(
                path=path_raw,
                sha256=sha256,
                size_bytes=size_bytes,
                split=split,
            )
        )

    missing_splits = set(SPLITS) - splits_seen
    if missing_splits:
        raise DatasetIntegrityError(
            f"{context}: splits missing from the manifest: {sorted(missing_splits)}"
        )

    resolved_root = root.resolve()
    for entry in entries:
        file_context = f"dataset file {PurePosixPath(entry.path).name!r}"
        candidate = _resolve_beneath(
            resolved_root,
            PurePosixPath(entry.path),
            context=file_context,
        )
        if not candidate.is_file():
            raise DatasetIntegrityError(f"{file_context}: missing or not a file")
        actual_sha, actual_size = _sha256_file(candidate, context=file_context)
        if actual_size != entry.size_bytes:
            raise DatasetIntegrityError(
                f"{file_context}: size mismatch "
                f"(manifest {entry.size_bytes}, actual {actual_size})"
            )
        if actual_sha != entry.sha256:
            raise DatasetIntegrityError(
                f"{file_context}: sha256 mismatch "
                f"(manifest {entry.sha256[:12]}..., actual {actual_sha[:12]}...)"
            )

    return DatasetManifest(
        dataset_id=dataset_id,
        created_at=created_at.strip(),
        author=author.strip(),
        chunk_size=document["chunk_size"],
        chunk_overlap=document["chunk_overlap"],
        files=tuple(entries),
        root=resolved_root,
    )


def _validate_question_record(
    item: object, *, context: str, seen_ids: set[str]
) -> Question:
    if not isinstance(item, dict):
        raise DatasetIntegrityError(f"{context}: each line must be a JSON object")
    question_id = item.get("id")
    if not isinstance(question_id, str) or not question_id.strip():
        raise DatasetIntegrityError(f"{context}: id must be a non-empty string")
    if question_id in seen_ids:
        raise DatasetIntegrityError(f"{context}: duplicate question id {question_id!r}")
    q_context = f"{context} id={question_id!r}"
    q_type = item.get("type")
    if q_type not in QUESTION_TYPES:
        raise DatasetIntegrityError(
            f"{q_context}: type must be one of {sorted(QUESTION_TYPES)}"
        )
    outcome = item.get("expected_outcome")
    if outcome not in EXPECTED_OUTCOMES:
        raise DatasetIntegrityError(
            f"{q_context}: expected_outcome must be one of {sorted(EXPECTED_OUTCOMES)}"
        )
    question = item.get("question")
    if not isinstance(question, str) or not question.strip():
        raise DatasetIntegrityError(f"{q_context}: question must be non-empty text")
    if len(question) > _MAX_QUESTION_CHARS:
        raise DatasetIntegrityError(f"{q_context}: question exceeds the char bound")
    gold_raw = item.get("gold_chunk_ids")
    if not isinstance(gold_raw, list) or any(
        not isinstance(g, str) or not g.strip() for g in gold_raw
    ):
        raise DatasetIntegrityError(
            f"{q_context}: gold_chunk_ids must be a list of non-empty strings"
        )
    if len(set(gold_raw)) != len(gold_raw):
        raise DatasetIntegrityError(f"{q_context}: gold_chunk_ids contains duplicates")
    gold_answer = item.get("gold_answer")
    if gold_answer is not None and not isinstance(gold_answer, str):
        raise DatasetIntegrityError(f"{q_context}: gold_answer must be a string")
    span_raw = item.get("gold_answer_span")
    span: GoldSpan | None = None
    if span_raw is not None:
        if not isinstance(span_raw, dict):
            raise DatasetIntegrityError(
                f"{q_context}: gold_answer_span must be an object"
            )
        span_chunk = span_raw.get("chunk_id")
        span_start = span_raw.get("char_start")
        span_end = span_raw.get("char_end")
        if not isinstance(span_chunk, str) or span_chunk not in gold_raw:
            raise DatasetIntegrityError(
                f"{q_context}: gold_answer_span.chunk_id must be one of the "
                "gold chunk ids"
            )
        if (
            isinstance(span_start, bool)
            or isinstance(span_end, bool)
            or not isinstance(span_start, int)
            or not isinstance(span_end, int)
            or span_start < 0
            or span_end <= span_start
        ):
            raise DatasetIntegrityError(
                f"{q_context}: gold_answer_span offsets must satisfy "
                "0 <= char_start < char_end"
            )
        span = GoldSpan(chunk_id=span_chunk, char_start=span_start, char_end=span_end)
    # Label consistency (fail-closed on contradictions):
    if q_type == "unanswerable" and outcome != "abstain":
        raise DatasetIntegrityError(
            f"{q_context}: unanswerable questions must expect abstain"
        )
    if q_type == "unanswerable" and gold_raw:
        raise DatasetIntegrityError(
            f"{q_context}: unanswerable questions must have no gold chunks"
        )
    if outcome == "abstain" and gold_raw:
        raise DatasetIntegrityError(
            f"{q_context}: abstain-expected questions must have no gold chunks"
        )
    if outcome == "answer" and not gold_raw:
        raise DatasetIntegrityError(
            f"{q_context}: answer-expected questions must declare gold chunks"
        )
    structures = item.get("structures", [])
    if not isinstance(structures, list) or any(
        not isinstance(s, str) for s in structures
    ):
        raise DatasetIntegrityError(f"{q_context}: structures must be a string list")
    seen_ids.add(question_id)
    return Question(
        question_id=question_id.strip(),
        type=q_type,
        expected_outcome=outcome,
        question=question,
        gold_chunk_ids=tuple(gold_raw),
        gold_answer=gold_answer,
        gold_answer_span=span,
        structures=tuple(structures),
    )


def load_questions(
    manifest: DatasetManifest,
    split: Literal["dev", "test"],
    *,
    tuning: bool = False,
) -> QuestionSet:
    """Load and validate the question file for *split*.

    Split protection (plan section 9.2): when *tuning* is true, access to
    the ``test`` split is refused outright - thresholds tune on dev only.
    The refusal raises :class:`DatasetIntegrityError` before the file is
    even read, so no test label can leak into a tuning workflow.
    """
    if tuning and split == "test":
        raise DatasetIntegrityError(
            "test-split labels are not accessible in tuning mode; "
            "tune on dev only and evaluate test once per candidate config"
        )
    entry = manifest.entry_for_split(split)
    context = f"questions file for split {split!r}"
    path = manifest.root.joinpath(*PurePosixPath(entry.path).parts)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DatasetIntegrityError(
            f"{context}: unreadable ({type(exc).__name__})"
        ) from exc

    questions: list[Question] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetIntegrityError(
                f"{context}: line {line_number} is malformed JSON"
            ) from exc
        questions.append(
            _validate_question_record(
                item, context=f"{context} line {line_number}", seen_ids=seen_ids
            )
        )
    if not questions:
        raise DatasetIntegrityError(f"{context}: no questions")
    if len(questions) > _MAX_QUESTIONS_PER_FILE:
        raise DatasetIntegrityError(
            f"{context}: {len(questions)} questions exceeds the bound "
            f"of {_MAX_QUESTIONS_PER_FILE}"
        )
    return QuestionSet(
        split=split, file_sha256=entry.sha256, questions=tuple(questions)
    )


def document_paths(manifest: DatasetManifest) -> Sequence[Path]:
    """Resolved paths of manifest-listed documents only (no ambient files)."""
    return tuple(
        manifest.root.joinpath(*PurePosixPath(entry.path).parts)
        for entry in manifest.document_entries
    )
