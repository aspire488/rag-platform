"""Frozen synthetic corpus verification (plan v2.4 sections 9.1, 15 Phase 8).

Read-only checks against the locally-authored corpus under
``corpora/synthetic_v1``:

- the manifest verifies fully (schema, per-file SHA-256 + size, path
  confinement, both splits declared) and verification is deterministic
  across invocations (stable digest - a fresh process sees the same
  frozen identity);
- verification touches nothing: a full byte-snapshot before/after proves
  the corpus stays read-only;
- the corpus shape matches the plan's Phase 8 criteria: ~24 documents
  in the 50-150 KB band across TXT/MD/PDF, 114 questions (dev 70 /
  test 44), ~40 multi-hop, every question type in both splits, unique
  IDs, label rules;
- split protection applies to the frozen corpus too (tuning refuses the
  test file before it is parsed).

No test here writes, ingests, indexes, or runs queries - the corpus is
only read. Offline: no network, no model, no provider.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from rag_platform.config import PROJECT_ROOT
from rag_platform.eval.dataset import (
    EXPECTED_OUTCOMES,
    MANIFEST_SCHEMA,
    QUESTION_TYPES,
    SPLITS,
    DatasetIntegrityError,
    document_paths,
    load_and_verify_dataset,
    load_questions,
)

CORPUS_ROOT = PROJECT_ROOT / "corpora" / "synthetic_v1"

# Plan Phase 8 corpus scale (kept honest - never silently shrunk).
EXPECTED_DOCUMENTS = 24
EXPECTED_DEV_QUESTIONS = 70
EXPECTED_TEST_QUESTIONS = 44
MIN_DOC_BYTES = 50 * 1024  # 50 KB band floor
MAX_DOC_BYTES = 150 * 1024  # 150 KB band ceiling
EXPECTED_FORMATS = {".txt": 10, ".md": 8, ".pdf": 6}


def _snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _question_records(split: str) -> list[dict[str, object]]:
    path = CORPUS_ROOT / "questions" / f"questions_{split}.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_frozen_corpus_manifest_verifies_and_is_deterministic() -> None:
    # A missing corpus is a failed artifact, not a skipped check.
    assert CORPUS_ROOT.is_dir(), "frozen corpus corpora/synthetic_v1 is missing"

    raw_manifest = json.loads(
        (CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8")
    )
    assert raw_manifest["schema"] == MANIFEST_SCHEMA
    manifest = load_and_verify_dataset(CORPUS_ROOT)
    assert manifest.dataset_id == "synthetic_v1"
    assert len(manifest.document_entries) == EXPECTED_DOCUMENTS
    assert {entry.split for entry in manifest.question_entries} == set(SPLITS)
    assert len(manifest.manifest_digest()) == 64

    # Restart persistence: a second, independent verification yields the
    # same frozen identity (same bytes -> same digest), and both question
    # files load with the hashes recorded in the manifest.
    again = load_and_verify_dataset(CORPUS_ROOT)
    assert again.manifest_digest() == manifest.manifest_digest()
    dev = load_questions(again, "dev")
    assert dev.file_sha256 == again.entry_for_split("dev").sha256
    assert (
        load_questions(again, "test").file_sha256
        == again.entry_for_split("test").sha256
    )


def test_frozen_corpus_verification_is_read_only() -> None:
    before = _snapshot(CORPUS_ROOT)
    manifest = load_and_verify_dataset(CORPUS_ROOT)
    load_questions(manifest, "dev")
    load_questions(manifest, "test")
    after = _snapshot(CORPUS_ROOT)
    assert after == before  # no writes, no additions, no deletions


def test_frozen_corpus_documents_meet_the_scale_band() -> None:
    manifest = load_and_verify_dataset(CORPUS_ROOT)
    sizes = [entry.size_bytes for entry in manifest.document_entries]
    assert len(sizes) == EXPECTED_DOCUMENTS
    for size in sizes:
        assert MIN_DOC_BYTES <= size <= MAX_DOC_BYTES, (
            f"document size {size} outside the 50-150 KB band"
        )

    formats = Counter(
        Path(entry.path).suffix.lower() for entry in manifest.document_entries
    )
    assert dict(formats) == EXPECTED_FORMATS

    for path in document_paths(manifest):
        assert path.is_file()
        assert path.resolve().is_relative_to(CORPUS_ROOT.resolve())
        assert not path.is_symlink()


def test_frozen_corpus_question_sets_are_complete_and_unique() -> None:
    manifest = load_and_verify_dataset(CORPUS_ROOT)
    dev = load_questions(manifest, "dev")
    test = load_questions(manifest, "test")
    assert len(dev) == EXPECTED_DEV_QUESTIONS
    assert len(test) == EXPECTED_TEST_QUESTIONS
    assert len(dev) + len(test) == 114

    # Unique IDs within each split; no overlap across splits.
    dev_ids = [q.question_id for q in dev]
    test_ids = [q.question_id for q in test]
    assert len(set(dev_ids)) == len(dev_ids)
    assert len(set(test_ids)) == len(test_ids)
    assert not set(dev_ids) & set(test_ids)

    # Every question type appears in BOTH splits (plan Phase 8 criteria).
    for question_set in (dev, test):
        types = {q.type for q in question_set}
        assert types == set(QUESTION_TYPES)
        outcomes = {q.expected_outcome for q in question_set}
        assert outcomes <= set(EXPECTED_OUTCOMES)

    all_questions = [*dev, *test]
    multi_hop = [q for q in all_questions if q.type == "multi-hop"]
    assert len(multi_hop) == 40  # the ~40 multi-hop subset, never shrunk
    assert all(len(q.gold_chunk_ids) >= 2 for q in multi_hop)

    # Label rules hold corpus-wide: abstain/unanswerable carry no gold,
    # answer-labeled questions carry gold + an answer.
    unanswerable = [q for q in all_questions if q.type == "unanswerable"]
    assert len(unanswerable) == 16
    for question in unanswerable:
        assert question.expected_outcome == "abstain"
        assert question.gold_chunk_ids == ()
    for question in all_questions:
        if question.expected_outcome == "answer":
            assert question.gold_chunk_ids
            assert question.gold_answer
        if question.expected_outcome == "abstain":
            assert question.gold_chunk_ids == ()


def test_frozen_corpus_gold_ids_are_well_formed_hex() -> None:
    manifest = load_and_verify_dataset(CORPUS_ROOT)
    for split in SPLITS:
        for question in load_questions(manifest, split):
            for chunk in question.gold_chunk_ids:
                assert re.fullmatch(r"[0-9a-f]{64}", chunk), (
                    f"{question.question_id}: malformed gold chunk id"
                )


def test_frozen_corpus_tuning_refuses_the_test_split() -> None:
    manifest = load_and_verify_dataset(CORPUS_ROOT)
    with pytest.raises(DatasetIntegrityError, match="tuning mode"):
        load_questions(manifest, "test", tuning=True)
    # dev remains available in tuning mode (thresholds tune on dev only).
    assert len(load_questions(manifest, "dev", tuning=True)) == EXPECTED_DEV_QUESTIONS
