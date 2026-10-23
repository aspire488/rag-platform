"""Dataset-id security tests (plan v2.4 sections 9.1-9.6; invariants Z8/I16).

The ``dataset_id`` flows into report filenames, run-directory names,
``run.json``, and trace request IDs, so it is validated as a strict
identifier - never stripped, never normalized - and every derived path is
containment-checked before a byte is written:

- hostile ids (traversal, drive letters, UNC, separators, control chars,
  whitespace padding, overlong, wrong charset) are rejected at manifest
  verification with :class:`DatasetIntegrityError`, before any filesystem
  path is ever constructed from them;
- valid ids pass through exactly (no mangling);
- the 32-char bound keeps the run-id's 8-hex uniqueness suffix intact, so
  same-second runs never overwrite each other;
- report files and run records are containment-checked (``require_beneath``);
- the "prove it would have escaped" tests document the legacy lexical
  collapse the fix removes - pure pathlib, zero file creation.

Offline: fixture dataset under ``data/tmp`` (D:), no network, no model.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import UTC, datetime as real_datetime
from pathlib import Path

import pytest

from rag_platform.config import PROJECT_ROOT
from rag_platform.eval import run_eval
from rag_platform.eval import harness as harness_module
from rag_platform.eval.dataset import (
    MAX_DATASET_ID_CHARS,
    DatasetIntegrityError,
    load_and_verify_dataset,
    require_beneath,
)
from rag_platform.eval.harness import RUN_ID_SANITIZE_CHARS
from rag_platform.eval.report import write_reports
from tests.eval.conftest import (
    BuiltDataset,
    load_manifest_doc,
    read_question_records,
    rewrite_question_file,
    write_manifest_doc,
)


def _set_dataset_id(root: Path, value: object) -> None:
    """Rewrite only the manifest's dataset_id field (value used as-is)."""
    document = load_manifest_doc(root)
    document["dataset_id"] = value
    write_manifest_doc(root, document)


def _names(directory: Path) -> list[str]:
    """Sorted listing of *directory*'s entry names ([] when absent)."""
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


# --- A: reject-class ids (regex-level rejection, before any FS touch) --------

_HOSTILE_IDS = [
    # traversal
    pytest.param("../evil", id="traversal-dotdot-slash"),
    pytest.param("..\\evil", id="traversal-dotdot-backslash"),
    pytest.param("..", id="traversal-bare-dotdot"),
    pytest.param("a/../b", id="traversal-embedded-slash"),
    pytest.param("a\\..\\b", id="traversal-embedded-backslash"),
    # absolute / drive / UNC
    pytest.param("D:\\x", id="abs-drive-backslash"),
    pytest.param("D:/x", id="abs-drive-slash"),
    pytest.param("C:x", id="drive-relative"),
    pytest.param("\\\\server\\share", id="unc-share"),
    pytest.param("/abs/posix", id="abs-posix"),
    # separators / controls / empty / whitespace (reject-NOT-strip rows)
    pytest.param("a/b", id="plain-slash"),
    pytest.param("a\\b", id="plain-backslash"),
    pytest.param("\x00", id="control-nul"),
    pytest.param("a\x00b", id="control-nul-embedded"),
    pytest.param("a\nb", id="control-newline"),
    pytest.param("a\tb", id="control-tab"),
    # TRAILING-newline/CR cases: "$" would match before them; \A..\Z
    # fullmatch must not (regression proof for the matcher form).
    pytest.param("a\n", id="trailing-newline"),
    pytest.param("a\r", id="trailing-cr"),
    pytest.param("a\r\n", id="trailing-crlf"),
    pytest.param("", id="empty"),
    pytest.param(" ", id="single-space"),
    pytest.param(" fixture_v1", id="leading-space-regression"),
    pytest.param("fixture_v1 ", id="trailing-space-regression"),
    # overlong / charset
    pytest.param("a" * 33, id="overlong-33"),
    pytest.param("a" * 200, id="overlong-200"),
    pytest.param("UPPER_V1", id="uppercase"),
    pytest.param("a.b", id="dot"),
    pytest.param("a b", id="inner-space"),
    pytest.param("h\u00e9llo", id="non-ascii"),
    pytest.param("-lead", id="leading-dash"),
    pytest.param("_lead", id="leading-underscore"),
    pytest.param(123, id="non-str-int"),
    pytest.param(None, id="non-str-none"),
    pytest.param(["x", "y"], id="non-str-list"),
    pytest.param(".", id="bare-dot"),
]


@pytest.mark.parametrize("hostile", _HOSTILE_IDS)
def test_hostile_dataset_ids_are_rejected_at_verification(
    eval_dataset: BuiltDataset, hostile: object
) -> None:
    # The hostile string is written only as manifest CONTENT (a JSON
    # value); no filesystem path is ever constructed from it - the
    # pytest.raises itself proves regex-level rejection happens first.
    _set_dataset_id(eval_dataset.root, hostile)
    with pytest.raises(DatasetIntegrityError, match="dataset_id"):
        load_and_verify_dataset(eval_dataset.root)


# --- B: valid ids pass through exactly ---------------------------------------

_VALID_IDS = [
    pytest.param("synthetic_v1", id="corpus-id"),
    pytest.param("fixture_v1", id="fixture-id"),
    pytest.param("a" * 30, id="length-30-now-valid"),
    pytest.param("a" * 32, id="boundary-32"),
    pytest.param("run0_9-x", id="mixed-chars"),
    pytest.param("0start", id="leading-digit"),
]


@pytest.mark.parametrize("candidate", _VALID_IDS)
def test_valid_dataset_ids_pass_through_exactly(
    eval_dataset: BuiltDataset, candidate: str
) -> None:
    _set_dataset_id(eval_dataset.root, candidate)
    manifest = load_and_verify_dataset(eval_dataset.root)
    assert manifest.dataset_id == candidate  # exact, no mangling, no strip


# --- C: hostile ids leave no artifacts at all --------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        pytest.param("../evil", id="traversal"),
        pytest.param("D:\\x", id="drive"),
        pytest.param("a" * 33, id="overlong"),
    ],
)
def test_hostile_dataset_ids_produce_no_artifacts(
    eval_dataset: BuiltDataset, hostile: str
) -> None:
    _set_dataset_id(eval_dataset.root, hostile)
    eval_runs = eval_dataset.settings.data_dir / "eval_runs"
    reports = eval_dataset.root.parent / "reports"
    eval_runs_before = _names(eval_runs)
    reports_before = _names(reports)

    with pytest.raises(DatasetIntegrityError, match="dataset_id"):
        run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)

    assert _names(eval_runs) == eval_runs_before  # no run dir, no database
    assert _names(reports) == reports_before  # no report, ever


# --- D: a max-length run stays contained everywhere --------------------------


def test_max_length_id_run_stays_contained(
    eval_dataset: BuiltDataset,
) -> None:
    _set_dataset_id(eval_dataset.root, "a" * MAX_DATASET_ID_CHARS)
    run = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)

    out = eval_dataset.root.parent / "reports"
    json_path, md_path = write_reports(run, out)
    assert json_path.is_relative_to(out.resolve())
    assert md_path.is_relative_to(out.resolve())
    assert json_path.parent == out.resolve()
    assert md_path.parent == out.resolve()
    assert json_path.suffix == ".json" and md_path.suffix == ".md"

    eval_runs = eval_dataset.settings.data_dir / "eval_runs"
    assert run.run_db_path.resolve().is_relative_to(eval_runs.resolve())
    run_record = run.run_db_path.parent / "run.json"
    assert run_record.exists()
    assert run_record.resolve().is_relative_to(eval_runs.resolve())


# --- E: the 8-hex uniqueness suffix survives the 32-char id on BOTH splits ---


@pytest.mark.parametrize(
    ("split", "id_budget"),
    [
        pytest.param("dev", 29, id="dev-segment-budget-29"),
        pytest.param("test", 28, id="test-segment-budget-28"),
    ],
)
def test_32_char_id_keeps_the_full_hex_suffix(
    eval_dataset: BuiltDataset, split: str, id_budget: int
) -> None:
    _set_dataset_id(eval_dataset.root, "a" * MAX_DATASET_ID_CHARS)
    run = run_eval(
        eval_dataset.settings,
        dataset_root=eval_dataset.root,
        split=split,  # type: ignore[arg-type]
    )
    # The dataset-id SEGMENT is truncated to the per-split budget; the
    # 8-hex uniqueness suffix always survives intact.
    assert re.fullmatch(
        rf"eval_{'a' * id_budget}_{split}_\d{{8}}T\d{{6}}Z_[0-9a-f]{{8}}",
        run.run_id,
    )
    suffix = run.run_id.rsplit("_", 1)[-1]
    assert len(suffix) == 8  # the slice never truncates the token_hex
    assert len(run.run_id) <= RUN_ID_SANITIZE_CHARS


# --- F: same-second runs never collide ---------------------------------------


def test_same_second_back_to_back_runs_never_overwrite(
    eval_dataset: BuiltDataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Freeze the stamp: both runs must share the timestamp component and
    # differ ONLY by the 8-hex token - the never-overwrite invariant.
    class FrozenDateTime:
        @classmethod
        def now(cls, tz: object = None) -> real_datetime:
            return real_datetime(2026, 6, 1, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(harness_module, "datetime", FrozenDateTime)

    run1 = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    run2 = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)

    assert run1.run_id != run2.run_id
    hex1 = run1.run_id.rsplit("_", 1)[-1]
    hex2 = run2.run_id.rsplit("_", 1)[-1]
    assert re.fullmatch(r"[0-9a-f]{8}", hex1)
    assert hex1 != hex2
    assert "20260601T120000Z" in run1.run_id  # identical stamp component
    assert "20260601T120000Z" in run2.run_id
    assert run1.run_db_path != run2.run_db_path  # distinct run directories


# --- G: overwrite protection -------------------------------------------------


def test_reports_are_never_overwritten(eval_dataset: BuiltDataset) -> None:
    run1 = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    run2 = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    out = eval_dataset.root.parent / "reports"

    json1, md1 = write_reports(run1, out)
    json1_bytes, md1_bytes = json1.read_bytes(), md1.read_bytes()
    assert json1.is_file() and md1.is_file()

    # Re-writing the SAME result stays byte-identical (same file when the
    # write lands in the same second; identical bytes either way).
    json1_again, md1_again = write_reports(run1, out)
    assert json1_again.read_bytes() == json1_bytes
    assert md1_again.read_bytes() == md1_bytes

    # A second run gets distinct filenames; the first files are untouched.
    json2, md2 = write_reports(run2, out)
    assert json2 != json1 and md2 != md1
    assert json1.read_bytes() == json1_bytes
    assert md1.read_bytes() == md1_bytes


# --- H: prove the legacy shapes WOULD have escaped (pure pathlib) ------------


def test_legacy_hostile_ids_would_have_escaped_the_directories() -> None:
    # Zero file creation: both target directories stay non-existent and
    # the hostile names are only joined and resolved lexically.
    out = PROJECT_ROOT / "data" / "tmp" / "never_created_reports"
    hostile = "../" * 4 + "x"
    target = (out / f"eval_{hostile}_dev_20260101T000000Z_deadbeef.json").resolve()
    assert not target.is_relative_to(out.resolve())  # WOULD have escaped
    assert not target.exists()  # and nothing was written there

    eval_runs = PROJECT_ROOT / "data" / "tmp" / "never_created_eval_runs"
    run_dir = (
        eval_runs / f"eval_..\\..\\..\\x_dev_20260101T000000Z_deadbeef"
    ).resolve()
    assert not run_dir.is_relative_to(eval_runs.resolve())  # WOULD have escaped
    assert not run_dir.exists()


# --- I: gold-mismatch ordering (honest current on-disk state) ---------------


def test_gold_mismatch_leaves_no_run_record_and_no_report(
    eval_dataset: BuiltDataset,
) -> None:
    records = read_question_records(eval_dataset.root, "dev")
    answer = next(r for r in records if r["id"] == "q0001")
    answer["gold_chunk_ids"] = ["0" * 64]
    answer.pop("gold_answer_span", None)
    rewrite_question_file(eval_dataset.root, "dev", records)

    eval_runs = eval_dataset.settings.data_dir / "eval_runs"
    reports = eval_dataset.root.parent / "reports"
    before = _names(eval_runs)

    with pytest.raises(DatasetIntegrityError, match="not present in the ingested"):
        run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)

    added = [name for name in _names(eval_runs) if name not in before]
    # Honest current behavior, asserted as-is (never cleaned up): the run
    # directory with its corpus.db IS left behind - the database is
    # created before gold verification - but the durable run record is
    # NOT, so the prior-test-split counter (which counts run.json files)
    # is unaffected by an aborted run.
    assert len(added) == 1
    leftover = eval_runs / added[0]
    assert (leftover / "corpus.db").is_file()
    assert not (leftover / "run.json").exists()
    # And no report was ever attempted for the aborted run.
    assert not reports.exists()
    assert _names(reports) == []


# --- J: defense-in-depth (verifier findings 4a + 9) ---------------------------
# Helper semantics pinned directly, and the containment property locked
# WITHOUT the manifest validator in the loop: a hostile dataset_id forced
# through write_reports is still confined (every sink holds alone).


def test_require_beneath_rejects_the_root_itself(storage_tmp: Path) -> None:
    """Strict-below: the root itself can never name an entry inside itself."""
    root = storage_tmp / "reports"
    with pytest.raises(DatasetIntegrityError, match="outside the reports"):
        require_beneath(root, root, what="report file")


def test_require_beneath_case_drift_stays_contained(storage_tmp: Path) -> None:
    """Both sides resolve in one place: prefix case cannot defeat containment."""
    root = storage_tmp / "reports"
    candidate = root / "eval_x.json"
    drifted = Path(str(candidate).swapcase())
    resolved = require_beneath(root, drifted, what="report file")
    assert resolved == candidate.resolve()  # WindowsPath equality is case-folded
    assert resolved.is_relative_to(root.resolve())
    assert not resolved.exists()  # zero file creation


def test_require_beneath_messages_carry_no_absolute_paths(
    storage_tmp: Path,
) -> None:
    """I16: rejection messages name the label and directory, never a path."""
    root = storage_tmp / "reports"
    escape = storage_tmp / "outside" / ".." / "escaped.json"
    with pytest.raises(DatasetIntegrityError) as excinfo:
        require_beneath(root, escape, what="report file")
    message = str(excinfo.value)
    assert "report file" in message
    assert str(root.resolve()) not in message
    assert str(escape.resolve()) not in message
    assert str(PROJECT_ROOT) not in message


def test_write_reports_hostile_stem_bypass_is_contained(
    eval_dataset: BuiltDataset, storage_tmp: Path
) -> None:
    """Invariant B without the validator: even a write_reports caller that
    hands over a traversal-shaped dataset_id creates NOTHING - the checks
    now precede mkdir, so the reports dir itself is not even created.

    Four ``..`` tokens are needed because the stem glues ``eval_`` onto
    the first one (``eval_..`` is a single component, not a climb); the
    premise assertion below locks that the chosen shape really escapes.
    """
    run = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    hostile_id = "../../../../evil"
    hostile = dataclasses.replace(run, dataset_id=hostile_id)
    out = storage_tmp / "bypass_reports"
    # Premise lock: this shape WOULD escape the reports dir if unchecked
    # (pure pathlib, zero file creation).
    probe = (out / f"eval_{hostile_id}_dev_20260101T000000Z_deadbeef.json").resolve()
    assert not probe.is_relative_to(out.resolve())
    assert not probe.exists()
    assert not out.exists()
    with pytest.raises(DatasetIntegrityError, match="report file"):
        write_reports(hostile, out)
    assert not out.exists()
    assert _names(out) == []
