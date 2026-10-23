"""End-to-end evaluation harness tests (plan v2.4 sections 9.1-9.5, 15).

Proves against a tiny fixture dataset under ``data/tmp`` (D:):

- the run completes through the SHARED ``run_query`` (no second pipeline);
- deterministic request IDs ``eval:<dataset>:<split>:<qid>`` and their
  linkage to records, traces, and run metadata;
- fail-closed behavior: tampered corpus, gold-label mismatch, span
  overflow, config refusals - all before any query is issued;
- manifest-only ingest (ambient files never ingested), read-only corpus;
- honest metric counting (errors stay in denominators, abstention
  correctness distinguishes evidence abstention from generation state);
- safety defaults preserved (generation disabled, floor unset, no model).

No sockets, no model, no provider, no external service: mocked typed
failures only, via monkeypatch of the shared orchestration seam.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from rag_platform.api import run_query as shared_run_query
from rag_platform.config import PROJECT_ROOT, Settings
from rag_platform.eval import DatasetIntegrityError, EvalConfigError, run_eval
from rag_platform.eval import harness as harness_module
from rag_platform.eval.dataset import load_and_verify_dataset
from rag_platform.retrieval import QueryValidationError
from tests.eval.conftest import (
    BuiltDataset,
    load_manifest_doc,
    read_question_records,
    rewrite_question_file,
    write_manifest_doc,
)


def _trace_dump(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        rows: list = []
        for table in ("traces", "trace_stages", "trace_authz"):
            rows.extend(conn.execute(f"SELECT * FROM {table}").fetchall())  # noqa: S608
        return json.dumps([list(row) for row in rows])
    finally:
        conn.close()


def _corpus_snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _fetch_by_request_id(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT request_id, COUNT(*) FROM traces GROUP BY 1")
        return {str(rid): int(n) for rid, n in rows.fetchall()}
    finally:
        conn.close()


# --- happy path --------------------------------------------------------------


def test_dev_run_completes_with_honest_measurements(
    eval_dataset: BuiltDataset,
) -> None:
    result = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)

    assert result.question_count == 4  # dev fixture questions
    assert result.dataset_id == "fixture_v1"
    assert result.split == "dev"
    assert result.ingest_summary["ingested"] == 2
    assert result.error_classes == {}

    # Outcome distributions reflect exactly what happened, honestly:
    # q0003 abstained at the evidence level (abstained outcome); the other
    # three reached the disabled-generation path (unavailable).
    assert result.retrieval_outcome_distribution == {
        "no_candidates": 1,
        "ok": 3,
    }
    assert result.generation_outcome_distribution == {
        "abstained": 1,
        "unavailable": 3,
    }
    assert result.abstention_reason_distribution == {
        "generation_unavailable": 3,
        "no_candidates": 1,
    }
    assert result.served_by_distribution == {"retrieval_only": 4}

    metrics = result.metrics
    # Counts: q0001/q0002 answerable, q0003/q0004 abstain-labeled.
    assert metrics.answerable_count == 2
    assert metrics.unanswerable_count == 2
    assert metrics.retrieval_error_count == 0

    # Both fixture docs are the whole corpus, so all gold is served at
    # top_k=10: recall@10 = (1.0 + 1.0)/2.
    assert metrics.recall_at_10.status == "measured"
    assert metrics.recall_at_10.value == pytest.approx(1.0)
    # precision@5 = (1/5 + 2/5)/2 = 0.3 (two chunks exist, both served)
    assert metrics.precision_at_5.value == pytest.approx(0.3)
    assert metrics.mrr.value is not None and 0.5 <= metrics.mrr.value <= 1.0
    assert metrics.ndcg_at_10.value is not None

    # Abstention correctness, hand-checked: q0003 abstained at the
    # evidence level (no_candidates) -> correct; q0004 only reached
    # generation_unavailable -> incorrect. 1 of 2.
    assert metrics.abstention_correctness.value == pytest.approx(0.5)
    assert metrics.abstention_correctness.status == "measured"
    # No answer-labeled question refused: false refusals = 0/2 (their
    # abstention_reason is generation_unavailable, not an evidence refusal).
    assert metrics.false_refusal_rate.value == pytest.approx(0.0)
    # retrieval-only counts generation_outcome == "unavailable": 3 of 4
    # (the fourth abstained at the evidence level).
    assert metrics.retrieval_only_rate.value == pytest.approx(0.75)

    # No generated answers -> token-F1 and citations are not measured,
    # never zero.
    assert metrics.token_f1.status == "not_measured"
    assert metrics.citation_validity.status == "not_measured"
    assert metrics.latency_p50_ms.status == "measured"
    assert (metrics.latency_p50_ms.value or 0.0) > 0.0

    # Per-stage trace timings (plan 7.1 gate 6): all four M1 stages timed,
    # from the trace (authoritative source). context_build runs for the
    # three questions that reach it - q0003 abstained at retrieval before
    # context assembly, so it honestly has no context_build event.
    stage_counts = {stage.stage: stage.count for stage in result.stage_timings}
    assert stage_counts == {
        "context_build": 3,
        "generation": 4,
        "response": 4,
        "retrieval": 4,
    }
    assert all(stage.p50_ms is not None for stage in result.stage_timings)

    # Isolated per-run database under <data_dir>/eval_runs/<run_id>/.
    assert result.run_db_path.is_file()
    assert result.run_db_path.parent.name == result.run_id
    assert result.run_db_path.is_relative_to(
        eval_dataset.settings.data_dir / "eval_runs"
    )
    assert (result.run_db_path.parent / "run.json").is_file()


def test_harness_queries_through_the_shared_orchestration() -> None:
    # Contract: there is exactly one pipeline; the harness binds the same
    # object the API exposes.
    assert harness_module.run_query is shared_run_query


def test_safety_defaults_survive_a_run(eval_dataset: BuiltDataset) -> None:
    settings = eval_dataset.settings
    assert settings.local_generation_enabled is False
    assert settings.retrieval_score_floor is None
    assert settings.generation_model is None
    top_k_before = settings.retrieval_top_k

    result = run_eval(settings, dataset_root=eval_dataset.root)

    # The harness never mutates the caller's settings...
    assert settings.local_generation_enabled is False
    assert settings.retrieval_score_floor is None
    assert settings.retrieval_top_k == top_k_before  # override was on a copy
    # ...and the recorded configuration states the safe values explicitly.
    assert result.config_labels["generation_enabled"] == "false"
    assert result.config_labels["generation_model"] == "none"
    assert result.config_labels["retrieval_score_floor"] == "unset"
    assert result.config_labels["retrieval_mode"] == "lexical_only"


# --- deterministic IDs, linkage, and redaction -------------------------------


def test_request_ids_are_deterministic_across_runs(
    eval_dataset: BuiltDataset,
) -> None:
    run1 = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    run2 = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)

    expected = {f"eval:fixture_v1:dev:{record.question_id}" for record in run1.records}
    assert expected == {
        f"eval:fixture_v1:dev:{record.question_id}" for record in run2.records
    }
    # Each run owns its database, so the same ids coexist without UNIQUE
    # violations - and the traces prove the linkage.
    assert _fetch_by_request_id(run1.run_db_path) == dict.fromkeys(expected, 1)
    assert _fetch_by_request_id(run2.run_db_path) == dict.fromkeys(expected, 1)
    assert {record.question_id for record in run1.records} == {
        "q0001",
        "q0002",
        "q0003",
        "q0004",
    }
    # One trace per question; the run metadata repeats the same identity.
    run_json = json.loads(
        (run1.run_db_path.parent / "run.json").read_text(encoding="utf-8")
    )
    assert run_json["question_count"] == 4
    assert run_json["dataset_id"] == "fixture_v1"
    assert run_json["split"] == "dev"
    assert run_json["manifest_digest"] == run1.manifest_digest


def test_no_question_text_reaches_traces_or_run_metadata(
    eval_dataset: BuiltDataset,
) -> None:
    result = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    dump = _trace_dump(result.run_db_path)
    run_meta = (result.run_db_path.parent / "run.json").read_text(encoding="utf-8")
    for secret in (
        "apple harvest",
        "Willow Creek",
        "Zzqxw",
        "banana shipment",
        "mango harvest",
    ):
        assert secret not in dump, f"trace leaked {secret!r}"
        assert secret not in run_meta

    # Stage rows carry digests/enums/counters only: no excerpts, ever.
    conn = sqlite3.connect(result.run_db_path)
    try:
        excerpts = conn.execute(
            "SELECT COUNT(*) FROM trace_stages WHERE excerpt IS NOT NULL"
        ).fetchone()[0]
        assert excerpts == 0
        authz = conn.execute("SELECT COUNT(*) FROM trace_authz").fetchone()[0]
        assert authz == 0  # no identity exists before M2
    finally:
        conn.close()


def test_manifest_and_question_hashes_are_reported_identity(
    eval_dataset: BuiltDataset,
) -> None:
    manifest = load_and_verify_dataset(eval_dataset.root)
    result = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    assert result.manifest_digest == manifest.manifest_digest()
    assert result.question_file_sha256 == manifest.entry_for_split("dev").sha256
    assert len(result.question_file_sha256) == 64


# --- test-split accounting ---------------------------------------------------


def test_prior_test_split_evaluations_counts_only_completed_test_runs(
    eval_dataset: BuiltDataset,
) -> None:
    first = run_eval(
        eval_dataset.settings, dataset_root=eval_dataset.root, split="test"
    )
    assert first.prior_test_split_evaluations == 0
    assert first.question_count == 2  # q0005 + q0006

    # A dev run in between does not add a test-split evaluation - the
    # counter tracks prior test runs of this dataset at every point.
    dev = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    assert dev.prior_test_split_evaluations == 1

    second = run_eval(
        eval_dataset.settings, dataset_root=eval_dataset.root, split="test"
    )
    assert second.prior_test_split_evaluations == 1


# --- fail closed -------------------------------------------------------------


def test_tampered_corpus_aborts_before_any_run_artifact(
    eval_dataset: BuiltDataset,
) -> None:
    target = eval_dataset.root / "documents" / "orchard.txt"
    raw = bytearray(target.read_bytes())
    raw[7] ^= 0x08
    target.write_bytes(bytes(raw))

    eval_runs = eval_dataset.settings.data_dir / "eval_runs"
    before = sorted(p.name for p in eval_runs.glob("*")) if eval_runs.is_dir() else []
    with pytest.raises(DatasetIntegrityError, match="sha256 mismatch"):
        run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    after = sorted(p.name for p in eval_runs.glob("*")) if eval_runs.is_dir() else []
    assert after == before  # no run directory, no database, no queries


def test_gold_chunk_mismatch_aborts_before_any_query(
    eval_dataset: BuiltDataset,
) -> None:
    records = read_question_records(eval_dataset.root, "dev")
    answer = next(r for r in records if r["id"] == "q0001")
    answer["gold_chunk_ids"] = ["0" * 64]
    answer.pop("gold_answer_span", None)  # span must name a declared gold id
    rewrite_question_file(eval_dataset.root, "dev", records)

    with pytest.raises(DatasetIntegrityError, match="not present in the ingested"):
        run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)


def test_gold_span_beyond_chunk_content_aborts(
    eval_dataset: BuiltDataset,
) -> None:
    records = read_question_records(eval_dataset.root, "dev")
    answer = next(r for r in records if r["id"] == "q0001")
    span = answer["gold_answer_span"]
    assert isinstance(span, dict)
    span["char_end"] = 1_000_000  # passes record validation; fails reality
    rewrite_question_file(eval_dataset.root, "dev", records)

    with pytest.raises(DatasetIntegrityError, match="exceeds its chunk content"):
        run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)


def test_generation_enabled_settings_are_refused(
    eval_dataset: BuiltDataset,
) -> None:
    enabled = eval_dataset.settings.model_copy(
        update={"local_generation_enabled": True}
    )
    with pytest.raises(EvalConfigError, match="generation-disabled only"):
        run_eval(enabled, dataset_root=eval_dataset.root)


@pytest.mark.parametrize("top_k", [0, 21, -3])
def test_top_k_bounds_are_refused(eval_dataset: BuiltDataset, top_k: int) -> None:
    with pytest.raises(EvalConfigError, match="top_k must be within 1..20"):
        run_eval(eval_dataset.settings, dataset_root=eval_dataset.root, top_k=top_k)


def test_chunk_settings_must_match_the_manifest(
    eval_dataset: BuiltDataset,
) -> None:
    mismatched = eval_dataset.settings.model_copy(update={"chunk_size": 999})
    with pytest.raises(EvalConfigError, match="do not match the manifest"):
        run_eval(mismatched, dataset_root=eval_dataset.root)


def test_dataset_root_outside_the_project_is_refused(
    eval_dataset: BuiltDataset,
) -> None:
    with pytest.raises(EvalConfigError, match="beneath the project root"):
        run_eval(
            eval_dataset.settings,
            dataset_root=PROJECT_ROOT.parent / "not_beneath_the_project",
        )


def test_question_over_the_query_char_bound_is_refused(
    eval_dataset: BuiltDataset,
) -> None:
    tight = eval_dataset.settings.model_copy(update={"retrieval_query_max_chars": 10})
    with pytest.raises(EvalConfigError, match="query character bound"):
        run_eval(tight, dataset_root=eval_dataset.root)


def test_document_count_bound_is_refused(eval_dataset: BuiltDataset) -> None:
    root = eval_dataset.root
    documents = root / "documents"
    document = load_manifest_doc(root)
    files = document["files"]
    assert isinstance(files, list)
    # 2 existing + 199 tiny extras = 201 documents > MAX_DOCUMENTS_PER_RUN.
    for index in range(199):
        path = documents / f"extra_{index:03d}.txt"
        raw = f"ambient number {index}\n".encode()
        path.write_bytes(raw)
        files.append(
            {
                "path": f"documents/extra_{index:03d}.txt",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
            }
        )
    write_manifest_doc(root, document)

    with pytest.raises(EvalConfigError, match="the bound for one run is 200"):
        run_eval(eval_dataset.settings, dataset_root=root)


def test_tuning_run_refuses_the_test_split(eval_dataset: BuiltDataset) -> None:
    with pytest.raises(DatasetIntegrityError, match="tuning mode"):
        run_eval(
            eval_dataset.settings,
            dataset_root=eval_dataset.root,
            split="test",
            tuning=True,
        )


# --- manifest-only ingest, read-only corpus ---------------------------------


def test_ambient_files_are_never_ingested(eval_dataset: BuiltDataset) -> None:
    # Present on disk, absent from the manifest.
    (eval_dataset.root / "documents" / "ambient_notes.txt").write_text(
        "This file is NOT listed in the manifest.\n", encoding="utf-8"
    )
    result = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    assert result.ingest_summary["ingested"] == 2  # manifest-listed only

    conn = sqlite3.connect(result.run_db_path)
    try:
        (doc_count,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        (chunk_count,) = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
    finally:
        conn.close()
    assert doc_count == 2
    assert chunk_count == 2  # one chunk per fixture doc at 1000/150
    assert "ambient_notes.txt" not in _trace_dump(result.run_db_path)


def test_corpus_is_read_only_during_a_run(eval_dataset: BuiltDataset) -> None:
    before = _corpus_snapshot(eval_dataset.root)
    run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    run_eval(eval_dataset.settings, dataset_root=eval_dataset.root, split="test")
    after = _corpus_snapshot(eval_dataset.root)
    assert after == before  # no writes, no additions, no deletions


# --- per-query typed errors stay visible ------------------------------------


def test_typed_query_error_is_counted_and_kept_in_denominators(
    eval_dataset: BuiltDataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = harness_module.run_query

    def failing_for_first_question(*args: object, **kwargs: object) -> object:
        request_id = kwargs.get("request_id", "")
        if str(request_id).endswith(":q0001"):
            raise QueryValidationError("simulated typed retrieval failure")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(harness_module, "run_query", failing_for_first_question)

    result = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)

    # The run completes; the error is a visible, counted outcome.
    assert result.question_count == 4
    assert result.error_classes == {"QueryValidationError": 1}
    errored = next(r for r in result.records if r.question_id == "q0001")
    assert errored.retrieval_error is True
    assert errored.served_chunk_ids == ()
    assert errored.retrieval_outcome == "error"

    metrics = result.metrics
    assert metrics.retrieval_error_count == 1
    # Errors stay in denominators: answerable = {q0001 error -> 0.0,
    # q0002 -> 1.0} so macro recall@10 = 0.5 (never dropped).
    assert metrics.recall_at_10.value == pytest.approx(0.5)
    assert metrics.recall_at_10.status == "measured"
