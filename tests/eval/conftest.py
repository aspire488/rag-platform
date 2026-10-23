"""Shared fixtures for the Phase 8 evaluation harness tests (plan v2.4
sections 9.1-9.5, 15 Phase 8 row).

Everything is offline and deterministic: a tiny locally-authored fixture
dataset is built under ``data/tmp`` (D:), gold labels are computed by
running the PRODUCTION loaders/chunker/id helpers (never reimplemented),
and the harness settings keep every artifact inside ``storage_tmp``.

No test in this package opens a socket, loads a model, contacts a
provider, or touches the real frozen corpus for mutation - the frozen
corpus appears only read-only in ``test_frozen_corpus.py``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from rag_platform.config import Settings
from rag_platform.eval.dataset import MANIFEST_SCHEMA, load_and_verify_dataset
from rag_platform.ingestion.chunker import chunk_text
from rag_platform.ingestion.loaders import load_document
from rag_platform.ingestion.store import chunk_id, document_id


@dataclass(frozen=True)
class BuiltDataset:
    """A tiny manifest-valid dataset plus the settings to run it."""

    root: Path
    settings: Settings  # generation-disabled; data_dir confined to storage_tmp
    gold: dict[str, tuple[str, ...]]  # question id -> declared gold chunk ids


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def build_dataset(root: Path, *, settings: Settings) -> BuiltDataset:
    """Build a manifest-valid two-document fixture dataset under *root*.

    The fixture exercises both abstention paths the M1 configuration can
    reach (lexical ``no_candidates`` for fully-absent vocabulary, and
    ``generation_unavailable`` for in-domain-but-unanswerable queries) as
    well as single-hop, multi-hop, and answer-labeled questions.
    """
    docs_dir = root / "documents"
    questions_dir = root / "questions"
    docs_dir.mkdir(parents=True)
    questions_dir.mkdir(parents=True)

    docs: dict[str, str] = {
        "orchard.txt": (
            "The orchard report for the Willow Creek estate records that the "
            "apple harvest yield is 42 bushels. The pear yield is 12 bushels. "
            "This document is the authoritative source for orchard yields."
        ),
        "warehouse.txt": (
            "The warehouse log for the Bayview depot records that the banana "
            "shipment count is 17 crates. The cherry shipment count is 9 "
            "crates. This document is the authoritative source for shipments."
        ),
    }
    chunk_ids: dict[str, str] = {}
    chunk_contents: dict[str, str] = {}
    doc_entries: list[dict[str, object]] = []
    for name, text in docs.items():
        path = docs_dir / name
        path.write_text(text, encoding="utf-8")
        loaded = load_document(path, max_bytes=settings.max_input_file_bytes)
        chunks = chunk_text(
            loaded.text, size=settings.chunk_size, overlap=settings.chunk_overlap
        )
        assert len(chunks) == 1, "fixture docs must fit a single chunk"
        doc_id = document_id(loaded.source_rel_path, loaded.content_sha256)
        chunk_ids[name] = chunk_id(doc_id, chunks[0].ordinal)
        chunk_contents[name] = chunks[0].content
        doc_entries.append(
            {
                "path": f"documents/{name}",
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )

    def answer_item(
        qid: str,
        qtype: str,
        question: str,
        doc_name: str,
        answer: str,
        structures: list[str],
    ) -> dict[str, object]:
        cid = chunk_ids[doc_name]
        content = chunk_contents[doc_name]
        start = content.index(answer)
        return {
            "id": qid,
            "type": qtype,
            "expected_outcome": "answer",
            "question": question,
            "gold_chunk_ids": [cid],
            "gold_answer": answer,
            "gold_answer_span": {
                "chunk_id": cid,
                "char_start": start,
                "char_end": start + len(answer),
            },
            "structures": structures,
        }

    orchard_cid = chunk_ids["orchard.txt"]
    warehouse_cid = chunk_ids["warehouse.txt"]
    warehouse_content = chunk_contents["warehouse.txt"]
    span_start = warehouse_content.index("17 crates")

    dev_questions: list[dict[str, object]] = [
        answer_item(
            "q0001",
            "single-hop",
            "What is the apple harvest yield at Willow Creek?",
            "orchard.txt",
            "42 bushels",
            ["answerable"],
        ),
        {
            # Gold spans both fixture documents (multi-hop mechanics).
            "id": "q0002",
            "type": "multi-hop",
            "expected_outcome": "answer",
            "question": "What are the orchard yield and warehouse shipment figures?",
            "gold_chunk_ids": [orchard_cid, warehouse_cid],
            "gold_answer": "17 crates",
            "gold_answer_span": {
                "chunk_id": warehouse_cid,
                "char_start": span_start,
                "char_end": span_start + len("17 crates"),
            },
            "structures": ["multi_hop_chain"],
        },
        {
            # Every token is absent from the corpus -> lexical no_candidates.
            "id": "q0003",
            "type": "unanswerable",
            "expected_outcome": "abstain",
            "question": "Zzqxw rpqtz vjkm blarn?",
            "gold_chunk_ids": [],
            "structures": ["unanswerable"],
        },
        {
            # In-domain vocabulary -> candidates exist; generation disabled,
            # so the honest outcome is generation_unavailable (not a correct
            # abstention).
            "id": "q0004",
            "type": "unanswerable",
            "expected_outcome": "abstain",
            "question": "What is the mango harvest yield at Willow Creek?",
            "gold_chunk_ids": [],
            "structures": ["unanswerable"],
        },
    ]
    test_questions: list[dict[str, object]] = [
        answer_item(
            "q0005",
            "single-hop",
            "What is the banana shipment count at Bayview?",
            "warehouse.txt",
            "17 crates",
            ["answerable"],
        ),
        {
            "id": "q0006",
            "type": "unanswerable",
            "expected_outcome": "abstain",
            "question": "Fgqtz wibble zorb plix?",
            "gold_chunk_ids": [],
            "structures": ["unanswerable"],
        },
    ]

    dev_path = questions_dir / "questions_dev.jsonl"
    test_path = questions_dir / "questions_test.jsonl"
    _write_jsonl(dev_path, dev_questions)
    _write_jsonl(test_path, test_questions)

    files = doc_entries + [
        {
            "path": "questions/questions_dev.jsonl",
            "sha256": _sha256(dev_path),
            "size_bytes": dev_path.stat().st_size,
            "split": "dev",
        },
        {
            "path": "questions/questions_test.jsonl",
            "sha256": _sha256(test_path),
            "size_bytes": test_path.stat().st_size,
            "split": "test",
        },
    ]
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "dataset_id": "fixture_v1",
        "created_at": "2026-01-01T00:00:00Z",
        "author": "test-suite",
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "files": files,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # The fixture must satisfy exactly the loader the harness uses.
    load_and_verify_dataset(root)

    gold = {
        str(question["id"]): tuple(question["gold_chunk_ids"])  # type: ignore[arg-type]
        for question in dev_questions + test_questions
    }
    return BuiltDataset(root=root, settings=settings, gold=gold)


@pytest.fixture
def eval_dataset(storage_tmp: Path) -> BuiltDataset:
    """Manifest-valid fixture dataset with harness-ready settings."""
    settings = Settings(
        _env_file=None, data_dir=storage_tmp / "data"
    )  # eval_runs stay inside storage_tmp
    return build_dataset(storage_tmp / "dataset", settings=settings)


# --- manifest/question mutation helpers (used by several test modules) ------


def load_manifest_doc(root: Path) -> dict[str, object]:
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def write_manifest_doc(root: Path, document: dict[str, object]) -> None:
    (root / "manifest.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def read_question_records(root: Path, split: str) -> list[dict[str, object]]:
    path = root / "questions" / f"questions_{split}.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def rewrite_question_file(
    root: Path, split: str, records: list[dict[str, object]]
) -> None:
    """Replace a question file AND its manifest hash/size so manifest
    verification still passes (isolates record-level validation)."""
    path = root / "questions" / f"questions_{split}.jsonl"
    path.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in records),
        encoding="utf-8",
    )
    document = load_manifest_doc(root)
    files = document["files"]
    assert isinstance(files, list)
    rel = f"questions/questions_{split}.jsonl"
    for entry in files:
        assert isinstance(entry, dict)
        if entry["path"] == rel:
            raw = path.read_bytes()
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
            entry["size_bytes"] = len(raw)
    write_manifest_doc(root, document)
