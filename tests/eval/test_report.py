"""Report generation tests (plan v2.4 sections 9.1, 9.5, 9.6; Phase 8).

Proves the contract the plan asks reports to keep:

- the ``deterministic`` section is reproducible run-over-run while run
  identity, timestamps, wall time, latency, and stage timings stay in
  ``volatile``;
- every metric row carries an explicit ``measured``/``not_measured``
  status, and ``not_measured`` never carries a fabricated value;
- no question text, answer text, passage text, or absolute path reaches
  either output;
- the plan section 9.4 known-limitations header prints in every report;
- reports accumulate - writing two runs never overwrites either.

Offline: fixture dataset under ``data/tmp`` (D:), no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_platform.config import PROJECT_ROOT
from rag_platform.eval import KNOWN_LIMITATIONS, build_report, run_eval
from rag_platform.eval.report import render_markdown, write_reports
from tests.eval.conftest import BuiltDataset

_DETERMINISTIC_METRIC_NAMES = {
    "recall_at_5",
    "recall_at_10",
    "precision_at_5",
    "precision_at_10",
    "mrr",
    "ndcg_at_10",
    "token_f1",
    "abstention_correctness",
    "false_refusal_rate",
    "retrieval_only_rate",
    "citation_validity",
}

_SECRETS = (
    "Willow Creek",
    "apple harvest",
    "Zzqxw",
    "banana shipment",
    "mango harvest",
    "42 bushels",
    "17 crates",
)


def test_deterministic_section_is_reproducible_across_runs(
    eval_dataset: BuiltDataset,
) -> None:
    run1 = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    run2 = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    report1 = build_report(run1)
    report2 = build_report(run2)

    assert report1["schema"] == "rag-platform-eval-report/1"
    assert report1["deterministic"] == report2["deterministic"]
    assert report1["known_limitations"] == list(KNOWN_LIMITATIONS)

    # Run identity lives only in volatile, and differs per run.
    det = report1["deterministic"]
    assert "run_id" not in det and "wall_time_s" not in det and "run_db" not in det
    volatile1, volatile2 = report1["volatile"], report2["volatile"]
    assert volatile1["run_id"] == run1.run_id
    assert volatile2["run_id"] == run2.run_id
    assert volatile1["run_id"] != volatile2["run_id"]
    assert volatile1["run_db"] == f"data/eval_runs/{run1.run_id}/corpus.db"
    assert not Path(str(volatile1["run_db"])).is_absolute()


def test_latency_lives_only_in_the_volatile_section(
    eval_dataset: BuiltDataset,
) -> None:
    report = build_report(
        run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    )
    det_names = {row["name"] for row in report["deterministic"]["metrics"]}
    vol_names = {row["name"] for row in report["volatile"]["metrics"]}
    assert det_names == _DETERMINISTIC_METRIC_NAMES
    assert vol_names == {"latency_p50_ms", "latency_p95_ms"}
    # Config and identity hashes are deterministic facts.
    det = report["deterministic"]
    assert det["top_k"] == 10
    assert len(det["manifest_sha256"]) == 64
    assert len(det["question_file_sha256"]) == 64
    assert (
        det["lockfile_sha256"] == "not_available" or len(det["lockfile_sha256"]) == 64
    )
    assert det["metric_counts"] == {
        "answerable_count": 2,
        "unanswerable_count": 2,
        "generation_unavailable_count": 3,
        "retrieval_error_count": 0,
    }


def test_every_metric_row_carries_an_honest_status(
    eval_dataset: BuiltDataset,
) -> None:
    report = build_report(
        run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    )
    rows = report["deterministic"]["metrics"] + report["volatile"]["metrics"]
    assert len(rows) == 13
    for row in rows:
        assert row["status"] in {"measured", "not_measured"}
        if row["status"] == "not_measured":
            # Unmeasured is never dressed up as a number - not 0, not 100.
            assert row["value"] is None
        else:
            assert isinstance(row["value"], (int, float))
            assert 0.0 <= float(row["value"])
    by_name = {row["name"]: row for row in rows}
    assert by_name["token_f1"]["value"] is None  # generation disabled
    assert by_name["citation_validity"]["value"] is None
    assert by_name["abstention_correctness"]["status"] == "measured"


def test_no_raw_text_or_absolute_paths_in_json_or_markdown(
    eval_dataset: BuiltDataset,
) -> None:
    run = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    report = build_report(run)
    json_text = json.dumps(report, indent=2, sort_keys=True)
    md_text = render_markdown(run, report)

    for output in (json_text, md_text):
        for secret in _SECRETS:
            assert secret not in output, f"report leaked {secret!r}"
        assert str(PROJECT_ROOT) not in output  # no absolute path
        assert PROJECT_ROOT.as_posix() not in output

    # Records are ID-only: counts and enums, never text.
    for record in report["deterministic"]["records"]:
        assert set(record) == {
            "question_id",
            "question_type",
            "expected_outcome",
            "gold_chunk_count",
            "served_chunk_count",
            "served_chunk_ids",
            "retrieval_outcome",
            "generation_outcome",
            "abstention_reason",
            "retrieval_error",
        }
        assert record["question_id"].startswith("q")


def test_markdown_header_limits_and_status_column(
    eval_dataset: BuiltDataset,
) -> None:
    run = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    report = build_report(run)
    md = render_markdown(run, report)

    assert md.startswith("# Evaluation report: fixture_v1")
    assert "## Known metric limitations (plan 9.4)" in md
    for limitation in KNOWN_LIMITATIONS:
        assert limitation in md
    assert "| Metric | Value | Status | Notes |" in md
    # not_measured renders as text, never as a number
    assert "| token_f1 | not_measured | not_measured |" in md
    assert "| citation_validity | not_measured | not_measured |" in md
    # measured rows render values with their status
    assert "abstention_correctness | 0.5000 | measured" in md
    # latency rows are labeled volatile in the notes column
    assert "(volatile)" in md
    assert "## Per-question records (IDs only)" in md
    assert "| q0001 |" in md
    # the test-split evidence statement is printed with its caveat
    assert "Prior test-split evaluations (this dataset)" in md
    assert "deleting that directory" in md


def test_write_reports_writes_both_formats(
    eval_dataset: BuiltDataset, storage_tmp: Path
) -> None:
    run = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    out = storage_tmp / "reports"
    json_path, md_path = write_reports(run, out)

    assert json_path.is_file() and md_path.is_file()
    assert json_path.parent == out and md_path.parent == out
    assert json_path.suffix == ".json" and md_path.suffix == ".md"
    assert json_path.name.startswith("eval_fixture_v1_dev_")
    assert json_path.stem.endswith(run.run_id.rsplit("_", 1)[-1])

    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed == build_report(run)
    assert md_path.read_text(encoding="utf-8") == render_markdown(run, parsed)


def test_write_reports_never_overwrites_an_earlier_run(
    eval_dataset: BuiltDataset, storage_tmp: Path
) -> None:
    run1 = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    run2 = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    out = storage_tmp / "reports"
    json1, md1 = write_reports(run1, out)
    json2, md2 = write_reports(run2, out)

    assert json1 != json2 and md1 != md2
    assert {json1, json2, md1, md2} == set(out.iterdir())
    first = json.loads(json1.read_text(encoding="utf-8"))
    assert first["volatile"]["run_id"] == run1.run_id  # untouched
