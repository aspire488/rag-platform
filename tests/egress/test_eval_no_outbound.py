"""Phase 8 eval under the socket guard (plan v2.4 sections 10.1, 12, 15).

Proves, on the generation-disabled evaluation path itself:

- a full harness run (and the CLI ``eval`` command) performs ZERO socket
  operations of any kind - not merely zero non-loopback ones;
- no model runtime (``sentence_transformers``/``torch``) is imported by
  an eval run (the lazy MiniLM loader is never triggered);
- a tampered corpus is refused BEFORE ingestion: no run database, no
  queries, no sockets;
- the deterministic report section is byte-stable across two runs while
  both runs still open no sockets;
- no question/answer/passage text or absolute path leaks into traces,
  run metadata, or either report format.

Offline by construction: pure in-process SQLite + filesystem under
``data/tmp`` (D:). No ``TestClient`` here - the eval path never enters an
event loop, so the total guard can be installed for the whole run.
"""

from __future__ import annotations

import json
import socket
import sqlite3
import sys
from pathlib import Path

import pytest

from rag_platform import cli as cli_module
from rag_platform.config import PROJECT_ROOT, Settings
from rag_platform.eval import build_report, run_eval
from rag_platform.eval.dataset import DatasetIntegrityError
from rag_platform.eval.report import render_markdown
from tests.eval.conftest import BuiltDataset, build_dataset

# The model runtimes (plan: no model loads - Ollama/MiniLM stay out).
# Note: ``qdrant_client`` is NOT in this list - it is the local vector
# store client the app itself imports eagerly (socketless at import) via
# api -> retrieval -> dense -> vectorstore; its presence says nothing
# about model loading. ``sentence_transformers``/``torch`` enter
# sys.modules only through the lazy MiniLM loader, which an eval run must
# never trigger.
_MODEL_RUNTIMES = ("sentence_transformers", "torch")

_SECRETS = (
    "Willow Creek",
    "apple harvest",
    "Zzqxw",
    "banana shipment",
    "mango harvest",
    "42 bushels",
    "17 crates",
)


def _install_total_guard(monkeypatch: pytest.MonkeyPatch) -> tuple[list, list]:
    """Record EVERY ``connect()``/``getaddrinfo()`` - loopback included."""
    connects: list = []
    dns: list = []
    original_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self: object, address: object) -> object:
        connects.append(address)
        return original_connect(self, address)  # type: ignore[arg-type]

    def guarded_getaddrinfo(
        host: object, port: object, *args: object, **kwargs: object
    ) -> object:
        dns.append(host)
        return original_getaddrinfo(host, port, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    return connects, dns


def _trace_dump(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        rows: list = []
        for table in ("traces", "trace_stages", "trace_authz"):
            rows.extend(conn.execute(f"SELECT * FROM {table}").fetchall())  # noqa: S608
        return json.dumps([list(row) for row in rows])
    finally:
        conn.close()


@pytest.fixture
def eval_dataset(storage_tmp: Path) -> BuiltDataset:
    """Same fixture dataset as ``tests/eval`` (pytest fixtures are
    directory-scoped, so the egress subtree defines its own handle)."""
    settings = Settings(_env_file=None, data_dir=storage_tmp / "data")
    return build_dataset(storage_tmp / "dataset", settings=settings)


# --- zero sockets on the eval path -------------------------------------------


def test_generation_disabled_eval_run_opens_zero_sockets(
    eval_dataset: BuiltDataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    connects, dns = _install_total_guard(monkeypatch)
    assert eval_dataset.settings.local_generation_enabled is False

    result = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)

    # Not merely non-loopback: the eval path attempts nothing, anywhere.
    assert connects == []
    assert dns == []
    # The run really executed (metrics measured, not stubbed out).
    assert result.question_count == 4
    assert result.metrics.recall_at_10.status == "measured"
    assert result.config_labels["generation_enabled"] == "false"


def test_eval_cli_command_opens_zero_sockets(
    eval_dataset: BuiltDataset, storage_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    connects, dns = _install_total_guard(monkeypatch)
    stdout = io.StringIO()
    code = cli_module.main(
        [
            "eval",
            "--dataset",
            str(eval_dataset.root),
            "--out",
            str(storage_tmp / "reports"),
        ],
        settings=eval_dataset.settings,
        stdout=stdout,
    )
    assert code == 0
    assert connects == []
    assert dns == []
    assert json.loads(stdout.getvalue())["questions"] == 4


def test_eval_run_imports_no_model_runtime(
    eval_dataset: BuiltDataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The whole suite loads no model runtime; an eval run must not be the
    # one that breaks that (no new imports, no first imports).
    assert not (set(_MODEL_RUNTIMES) & set(sys.modules)), (
        "a model runtime was imported before the eval ran"
    )
    run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    assert not (set(_MODEL_RUNTIMES) & set(sys.modules)), (
        "an eval run imported a model runtime"
    )


# --- fail closed before ingestion --------------------------------------------


def test_tampered_corpus_refused_before_ingestion_queries_or_sockets(
    eval_dataset: BuiltDataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = eval_dataset.root / "documents" / "orchard.txt"
    raw = bytearray(target.read_bytes())
    raw[9] ^= 0x20
    target.write_bytes(bytes(raw))

    eval_runs = eval_dataset.settings.data_dir / "eval_runs"
    before = sorted(p.name for p in eval_runs.glob("*")) if eval_runs.is_dir() else []
    connects, dns = _install_total_guard(monkeypatch)

    with pytest.raises(DatasetIntegrityError, match="sha256 mismatch"):
        run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)

    after = sorted(p.name for p in eval_runs.glob("*")) if eval_runs.is_dir() else []
    assert after == before  # refused before ingestion: no run database
    assert connects == []
    assert dns == []


# --- determinism proven under the guard --------------------------------------


def test_two_runs_under_the_guard_are_socketless_and_report_identical(
    eval_dataset: BuiltDataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    connects, dns = _install_total_guard(monkeypatch)

    run1 = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    run2 = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)

    assert connects == []
    assert dns == []
    # Deterministic section: identical bytes for identical inputs - while
    # run identity stays isolated in volatile.
    report1 = build_report(run1)
    report2 = build_report(run2)
    assert report1["deterministic"] == report2["deterministic"]
    assert report1["volatile"]["run_id"] != report2["volatile"]["run_id"]


# --- no leakage in traces or either report format ----------------------------


def test_no_question_path_or_absolute_leak_in_traces_and_reports(
    eval_dataset: BuiltDataset, storage_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connects, dns = _install_total_guard(monkeypatch)
    result = run_eval(eval_dataset.settings, dataset_root=eval_dataset.root)
    assert connects == [] and dns == []

    report = build_report(result)
    json_text = json.dumps(report, indent=2, sort_keys=True)
    md_text = render_markdown(result, report)
    trace_text = _trace_dump(result.run_db_path)
    run_meta = (result.run_db_path.parent / "run.json").read_text(encoding="utf-8")

    for output in (json_text, md_text, trace_text, run_meta):
        for secret in _SECRETS:
            assert secret not in output, f"leaked {secret!r}"
        assert str(PROJECT_ROOT) not in output
        assert PROJECT_ROOT.as_posix() not in output

    # Trace stages: excerpts stay NULL, trace_authz stays empty.
    conn = sqlite3.connect(result.run_db_path)
    try:
        excerpts = conn.execute(
            "SELECT COUNT(*) FROM trace_stages WHERE excerpt IS NOT NULL"
        ).fetchone()[0]
        authz = conn.execute("SELECT COUNT(*) FROM trace_authz").fetchone()[0]
    finally:
        conn.close()
    assert excerpts == 0
    assert authz == 0
