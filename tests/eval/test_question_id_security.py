"""Question-id security tests (plan v2.4 sections 9.1-9.6; change note 14).

The ``question_id`` flows into trace request IDs
(``eval:<dataset_id>:<split>:<question_id>``, harness), evidence-row
rendering in gate-decision reports (report markdown), and raw CLI
output, so it is validated as a strict identifier - fullmatch on the
RAW value, reject-NOT-strip - at the single authoritative boundary
(``_validate_question_record``, the only ``Question`` construction
site in ``src/``):

- hostile ids (uppercase, whitespace, newline, controls, unicode
  confusables, path-like, separators, punctuation, markdown pipes,
  ``:``-forgery of the documented 4-segment request-id shape, overlong,
  empty) are rejected with :class:`DatasetIntegrityError` at question
  load - before any run directory, database, trace row, or report is
  created;
- valid ids pass through byte-identically (no strip, no normalize, no
  truncate);
- the strip/dedup collision that silently lost trace rows is closed
  structurally (the format check rejects what dedup used to see as
  distinct);
- the frozen corpus (114 ids) loads unchanged and every id satisfies
  the contract;
- the CLI refuses a hostile-id dataset with exit 3, a bounded message,
  and no traceback.

Offline: fixture dataset under ``data/tmp`` (D:), no network, no model.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from rag_platform import cli as cli_module
from rag_platform.config import Settings
from rag_platform.eval import build_report, run_eval
from rag_platform.eval.dataset import (
    _QUESTION_ID_PATTERN,
    DatasetIntegrityError,
    load_and_verify_dataset,
    load_questions,
)
from rag_platform.eval.report import render_markdown
from tests.eval.conftest import (
    BuiltDataset,
    read_question_records,
    rewrite_question_file,
)


def _names(directory: Path) -> list[str]:
    """Sorted entry names of *directory* ([] when it does not exist)."""
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


def _set_first_question_id(root: Path, value: object) -> None:
    """Set dev record 0's id to *value* as-is and re-hash the manifest.

    The hostile string is written only as question-file CONTENT (a JSON
    value); no filesystem path is ever constructed from it.
    """
    records = read_question_records(root, "dev")
    records[0]["id"] = value
    rewrite_question_file(root, "dev", records)


# --- 1: reject matrix (format failure at load, no execution) -----------------

_INVALID_IDS = [
    # empty / whitespace-only (reject-NOT-strip rows)
    pytest.param("", id="empty"),
    pytest.param(" ", id="single-space"),
    pytest.param("   ", id="spaces-only"),
    # leading / trailing / inner whitespace
    pytest.param(" q1", id="leading-space"),
    pytest.param("q1 ", id="trailing-space"),
    pytest.param(" q1 ", id="surrounding-spaces"),
    pytest.param("q 1", id="inner-space"),
    pytest.param("\tq1", id="leading-tab"),
    pytest.param("q1\t", id="trailing-tab"),
    # newline / CR variants: a trailing-regex ($-less) fullmatch must
    # reject these even though the value "looks" clean before the EOL.
    pytest.param("q1\n", id="trailing-newline"),
    pytest.param("q1\r", id="trailing-cr"),
    pytest.param("q1\r\n", id="trailing-crlf"),
    pytest.param("\nq1", id="leading-newline"),
    pytest.param("q1\nq2", id="embedded-newline"),
    # uppercase
    pytest.param("Q1", id="uppercase"),
    pytest.param("qA", id="embedded-uppercase"),
    pytest.param("UPPERCASE", id="all-uppercase"),
    # control characters (incl. NUL and the ANSI escape the raw-print
    # CLI sink would otherwise forward to the terminal)
    pytest.param("\x00", id="control-nul"),
    pytest.param("a\x00b", id="control-nul-embedded"),
    pytest.param("q1\x1b", id="control-ansi-escape"),
    pytest.param("q1\x7f", id="control-del"),
    pytest.param("\x01q", id="control-soh"),
    # unicode confusables / non-ASCII (incl. zero-width)
    pytest.param("q\u00e91", id="confusable-latin-e-acute"),
    pytest.param("q\u04301", id="confusable-cyrillic-a"),
    pytest.param("\uff11q", id="confusable-fullwidth-digit"),
    pytest.param("q\u20701", id="confusable-superscript-zero"),
    pytest.param("q\u200b1", id="confusable-zero-width-space"),
    pytest.param("q\u20281", id="confusable-line-separator"),
    # path-like (inert for this sink, rejected anyway - no path grammar
    # ever reaches a question id)
    pytest.param("../../q1", id="path-dotdot-slash"),
    pytest.param("..\\q1", id="path-dotdot-backslash"),
    pytest.param("q/1", id="path-slash"),
    pytest.param("q\\1", id="path-backslash"),
    pytest.param("D:q1", id="path-drive-relative"),
    pytest.param("/q1", id="path-absolute"),
    # ``:``-forgery of the request-id namespace (the Medium finding)
    pytest.param("q1:x", id="forgery-colon-suffix"),
    pytest.param("x:prod:q1", id="forgery-colon-segments"),
    pytest.param("eval:fixture_v1:dev:q1", id="forgery-whole-request-id-shape"),
    pytest.param(":", id="forgery-bare-colon"),
    # markdown table / evidence-row injection
    pytest.param("q1|fake", id="markdown-pipe"),
    pytest.param("q1\n|---|", id="markdown-row-separator"),
    pytest.param("|q1", id="markdown-leading-pipe"),
    pytest.param("q1\n| FORGED | row |", id="markdown-forged-evidence-row"),
    # punctuation / shell-ish characters
    pytest.param("q1%s", id="punct-percent-s"),
    pytest.param("q1%0a", id="punct-url-encoded-newline"),
    pytest.param("q1;x", id="punct-semicolon"),
    pytest.param("q1$x", id="punct-dollar"),
    pytest.param("q1*x", id="punct-star"),
    pytest.param("q1?q1", id="punct-question"),
    pytest.param("q1#q1", id="punct-hash"),
    pytest.param("q1@q1", id="punct-at"),
    pytest.param("q1+q1", id="punct-plus"),
    pytest.param("q1=q1", id="punct-equals"),
    pytest.param("q1'x", id="punct-single-quote"),
    pytest.param('q1"x', id="punct-double-quote"),
    pytest.param("q1`x", id="punct-backtick"),
    pytest.param("q1(x)", id="punct-parens"),
    pytest.param("q1[x]", id="punct-brackets"),
    pytest.param("q1{x}", id="punct-braces"),
    pytest.param("q1<q1", id="punct-angle"),
    pytest.param("q1,q1", id="punct-comma"),
    pytest.param("q1.q1", id="punct-dot"),
    pytest.param("q1~q1", id="punct-tilde"),
    pytest.param("q1^q1", id="punct-caret"),
    pytest.param("q1&", id="punct-ampersand"),
    # leading dash/underscore (first char must be [a-z0-9])
    pytest.param("-q1", id="leading-dash"),
    pytest.param("_q1", id="leading-underscore"),
    # overlong (contract: 1 + {0,63} = 64 max)
    pytest.param("a" * 65, id="overlong-65"),
    pytest.param("a" * 200, id="overlong-200"),
    pytest.param("q" + "a" * 64, id="overlong-65-after-prefix"),
    # non-str (type boundary, pre-existing behavior guard)
    pytest.param(123, id="non-str-int"),
    pytest.param(None, id="non-str-none"),
    pytest.param(["q1"], id="non-str-list"),
    pytest.param(True, id="non-str-bool"),
]


@pytest.mark.parametrize("hostile", _INVALID_IDS)
def test_invalid_question_ids_are_rejected_at_load(
    eval_dataset: BuiltDataset, hostile: object
) -> None:
    _set_first_question_id(eval_dataset.root, hostile)
    manifest = load_and_verify_dataset(eval_dataset.root)  # hashes valid
    with pytest.raises(DatasetIntegrityError, match="id must match|non-empty"):
        load_questions(manifest, "dev")


# --- 2: valid ids pass through byte-identically ------------------------------

_VALID_IDS = [
    pytest.param("q0001", id="frozen-style"),
    pytest.param("q1", id="minimal-two-char"),
    pytest.param("a", id="minimal-one-char"),
    pytest.param("9lives", id="leading-digit"),
    pytest.param("hero-1", id="named-with-hyphen"),
    pytest.param("policy_v2", id="named-with-underscore"),
    pytest.param("chapter-3a", id="mixed-hyphen-digit"),
    pytest.param("q_0001", id="underscore-joined"),
    pytest.param("a" * 64, id="boundary-64"),
    pytest.param("0", id="minimal-digit"),
]


@pytest.mark.parametrize("candidate", _VALID_IDS)
def test_valid_question_ids_pass_through_exactly(
    eval_dataset: BuiltDataset, candidate: str
) -> None:
    _set_first_question_id(eval_dataset.root, candidate)
    manifest = load_and_verify_dataset(eval_dataset.root)
    questions = load_questions(manifest, "dev")
    assert questions.questions[0].question_id == candidate  # byte-exact


def test_boundary_lengths_64_valid_65_invalid(eval_dataset: BuiltDataset) -> None:
    _set_first_question_id(eval_dataset.root, "a" * 64)
    manifest = load_and_verify_dataset(eval_dataset.root)
    assert load_questions(manifest, "dev").questions[0].question_id == "a" * 64

    _set_first_question_id(eval_dataset.root, "a" * 65)
    manifest = load_and_verify_dataset(eval_dataset.root)
    with pytest.raises(DatasetIntegrityError, match="id must match"):
        load_questions(manifest, "dev")


# --- 3: reject-NOT-strip regression (the pre-fix silent-alteration class) ---


@pytest.mark.parametrize(
    "hostile",
    [
        pytest.param(" q1 ", id="surrounding-spaces"),
        pytest.param("q1\n", id="trailing-newline"),
        pytest.param("q1 ", id="trailing-space"),
    ],
)
def test_ids_are_rejected_never_silently_stripped(
    eval_dataset: BuiltDataset, hostile: str
) -> None:
    # Before the contract these loaded as "q1" (strip at the record
    # boundary); now they are refused outright - the stored id is never
    # a mangled form of the manifest value.
    _set_first_question_id(eval_dataset.root, hostile)
    manifest = load_and_verify_dataset(eval_dataset.root)
    with pytest.raises(DatasetIntegrityError, match="id must match"):
        load_questions(manifest, "dev")


# --- 4: request-id forgery is structurally unreachable -----------------------


def test_forged_request_id_shape_has_more_than_four_segments() -> None:
    # Premise: the documented trace request-id shape is exactly four
    # ':'-separated segments (docs/EVALUATION.md section 7). A hostile
    # id containing ':' would mint a fifth segment - the exact forgery
    # class closed for dataset_id.
    forged = "eval:fixture_v1:dev:x:injected"
    assert len(forged.split(":")) > 4


def test_colon_forgery_id_cannot_reach_the_request_id(
    eval_dataset: BuiltDataset,
) -> None:
    _set_first_question_id(eval_dataset.root, "x:injected")
    manifest = load_and_verify_dataset(eval_dataset.root)
    with pytest.raises(DatasetIntegrityError, match="id must match"):
        load_questions(manifest, "dev")
    # No question object exists, so the harness can never build
    # "eval:fixture_v1:dev:x:injected" from a loaded dataset.


# --- 5: strip/dedup collision (silent trace loss) is closed ------------------


def test_duplicate_ids_still_rejected(eval_dataset: BuiltDataset) -> None:
    records = read_question_records(eval_dataset.root, "dev")
    records.append(dict(records[0]))
    rewrite_question_file(eval_dataset.root, "dev", records)
    manifest = load_and_verify_dataset(eval_dataset.root)
    with pytest.raises(DatasetIntegrityError, match="duplicate question id"):
        load_questions(manifest, "dev")


def test_strip_dedup_collision_pair_is_rejected(
    eval_dataset: BuiltDataset,
) -> None:
    # Pre-fix: "q1" and " q1 " were distinct for dedup (raw values) yet
    # both stored as "q1" (stripped) -> identical request_ids -> UNIQUE
    # violation swallowed by best-effort tracing -> silent evidence-row
    # loss. Now the second value fails the format check outright.
    records = read_question_records(eval_dataset.root, "dev")
    second = dict(records[1])
    second["id"] = " q1 "
    records.append(second)
    rewrite_question_file(eval_dataset.root, "dev", records)
    manifest = load_and_verify_dataset(eval_dataset.root)
    with pytest.raises(DatasetIntegrityError, match="id must match"):
        load_questions(manifest, "dev")


# --- 6: markdown evidence-row injection --------------------------------------


def test_markdown_injection_id_is_rejected(eval_dataset: BuiltDataset) -> None:
    _set_first_question_id(eval_dataset.root, "q1\n|---|")
    manifest = load_and_verify_dataset(eval_dataset.root)
    with pytest.raises(DatasetIntegrityError, match="id must match"):
        load_questions(manifest, "dev")


def test_markdown_sink_would_emit_the_forged_row_if_admitted(
    eval_dataset: BuiltDataset,
) -> None:
    # Defense-in-depth mirror of the dataset_id "prove it would have
    # escaped" tests: run a real (valid) eval, then splice a hostile id
    # into the already-built report dict and render - proving the
    # report-markdown sink emits a forged evidence row for any id the
    # loader would have admitted. Pure dictionary manipulation after a
    # real run; nothing is written.
    run = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    report = build_report(run)
    forged = "q1\n|---|---|---|---|---|---|---|\n| FORGED | forged | forged | 0 | 0 | ok | unavailable |"
    records = report["deterministic"]["records"]  # type: ignore[index]
    assert all(forged != record["question_id"] for record in records)
    records[0]["question_id"] = forged
    rendered = render_markdown(run, report)
    # The forged separator and row land inside the evidence table -
    # which is exactly why the loader must never admit such an id.
    assert "\n| FORGED |" in rendered
    assert rendered.count("\n|---|---|---|---|---|---|---|") >= 2


# --- 7: invalid ids produce NO artifacts and NO traces -----------------------


def test_invalid_question_id_produces_no_artifacts_or_traces(
    eval_dataset: BuiltDataset,
) -> None:
    _set_first_question_id(eval_dataset.root, "x:injected")
    eval_runs = eval_dataset.settings.data_dir / "eval_runs"
    reports = eval_dataset.root.parent / "reports"
    eval_runs_before = _names(eval_runs)
    reports_before = _names(reports)

    with pytest.raises(DatasetIntegrityError, match="id must match"):
        run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)

    # Question load happens before the run directory and its trace
    # database are created (harness: load_questions precedes
    # initialize_database), so no run dir, no corpus.db, no trace row,
    # and no report can exist for a rejected id.
    assert _names(eval_runs) == eval_runs_before
    assert _names(reports) == reports_before


# --- 8: frozen corpus compatibility (114 ids, byte-identical) ----------------


def test_frozen_ids_satisfy_the_contract() -> None:
    from rag_platform.config import PROJECT_ROOT

    root = PROJECT_ROOT / "corpora" / "synthetic_v1"
    manifest = load_and_verify_dataset(root)  # read-only
    dev = load_questions(manifest, "dev")
    test = load_questions(manifest, "test")
    loaded = tuple(q.question_id for q in dev.questions) + tuple(
        q.question_id for q in test.questions
    )
    assert len(loaded) == 114  # dev 70 + test 44

    # Every frozen id satisfies the strict contract...
    assert all(_QUESTION_ID_PATTERN.fullmatch(qid) is not None for qid in loaded)

    # ...and is byte-identical to the manifest-file value (load applies
    # no strip, no normalization to the frozen corpus).
    raw_ids: list[str] = []
    for split in ("dev", "test"):
        path = root / "questions" / f"questions_{split}.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                raw_ids.append(json.loads(line)["id"])
    assert tuple(raw_ids) == loaded


# --- 9: cross-split id reuse stays legal (no over-strengthening) ------------


def test_same_id_in_dev_and_test_is_accepted(
    eval_dataset: BuiltDataset,
) -> None:
    # Request ids differ by the split segment, so an id reused across
    # splits never collides in traces; dedup is per-file by design.
    records = read_question_records(eval_dataset.root, "test")
    records[0]["id"] = "q0001"  # already used in dev
    rewrite_question_file(eval_dataset.root, "test", records)
    manifest = load_and_verify_dataset(eval_dataset.root)
    assert load_questions(manifest, "dev").questions
    assert load_questions(manifest, "test").questions[0].question_id == "q0001"


# --- 10: determinism across repeated validation ------------------------------


def test_validation_is_deterministic_across_repeats(
    eval_dataset: BuiltDataset,
) -> None:
    _set_first_question_id(eval_dataset.root, "hero-1_policy-2")
    first = load_questions(load_and_verify_dataset(eval_dataset.root), "dev")
    second = load_questions(load_and_verify_dataset(eval_dataset.root), "dev")
    assert tuple(q.question_id for q in first.questions) == tuple(
        q.question_id for q in second.questions
    )


# --- 11: CLI end-to-end (exit 3, bounded message, no traceback) --------------


def test_cli_refuses_hostile_id_dataset_without_traceback(
    eval_dataset: BuiltDataset,
    storage_tmp: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    _set_first_question_id(eval_dataset.root, "x:injected")
    out = storage_tmp / "reports"
    eval_runs = eval_dataset.settings.data_dir / "eval_runs"
    eval_runs_before = _names(eval_runs)

    stdout = io.StringIO()
    code = cli_module.main(
        [
            "eval",
            "--dataset",
            str(eval_dataset.root),
            "--out",
            str(out),
            "--split",
            "dev",
        ],
        settings=eval_dataset.settings,
        stdout=stdout,
    )

    assert code == 3  # EXIT_UNRECOVERABLE - integrity failure contract
    captured = capsys.readouterr()
    assert "dataset integrity failure" in captured.err
    assert "id must match" in captured.err
    # Bounded, typed message - never a traceback or raw exception.
    assert "Traceback" not in captured.err
    assert "DatasetIntegrityError" not in captured.err
    # No artifacts: no run database (hence zero trace rows), no report.
    assert _names(eval_runs) == eval_runs_before
    assert _names(out) == []
