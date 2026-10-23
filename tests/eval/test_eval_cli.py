"""CLI ``eval`` and ``ingest`` subcommand tests (plan v2.4 section 11).

Proves the documented exit-code contract end-to-end, in-process:

- ``0`` clean run (dev split, report written, bounded summary);
- ``2`` invalid input (tuning+test, missing dataset, top-k out of range,
  ``--out`` outside the project, dataset outside the project, usage);
- ``3`` integrity failure (tampered manifest-listed file);
- ``4`` run completed but per-query errors were recorded (report written).

Also proves the CLI never dumps queries by default, never activates a
provider implicitly, and ``ingest`` touches only manifest-listed files.

Offline: fixture dataset under ``data/tmp`` (D:), injected settings,
monkeypatched typed failures only - no server, no model, no network.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest

from rag_platform import cli as cli_module
from rag_platform.api import default_db_path
from rag_platform.config import PROJECT_ROOT, Settings
from rag_platform.eval import harness as harness_module
from rag_platform.retrieval import QueryValidationError
from tests.eval.conftest import (
    BuiltDataset,
    load_manifest_doc,
    read_question_records,
    rewrite_question_file,
    write_manifest_doc,
)


def _run_cli(argv: list[str], settings: Settings) -> tuple[int, str]:
    stdout = io.StringIO()
    code = cli_module.main(argv, settings=settings, stdout=stdout)
    return code, stdout.getvalue()


def _eval_argv(dataset: Path, out: Path, *extra: str) -> list[str]:
    return ["eval", "--dataset", str(dataset), "--out", str(out), *extra]


def _reports(out: Path) -> list[Path]:
    return sorted(out.glob("eval_*")) if out.is_dir() else []


def _names(directory: Path) -> list[str]:
    """Sorted entry names of *directory* ([] when it does not exist)."""
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


# --- exit 0: clean run -------------------------------------------------------


def test_eval_dev_run_exits_zero_and_writes_reports(
    eval_dataset: BuiltDataset, storage_tmp: Path, capsys: pytest.CaptureFixture
) -> None:
    out = storage_tmp / "reports"
    code, stdout = _run_cli(_eval_argv(eval_dataset.root, out), eval_dataset.settings)
    assert code == 0
    summary = json.loads(stdout)
    assert summary["dataset_id"] == "fixture_v1"
    assert summary["split"] == "dev"
    assert summary["questions"] == 4
    assert summary["error_questions"] == 0
    assert summary["run_id"].startswith("eval_fixture_v1_dev_")
    # Report paths are project-relative, both files exist, both on D:.
    json_rel = Path(summary["report_json"])
    md_rel = Path(summary["report_markdown"])
    assert not json_rel.is_absolute() and not md_rel.is_absolute()
    assert (PROJECT_ROOT / json_rel).is_file()
    assert (PROJECT_ROOT / md_rel).is_file()
    assert (PROJECT_ROOT / json_rel).is_relative_to(PROJECT_ROOT)

    # No query dumps by default: the summary is identifiers and counts only.
    for question in read_question_records(eval_dataset.root, "dev"):
        assert str(question["question"]) not in stdout
    assert "Willow Creek" not in stdout
    assert "passage" not in stdout.lower()
    captured = capsys.readouterr()
    assert captured.err == ""


def test_eval_test_split_run_exits_zero(
    eval_dataset: BuiltDataset, storage_tmp: Path
) -> None:
    out = storage_tmp / "reports"
    code, stdout = _run_cli(
        _eval_argv(eval_dataset.root, out, "--split", "test"),
        eval_dataset.settings,
    )
    assert code == 0
    summary = json.loads(stdout)
    assert summary["split"] == "test"
    assert summary["questions"] == 2


def test_eval_tuning_mode_on_dev_exits_zero(
    eval_dataset: BuiltDataset, storage_tmp: Path
) -> None:
    out = storage_tmp / "reports"
    code, stdout = _run_cli(
        _eval_argv(eval_dataset.root, out, "--tuning"), eval_dataset.settings
    )
    assert code == 0
    assert json.loads(stdout)["questions"] == 4


# --- exit 2: invalid input ---------------------------------------------------


def test_eval_tuning_with_test_split_exits_two(
    eval_dataset: BuiltDataset,
    storage_tmp: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    out = storage_tmp / "reports"
    code, _ = _run_cli(
        _eval_argv(eval_dataset.root, out, "--split", "test", "--tuning"),
        eval_dataset.settings,
    )
    assert code == 2
    assert "tuning" in capsys.readouterr().err
    assert _reports(out) == []  # refused before any run or report


def test_eval_missing_dataset_exits_two(
    eval_dataset: BuiltDataset,
    storage_tmp: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    out = storage_tmp / "reports"
    code, _ = _run_cli(
        _eval_argv(eval_dataset.root / "no_such_dir", out), eval_dataset.settings
    )
    assert code == 2
    assert "--dataset" in capsys.readouterr().err
    assert _reports(out) == []


@pytest.mark.parametrize("top_k", ["0", "21", "-1"])
def test_eval_top_k_out_of_range_exits_two(
    eval_dataset: BuiltDataset, storage_tmp: Path, top_k: str
) -> None:
    out = storage_tmp / "reports"
    code, _ = _run_cli(
        _eval_argv(eval_dataset.root, out, "--top-k", top_k), eval_dataset.settings
    )
    assert code == 2
    assert _reports(out) == []


def test_eval_out_outside_project_exits_two_without_writing(
    eval_dataset: BuiltDataset,
    storage_tmp: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    escape = PROJECT_ROOT.parent / "eval_out_never_created"
    code, _ = _run_cli(_eval_argv(eval_dataset.root, escape), eval_dataset.settings)
    assert code == 2
    assert "beneath the project root" in capsys.readouterr().err
    assert not escape.exists()  # refused before any write


def test_eval_dataset_outside_project_exits_two(
    eval_dataset: BuiltDataset,
    storage_tmp: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    # D:\ exists, so the input-shaped check passes - but the harness
    # refuses a root outside the project before reading anything.
    code, _ = _run_cli(
        _eval_argv(PROJECT_ROOT.parent, storage_tmp / "reports"), eval_dataset.settings
    )
    assert code == 2
    assert "beneath the project root" in capsys.readouterr().err


def test_eval_bad_split_value_is_usage_error_exit_two(
    eval_dataset: BuiltDataset, storage_tmp: Path
) -> None:
    code, _ = _run_cli(
        _eval_argv(eval_dataset.root, storage_tmp / "reports", "--split", "holdout"),
        eval_dataset.settings,
    )
    assert code == 2  # argparse usage error, never a traceback


def test_eval_help_documents_the_safety_contract(
    capsys: pytest.CaptureFixture,
) -> None:
    code = cli_module.main(["eval", "--help"])
    captured = capsys.readouterr()
    assert code == 0
    assert "generation-disabled" in captured.out
    assert "test split" in captured.out


# --- exit 3: integrity -------------------------------------------------------


def test_eval_tampered_document_exits_three(
    eval_dataset: BuiltDataset,
    storage_tmp: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    out = storage_tmp / "reports"
    target = eval_dataset.root / "documents" / "orchard.txt"
    raw = bytearray(target.read_bytes())
    raw[5] ^= 0x10
    target.write_bytes(bytes(raw))

    code, _ = _run_cli(_eval_argv(eval_dataset.root, out), eval_dataset.settings)
    assert code == 3
    assert "integrity" in capsys.readouterr().err
    assert _reports(out) == []  # no report for an aborted run


def test_eval_gold_label_mismatch_exits_three(
    eval_dataset: BuiltDataset,
    storage_tmp: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    records = read_question_records(eval_dataset.root, "dev")
    answer = next(r for r in records if r["id"] == "q0001")
    answer["gold_chunk_ids"] = ["f" * 64]
    answer.pop("gold_answer_span", None)
    rewrite_question_file(eval_dataset.root, "dev", records)

    out = storage_tmp / "reports"
    code, _ = _run_cli(_eval_argv(eval_dataset.root, out), eval_dataset.settings)
    assert code == 3
    assert "integrity" in capsys.readouterr().err
    assert _reports(out) == []


def test_unexpected_internal_error_never_prints_a_traceback(
    eval_dataset: BuiltDataset,
    storage_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("internal detail must not surface")

    monkeypatch.setattr(harness_module, "run_query", boom)
    out = storage_tmp / "reports"
    code, _ = _run_cli(_eval_argv(eval_dataset.root, out), eval_dataset.settings)
    captured = capsys.readouterr()
    # Only typed retrieval errors are contained per question; anything
    # unexpected fails the run as unrecoverable - with a class-name-only
    # message: never a traceback, never the internal detail.
    assert code == 3
    assert "unexpected internal error" in captured.err
    assert "internal detail must not surface" not in captured.err
    assert "Traceback" not in captured.err
    assert _reports(out) == []


# --- exit 4: per-query errors, report still written --------------------------


def test_eval_per_query_errors_exit_four_with_report(
    eval_dataset: BuiltDataset,
    storage_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = harness_module.run_query

    def failing_for_one(*args: object, **kwargs: object) -> object:
        if str(kwargs.get("request_id", "")).endswith(":q0001"):
            raise QueryValidationError("simulated typed retrieval failure")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(harness_module, "run_query", failing_for_one)

    out = storage_tmp / "reports"
    code, stdout = _run_cli(_eval_argv(eval_dataset.root, out), eval_dataset.settings)
    assert code == 4
    summary = json.loads(stdout)
    assert summary["error_questions"] == 1
    assert summary["questions"] == 4
    # The report was still written - errors are reported, never hidden.
    assert _reports(out) != []
    json_files = list(out.glob("eval_*.json"))
    assert len(json_files) == 1
    report = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert report["deterministic"]["error_classes"] == {"QueryValidationError": 1}
    assert report["deterministic"]["metric_counts"]["retrieval_error_count"] == 1


# --- dataset-id containment (hostile ids exit 3, fail-closed) ----------------


def _set_dataset_id(root: Path, value: object) -> None:
    document = load_manifest_doc(root)
    document["dataset_id"] = value
    write_manifest_doc(root, document)


@pytest.mark.parametrize(
    "hostile",
    [
        pytest.param("../../evil", id="traversal"),
        pytest.param("D:\\x", id="drive"),
        pytest.param("a" * 33, id="overlong"),
        pytest.param("a b", id="inner-space"),
    ],
)
def test_eval_hostile_dataset_id_exits_three_fail_closed(
    eval_dataset: BuiltDataset,
    storage_tmp: Path,
    hostile: str,
    capsys: pytest.CaptureFixture,
) -> None:
    _set_dataset_id(eval_dataset.root, hostile)
    out = storage_tmp / "reports"
    eval_runs = eval_dataset.settings.data_dir / "eval_runs"
    before = _names(eval_runs)

    code, stdout = _run_cli(_eval_argv(eval_dataset.root, out), eval_dataset.settings)
    captured = capsys.readouterr()

    assert code == 3  # integrity failure, never 0, never a crash
    assert "dataset_id" in captured.err
    assert "integrity" in captured.err
    assert "Traceback" not in captured.err
    assert str(PROJECT_ROOT) not in captured.err
    assert stdout == ""  # nothing but the bounded summary would print - it never does
    assert _reports(out) == []  # no report for a refused run
    assert _names(eval_runs) == before  # no run directory, no database


def test_eval_report_paths_outside_project_fail_closed_without_summary(
    eval_dataset: BuiltDataset,
    storage_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    # Simulate a write_reports whose paths land OUTSIDE the project root:
    # the CLI must refuse to print them, emit no absolute path, and exit 3.
    escape_json = PROJECT_ROOT.parent / "evil.json"
    escape_md = PROJECT_ROOT.parent / "evil.md"
    monkeypatch.setattr(
        cli_module, "write_reports", lambda *_: (escape_json, escape_md)
    )

    out = storage_tmp / "reports"
    code, stdout = _run_cli(_eval_argv(eval_dataset.root, out), eval_dataset.settings)
    captured = capsys.readouterr()

    assert code == 3
    assert "error: could not write the evaluation report" in captured.err
    assert "Traceback" not in captured.err
    assert str(escape_json) not in stdout + captured.err
    assert str(PROJECT_ROOT) not in stdout + captured.err
    assert stdout == ""  # return happens BEFORE any summary print
    assert not escape_json.exists() and not escape_md.exists()


def test_project_relative_only_formats_contained_paths() -> None:
    inside = cli_module._project_relative(PROJECT_ROOT / "reports" / "x.json")
    assert inside == "reports/x.json"
    assert not Path(inside).is_absolute()
    assert str(PROJECT_ROOT) not in inside

    assert cli_module._project_relative(PROJECT_ROOT.parent / "x.json") is None


# --- ingest subcommand -------------------------------------------------------


def test_ingest_manifest_only_and_exits_zero(
    eval_dataset: BuiltDataset, storage_tmp: Path
) -> None:
    # Present on disk, absent from the manifest: must never be ingested.
    (eval_dataset.root / "documents" / "ambient_notes.txt").write_text(
        "not listed in the manifest\n", encoding="utf-8"
    )
    code, stdout = _run_cli(
        ["ingest", "--dataset", str(eval_dataset.root)], eval_dataset.settings
    )
    assert code == 0
    summary = json.loads(stdout)
    assert summary == {
        "dataset_id": "fixture_v1",
        "documents_listed": 2,
        "ingested": 2,
        "skipped_unsupported": 0,
    }

    db_path = default_db_path(eval_dataset.settings)
    assert db_path.is_file() and db_path.is_relative_to(storage_tmp)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT source_path FROM documents ORDER BY source_path")
        sources = [str(row[0]) for row in rows.fetchall()]
    finally:
        conn.close()
    assert all("ambient_notes" not in source for source in sources)
    assert len(sources) == 2


def test_ingest_tampered_dataset_exits_three(
    eval_dataset: BuiltDataset,
    storage_tmp: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    target = eval_dataset.root / "documents" / "warehouse.txt"
    raw = bytearray(target.read_bytes())
    raw[3] ^= 0x01
    target.write_bytes(bytes(raw))

    code, _ = _run_cli(
        ["ingest", "--dataset", str(eval_dataset.root)], eval_dataset.settings
    )
    assert code == 3
    assert "integrity" in capsys.readouterr().err
    # Fail-closed before any byte was ingested: the database never existed.
    assert not default_db_path(eval_dataset.settings).exists()


def test_ingest_missing_dataset_exits_two(
    eval_dataset: BuiltDataset,
    storage_tmp: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    code, _ = _run_cli(
        ["ingest", "--dataset", str(eval_dataset.root / "no_such_dir")],
        eval_dataset.settings,
    )
    assert code == 2
    assert "--dataset" in capsys.readouterr().err


def test_ingest_gold_question_labels_are_not_consulted(
    eval_dataset: BuiltDataset, storage_tmp: Path
) -> None:
    # ingest uses documents only: question labels (even broken ones) do
    # not block a document ingest - split protection belongs to eval.
    records = read_question_records(eval_dataset.root, "dev")
    answer = next(r for r in records if r["id"] == "q0001")
    answer["gold_chunk_ids"] = ["e" * 64]
    answer.pop("gold_answer_span", None)
    rewrite_question_file(eval_dataset.root, "dev", records)

    code, stdout = _run_cli(
        ["ingest", "--dataset", str(eval_dataset.root)], eval_dataset.settings
    )
    assert code == 0
    assert json.loads(stdout)["ingested"] == 2


# --- no implicit provider activation -----------------------------------------


def test_cli_eval_never_enables_generation(
    eval_dataset: BuiltDataset, storage_tmp: Path, capsys: pytest.CaptureFixture
) -> None:
    # The CLI exposes no flag that could turn generation on; the harness
    # refuses generation-enabled settings anyway (harness-level test).
    import argparse

    parser = cli_module._build_parser()  # noqa: SLF001 - contract probe
    subactions = [
        action
        for action in parser._actions  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
    ]
    assert len(subactions) == 1
    eval_parser = subactions[0].choices["eval"]
    dests = {
        action.dest
        for action in eval_parser._actions  # noqa: SLF001
        if action.dest != "help"
    }
    assert dests == {"dataset", "split", "tuning", "top_k", "out"}
    assert eval_dataset.settings.local_generation_enabled is False

    # Help text states the safety contract, not an activation path.
    assert cli_module.main(["eval", "--help"]) == 0
    captured = capsys.readouterr()
    assert "--generation" not in captured.out
    assert "never contacts a provider" in captured.out
