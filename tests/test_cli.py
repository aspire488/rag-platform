"""Phase 7 CLI tests (plan v2.4 section 15 Phase 7 row: API + CLI incl.
``report`` skeleton): help, valid query, invalid input, retrieval-only
reporting, exit codes, the report skeleton, and the shared-orchestration
contract with the API.

Offline and deterministic: ``main()`` is invoked in-process with injected
stdout and an isolated SQLite database under ``data/tmp`` (D:) - no
subprocess, no server, no model, no network.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rag_platform import api as api_module
from rag_platform import cli as cli_module
from rag_platform.config import PROJECT_ROOT, Settings
from rag_platform.db import initialize_database
from rag_platform.ingestion.injection import scan_text
from rag_platform.ingestion.store import ChunkInput, CorpusStore
from rag_platform.main import create_app

_WALRUS_DOC = "The walrus swims slowly past the ice floe."
_QUERY = "walrus"


def _settings(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {"_env_file": None}
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[arg-type]


@pytest.fixture
def corpus_db(storage_tmp: Path) -> Iterator[sqlite3.Connection]:
    conn = initialize_database(storage_tmp / "corpus.db")
    try:
        yield conn
    finally:
        conn.close()


def _ingest(conn: sqlite3.Connection, rel_path: str, text: str) -> None:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    CorpusStore(conn).upsert_document(
        source_rel_path=rel_path,
        source_name=Path(rel_path).name,
        file_format="txt",
        content_sha256=digest,
        size_bytes=len(text.encode("utf-8")),
        source_mtime="2026-10-19T12:00:00Z",
        declared_date=None,
        declared_date_status="missing",
        ingested_at="2026-10-19T12:00:00Z",
        chunks=[
            ChunkInput(
                ordinal=0,
                content=text,
                char_start=0,
                char_end=len(text),
                scan=scan_text(text),
            )
        ],
    )


def _run_cli(argv: list[str], settings: Settings | None = None) -> tuple[int, str]:
    stdout = io.StringIO()
    code = cli_module.main(
        argv, settings=settings if settings is not None else _settings(), stdout=stdout
    )
    return code, stdout.getvalue()


# --- help -----------------------------------------------------------------


def test_help_exits_zero_and_documents_invocation(
    capsys: pytest.CaptureFixture,
) -> None:
    code = cli_module.main(["--help"])
    captured = capsys.readouterr()
    assert code == 0
    assert "usage:" in captured.out
    assert "query" in captured.out
    assert "report" in captured.out


def test_query_help_exits_zero(capsys: pytest.CaptureFixture) -> None:
    code = cli_module.main(["query", "--help"])
    captured = capsys.readouterr()
    assert code == 0
    assert "usage:" in captured.out


def test_no_arguments_is_usage_error_exit_2(capsys: pytest.CaptureFixture) -> None:
    code = cli_module.main([])
    captured = capsys.readouterr()
    assert code == 2
    assert "usage:" in captured.err


# --- valid query and retrieval-only reporting ----------------------------


def test_valid_query_reports_retrieval_only_with_exit_zero(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    db = storage_tmp / "corpus.db"
    code, out = _run_cli(["--db", str(db), "query", _QUERY])
    assert code == 0  # retrieval-only is a truthful outcome, not an error
    assert "retrieval_mode: lexical_only" in out
    assert "retrieval_outcome: ok" in out
    assert "generation_outcome: unavailable" in out
    assert "generation_failure: disabled" in out
    assert "passages_served: 1" in out
    # Bounded default output: provenance ids, never a content dump.
    assert _WALRUS_DOC not in out
    assert "passage: rank 1" in out
    assert "source a.txt" in out


def test_json_output_is_the_canonical_response(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    db = storage_tmp / "corpus.db"
    code, out = _run_cli(["--db", str(db), "query", "--json", _QUERY])
    assert code == 0
    payload = json.loads(out)
    assert set(payload) == {
        "request_id",
        "retrieval",
        "passages",
        "generation",
        "abstention",
        "citations",
    }
    assert payload["generation"]["outcome"] == "unavailable"
    assert payload["abstention"]["detail"] == "config:disabled"
    assert payload["passages"] == payload["retrieval"]["results"]
    assert payload["passages"][0]["content"] == _WALRUS_DOC  # explicit --json


def test_empty_corpus_query_reports_abstention_with_exit_zero(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    db = storage_tmp / "corpus.db"
    code, out = _run_cli(["--db", str(db), "query", _QUERY])
    assert code == 0
    assert "retrieval_outcome: no_candidates" in out
    assert "abstention_reason: no_candidates" in out


# --- invalid input and failure exit codes --------------------------------


def test_empty_and_whitespace_queries_exit_2(
    corpus_db: sqlite3.Connection, storage_tmp: Path, capsys: pytest.CaptureFixture
) -> None:
    db = str(storage_tmp / "corpus.db")
    for candidate in ("", "   "):
        code, out = _run_cli(["--db", db, "query", candidate])
        captured = capsys.readouterr()
        assert code == 2
        assert out == ""
        assert "query must not be empty" in captured.err


def test_oversized_query_exits_2_without_echoing(
    corpus_db: sqlite3.Connection, storage_tmp: Path, capsys: pytest.CaptureFixture
) -> None:
    sentinel = "Z" * 600  # over the default 512-char retrieval cap
    code, out = _run_cli(["--db", str(storage_tmp / "corpus.db"), "query", sentinel])
    captured = capsys.readouterr()
    assert code == 2
    assert out == ""
    assert "configured maximum length" in captured.err
    assert "Z" * 10 not in captured.err  # the prompt is never echoed


def test_unrecoverable_storage_failure_exits_3_without_paths(
    storage_tmp: Path, capsys: pytest.CaptureFixture
) -> None:
    blocked = storage_tmp / "blocked"
    blocked.mkdir()
    code, out = _run_cli(["--db", str(blocked), "query", _QUERY])
    captured = capsys.readouterr()
    assert code == 3
    assert out == ""
    assert "storage unavailable" in captured.err
    assert str(PROJECT_ROOT) not in captured.err  # no paths on failures
    assert str(blocked) not in captured.err
    assert "Traceback" not in captured.err


def test_report_on_uninitialized_storage_exits_3(storage_tmp: Path) -> None:
    missing = storage_tmp / "empty" / "missing.db"  # parent missing entirely
    code, out = _run_cli(["--db", str(missing), "report", "runs"])
    assert code == 3
    assert out == ""


def test_default_db_path_is_project_local() -> None:
    path = api_module.default_db_path(_settings())
    assert path == PROJECT_ROOT / "data" / "sqlite" / "rag.db"
    assert path.is_relative_to(PROJECT_ROOT)


# --- report skeleton ------------------------------------------------------


def test_report_runs_renders_recorded_traces(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    db = storage_tmp / "corpus.db"
    code, _ = _run_cli(["--db", str(db), "query", _QUERY])
    assert code == 0
    report_code, out = _run_cli(["--db", str(db), "report", "runs"])
    assert report_code == 0
    payload = json.loads(out)
    assert payload["count"] == 1
    (trace,) = payload["traces"]
    assert trace["served_by"] == "retrieval_only"
    assert trace["degradation_reason"] == "disabled"
    assert trace["cache_status"] == "not_implemented"
    assert len(trace["request_id"]) == 16


def test_report_failures_renders_one_run_s_stages(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    db = storage_tmp / "corpus.db"
    _, out = _run_cli(["--db", str(db), "query", "--json", _QUERY])
    request_id = json.loads(out)["request_id"]
    code, rendered = _run_cli(
        ["--db", str(db), "report", "failures", "--run", request_id]
    )
    assert code == 0
    payload = json.loads(rendered)
    assert payload["request_id"] == request_id
    assert [stage["stage"] for stage in payload["stages"]] == [
        "retrieval",
        "generation",
        "response",
    ]
    assert "skeleton" in payload["note"]  # honest scope statement


def test_report_failures_unknown_run_exits_2(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    code, _ = _run_cli(
        [
            "--db",
            str(storage_tmp / "corpus.db"),
            "report",
            "failures",
            "--run",
            "f" * 16,
        ]
    )
    assert code == 2


def test_report_runs_rejects_unbounded_limit(storage_tmp: Path) -> None:
    code, _ = _run_cli(
        ["--db", str(storage_tmp / "corpus.db"), "report", "runs", "--limit", "0"]
    )
    assert code == 2


# --- API and CLI share one orchestration ---------------------------------


def test_cli_and_api_import_the_same_orchestration_symbol() -> None:
    assert cli_module.run_query is api_module.run_query
    assert cli_module.QueryResponse is api_module.QueryResponse


def test_api_and_cli_produce_the_same_canonical_result(
    corpus_db: sqlite3.Connection, storage_tmp: Path
) -> None:
    _ingest(corpus_db, "a.txt", _WALRUS_DOC)
    db = storage_tmp / "corpus.db"
    api_body = (
        TestClient(create_app(_settings(), db_path=db))
        .post("/query", json={"query": _QUERY})
        .json()
    )
    cli_code, cli_out = _run_cli(["--db", str(db), "query", "--json", _QUERY])
    assert cli_code == 0
    cli_body = json.loads(cli_out)
    assert {k: v for k, v in api_body.items() if k != "request_id"} == {
        k: v for k, v in cli_body.items() if k != "request_id"
    }
