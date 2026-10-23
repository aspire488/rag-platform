"""Frozen-dataset verification tests (plan v2.4 sections 9.2, 9.5).

Covers fail-closed manifest verification (schema, per-file SHA-256 +
size, path confinement, traversal/absolute/symlink/duplicate rejection),
question-label rules, gold-span structure, and the tuning-mode refusal
that must fire BEFORE the test-split file is even parsed.

Offline only: everything happens under ``data/tmp`` (D:); no network, no
model, no provider.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rag_platform.eval.dataset import (
    MANIFEST_SCHEMA,
    DatasetIntegrityError,
    GoldSpan,
    document_paths,
    load_and_verify_dataset,
    load_questions,
)
from tests.eval.conftest import BuiltDataset


def _manifest_path(root: Path) -> Path:
    return root / "manifest.json"


def _load_manifest_doc(root: Path) -> dict[str, object]:
    return json.loads(_manifest_path(root).read_text(encoding="utf-8"))


def _write_manifest_doc(root: Path, document: dict[str, object]) -> None:
    _manifest_path(root).write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _rewrite_question_file(
    root: Path, split: str, records: list[dict[str, object]]
) -> None:
    """Replace a question file AND its manifest entry so verification still
    passes - isolating question-record validation from hash validation."""
    path = root / "questions" / f"questions_{split}.jsonl"
    path.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in records),
        encoding="utf-8",
    )
    document = _load_manifest_doc(root)
    files = document["files"]
    assert isinstance(files, list)
    rel = f"questions/questions_{split}.jsonl"
    for entry in files:
        assert isinstance(entry, dict)
        if entry["path"] == rel:
            raw = path.read_bytes()
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
            entry["size_bytes"] = len(raw)
    _write_manifest_doc(root, document)


def _dev_records(root: Path) -> list[dict[str, object]]:
    path = root / "questions" / "questions_dev.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --- manifest verification ---------------------------------------------------


def test_valid_dataset_verifies_and_is_confined(eval_dataset: BuiltDataset) -> None:
    manifest = load_and_verify_dataset(eval_dataset.root)
    assert manifest.dataset_id == "fixture_v1"
    assert manifest.chunk_size == eval_dataset.settings.chunk_size
    assert len(manifest.document_entries) == 2
    assert {e.split for e in manifest.question_entries} == {"dev", "test"}
    for path in document_paths(manifest):
        assert path.is_file()
        assert path.resolve().is_relative_to(eval_dataset.root.resolve())
    digest = manifest.manifest_digest()
    assert len(digest) == 64 and int(digest, 16) >= 0


def test_tampered_document_is_rejected_before_any_run(
    eval_dataset: BuiltDataset,
) -> None:
    target = eval_dataset.root / "documents" / "orchard.txt"
    raw = bytearray(target.read_bytes())
    raw[10] ^= 0x41
    target.write_bytes(bytes(raw))
    with pytest.raises(DatasetIntegrityError, match="sha256 mismatch"):
        load_and_verify_dataset(eval_dataset.root)


def test_missing_manifest_is_rejected(eval_dataset: BuiltDataset) -> None:
    _manifest_path(eval_dataset.root).unlink()
    with pytest.raises(DatasetIntegrityError, match="manifest.json: missing"):
        load_and_verify_dataset(eval_dataset.root)


def test_wrong_schema_is_rejected(eval_dataset: BuiltDataset) -> None:
    document = _load_manifest_doc(eval_dataset.root)
    document["schema"] = "some-other-schema/9"
    _write_manifest_doc(eval_dataset.root, document)
    with pytest.raises(DatasetIntegrityError, match="schema must be"):
        load_and_verify_dataset(eval_dataset.root)


@pytest.mark.parametrize(
    "bad_path",
    [
        "../outside.txt",  # traversal
        "documents/../../escape.txt",  # traversal below a valid dir
        "/etc/passwd",  # absolute
        "documents\\evil.txt",  # backslash (not POSIX)
        "C:/windows/system.ini",  # drive colon
        "./documents/x.txt",  # leading '.' component
    ],
)
def test_unsafe_manifest_paths_are_rejected(
    eval_dataset: BuiltDataset, bad_path: str
) -> None:
    document = _load_manifest_doc(eval_dataset.root)
    files = document["files"]
    assert isinstance(files, list)
    assert isinstance(files[0], dict)
    files[0]["path"] = bad_path  # reuse an entry with a valid hash/size
    _write_manifest_doc(eval_dataset.root, document)
    with pytest.raises(DatasetIntegrityError):
        load_and_verify_dataset(eval_dataset.root)


def test_duplicate_manifest_paths_are_rejected(eval_dataset: BuiltDataset) -> None:
    document = _load_manifest_doc(eval_dataset.root)
    files = document["files"]
    assert isinstance(files, list) and isinstance(files[0], dict)
    files.append(dict(files[0]))
    _write_manifest_doc(eval_dataset.root, document)
    with pytest.raises(DatasetIntegrityError, match="duplicate path"):
        load_and_verify_dataset(eval_dataset.root)


def test_missing_split_declaration_is_rejected(eval_dataset: BuiltDataset) -> None:
    document = _load_manifest_doc(eval_dataset.root)
    files = document["files"]
    assert isinstance(files, list)
    document["files"] = [
        entry
        for entry in files
        if not (isinstance(entry, dict) and entry.get("split") == "test")
    ]
    _write_manifest_doc(eval_dataset.root, document)
    with pytest.raises(DatasetIntegrityError, match="splits missing"):
        load_and_verify_dataset(eval_dataset.root)


def test_symlinked_dataset_file_is_rejected(eval_dataset: BuiltDataset) -> None:
    target = eval_dataset.root / "documents" / "orchard.txt"
    link = eval_dataset.root / "documents" / "orchard_link.txt"
    document = _load_manifest_doc(eval_dataset.root)
    files = document["files"]
    assert isinstance(files, list) and isinstance(files[0], dict)
    # point the manifest at a path we are about to replace with a symlink,
    # reusing the hash/size of the file it links to (same content)
    files[0]["path"] = "documents/orchard_link.txt"
    _write_manifest_doc(eval_dataset.root, document)
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:  # privilege not held (Windows)
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(DatasetIntegrityError, match="symlinks are rejected"):
        load_and_verify_dataset(eval_dataset.root)


def test_tampered_question_file_is_rejected(eval_dataset: BuiltDataset) -> None:
    path = eval_dataset.root / "questions" / "questions_dev.jsonl"
    raw = bytearray(path.read_bytes())
    raw[2] ^= 0x20
    path.write_bytes(bytes(raw))
    with pytest.raises(DatasetIntegrityError, match="sha256 mismatch"):
        load_and_verify_dataset(eval_dataset.root)


# --- question record validation ---------------------------------------------


def _expect_question_error(eval_dataset: BuiltDataset, mutate, match: str) -> None:  # noqa: ANN001
    records = _dev_records(eval_dataset.root)
    mutate(records)
    _rewrite_question_file(eval_dataset.root, "dev", records)
    manifest = load_and_verify_dataset(eval_dataset.root)  # hashes still valid
    with pytest.raises(DatasetIntegrityError, match=match):
        load_questions(manifest, "dev")


def test_duplicate_question_ids_are_rejected(eval_dataset: BuiltDataset) -> None:
    def mutate(records: list[dict[str, object]]) -> None:
        records.append(dict(records[0]))

    _expect_question_error(eval_dataset, mutate, "duplicate question id")


def test_invalid_question_type_is_rejected(eval_dataset: BuiltDataset) -> None:
    def mutate(records: list[dict[str, object]]) -> None:
        records[0]["type"] = "free-form"

    _expect_question_error(eval_dataset, mutate, "type must be one of")


def test_invalid_expected_outcome_is_rejected(eval_dataset: BuiltDataset) -> None:
    def mutate(records: list[dict[str, object]]) -> None:
        records[0]["expected_outcome"] = "maybe"

    _expect_question_error(eval_dataset, mutate, "expected_outcome must be one of")


def test_unanswerable_with_gold_chunks_is_rejected(
    eval_dataset: BuiltDataset,
) -> None:
    def mutate(records: list[dict[str, object]]) -> None:
        unanswerable = next(r for r in records if r["type"] == "unanswerable")
        unanswerable["gold_chunk_ids"] = ["some_chunk"]

    _expect_question_error(
        eval_dataset, mutate, "unanswerable questions must have no gold chunks"
    )


def test_abstain_with_gold_chunks_is_rejected(eval_dataset: BuiltDataset) -> None:
    def mutate(records: list[dict[str, object]]) -> None:
        # an answer-labeled record relabeled abstain while keeping its gold
        answer = next(
            r
            for r in records
            if r["expected_outcome"] == "answer" and r["type"] != "unanswerable"
        )
        answer["expected_outcome"] = "abstain"

    _expect_question_error(
        eval_dataset, mutate, "abstain-expected questions must have no gold chunks"
    )


def test_answer_without_gold_chunks_is_rejected(eval_dataset: BuiltDataset) -> None:
    def mutate(records: list[dict[str, object]]) -> None:
        answer = next(r for r in records if r["expected_outcome"] == "answer")
        answer["gold_chunk_ids"] = []
        answer.pop("gold_answer_span", None)

    _expect_question_error(
        eval_dataset, mutate, "answer-expected questions must declare gold chunks"
    )


def test_gold_span_must_reference_a_gold_chunk(eval_dataset: BuiltDataset) -> None:
    def mutate(records: list[dict[str, object]]) -> None:
        answer = next(r for r in records if r["expected_outcome"] == "answer")
        span = answer["gold_answer_span"]
        assert isinstance(span, dict)
        span["chunk_id"] = "not_a_gold_chunk"

    _expect_question_error(
        eval_dataset, mutate, "gold_answer_span.chunk_id must be one of"
    )


@pytest.mark.parametrize(
    ("start", "end"),
    [(-1, 4), (0, 0), (5, 2), ("0", 4), (0, True)],  # bool is not an offset
)
def test_gold_span_offsets_must_be_sane(
    eval_dataset: BuiltDataset, start: object, end: object
) -> None:
    def mutate(records: list[dict[str, object]]) -> None:
        answer = next(r for r in records if r["expected_outcome"] == "answer")
        span = answer["gold_answer_span"]
        assert isinstance(span, dict)
        span["char_start"] = start
        span["char_end"] = end

    _expect_question_error(eval_dataset, mutate, "char_start < char_end")


def test_overlong_question_is_rejected(eval_dataset: BuiltDataset) -> None:
    def mutate(records: list[dict[str, object]]) -> None:
        records[0]["question"] = "x" * 2001

    _expect_question_error(eval_dataset, mutate, "exceeds the char bound")


def test_question_file_entry_must_be_jsonl(eval_dataset: BuiltDataset) -> None:
    document = _load_manifest_doc(eval_dataset.root)
    files = document["files"]
    assert isinstance(files, list)
    for entry in files:
        if isinstance(entry, dict) and entry.get("split") == "dev":
            entry["path"] = "questions/questions_dev.json"  # not .jsonl
    _write_manifest_doc(eval_dataset.root, document)
    with pytest.raises(DatasetIntegrityError, match="must end with .jsonl"):
        load_and_verify_dataset(eval_dataset.root)


# --- gold span + splits ------------------------------------------------------


def test_valid_questions_load_with_typed_gold_labels(
    eval_dataset: BuiltDataset,
) -> None:
    manifest = load_and_verify_dataset(eval_dataset.root)
    dev = load_questions(manifest, "dev")
    assert dev.split == "dev"
    assert dev.file_sha256 == manifest.entry_for_split("dev").sha256
    assert len(dev) == 4
    by_id = {q.question_id: q for q in dev}
    assert set(by_id) == {"q0001", "q0002", "q0003", "q0004"}
    multi = by_id["q0002"]
    assert multi.type == "multi-hop"
    assert len(multi.gold_chunk_ids) == 2
    assert isinstance(multi.gold_answer_span, GoldSpan)
    span = multi.gold_answer_span
    assert span is not None and 0 <= span.char_start < span.char_end
    assert span.chunk_id in multi.gold_chunk_ids
    # gold span must locate the declared answer inside the gold chunk
    assert eval_dataset.gold["q0002"] == multi.gold_chunk_ids
    assert by_id["q0003"].gold_chunk_ids == ()
    assert by_id["q0003"].expected_outcome == "abstain"


def test_tuning_mode_refuses_test_labels_before_parsing(
    eval_dataset: BuiltDataset,
) -> None:
    manifest = load_and_verify_dataset(eval_dataset.root)
    # Remove the test file itself: if the refusal happens before any read,
    # tuning mode still raises the tuning refusal, while non-tuning mode
    # proves the file would have been read (unreadable).
    (eval_dataset.root / "questions" / "questions_test.jsonl").unlink()
    with pytest.raises(DatasetIntegrityError, match="tuning mode"):
        load_questions(manifest, "test", tuning=True)
    with pytest.raises(DatasetIntegrityError, match="unreadable"):
        load_questions(manifest, "test", tuning=False)


def test_dev_split_is_available_in_tuning_mode(eval_dataset: BuiltDataset) -> None:
    manifest = load_and_verify_dataset(eval_dataset.root)
    dev = load_questions(manifest, "dev", tuning=True)
    assert len(dev) == 4


def test_manifest_digest_is_order_sensitive(eval_dataset: BuiltDataset) -> None:
    manifest = load_and_verify_dataset(eval_dataset.root)
    before = manifest.manifest_digest()
    document = _load_manifest_doc(eval_dataset.root)
    files = document["files"]
    assert isinstance(files, list)
    files.reverse()  # same identity, different manifest order
    _write_manifest_doc(eval_dataset.root, document)
    after = load_and_verify_dataset(eval_dataset.root).manifest_digest()
    assert before != after


def test_manifest_digest_changes_with_content_identity(
    eval_dataset: BuiltDataset,
) -> None:
    document = _load_manifest_doc(eval_dataset.root)
    files = document["files"]
    assert isinstance(files, list) and isinstance(files[0], dict)
    files[0]["sha256"] = "0" * 64  # identity change; verification would fail,
    # but digest() is computed from the parsed list - so digests must differ.
    # We bypass verification on purpose: digest semantics are unit-level.
    from rag_platform.eval.dataset import DatasetManifest, FileEntry

    entries = tuple(
        FileEntry(
            path=str(e["path"]),
            sha256=str(e["sha256"]),
            size_bytes=int(e["size_bytes"]),
        )  # type: ignore[index]
        for e in files
    )
    base = DatasetManifest(
        dataset_id="fixture_v1",
        created_at="2026-01-01T00:00:00Z",
        author="test-suite",
        chunk_size=1000,
        chunk_overlap=150,
        files=entries,
        root=eval_dataset.root.resolve(),
    )
    assert base.manifest_digest() != (
        load_and_verify_dataset(eval_dataset.root).manifest_digest()
    )


def test_schema_constant_is_frozen() -> None:
    assert MANIFEST_SCHEMA == "rag-platform-eval-manifest/1"
