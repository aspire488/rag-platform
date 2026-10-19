"""Phase 2 SQLite storage tests (plan v2.4 section 15 Phase 2; D5, I9, Z9).

Deterministic, offline, tiny. Every database is a real file created inside
the project's ``data/tmp`` workspace (fixture-asserted to be inside
``D:\\rag-platform``), never in the system temp directory, never outside the
project root. No network, no models, no services; connections are always
closed and only fixture-owned artifacts are removed.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest

from rag_platform.config import PROJECT_ROOT
from rag_platform.db import (
    MIGRATIONS,
    SCHEMA_VERSION,
    Migration,
    MigrationError,
    PathContainmentError,
    StorageError,
    UnsupportedSchemaVersionError,
    fts5_available,
    initialize,
    initialize_database,
    open_db,
    resolve_under_project,
    user_version,
)

_EXPECTED_TABLES = {"chunk_fts", "traces", "trace_stages", "trace_authz"}

_PROBE_MIGRATION = Migration(2, "probe_table", "CREATE TABLE probe_v2 (x INTEGER);")

_TRACE_COLUMNS = {
    "traces": {
        "id",
        "request_id",
        "created_at",
        "tenant_id",
        "served_by",
        "degradation_reason",
        "cache_status",
    },
    "trace_stages": {
        "id",
        "trace_id",
        "stage",
        "outcome",
        "timing_ms",
        "inputs_digest",
        "outputs_digest",
        "scores_json",
        "excerpt",
    },
    "trace_authz": {"id", "trace_id", "doc_id", "decision"},
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in rows}


# --- fresh initialization / idempotence / version behaviour ---------------


def test_fresh_initialization_creates_schema_and_version(storage_tmp: Path) -> None:
    db_path = storage_tmp / "fresh.db"
    assert SCHEMA_VERSION == 1
    with closing(initialize_database(db_path)) as conn:
        assert db_path.exists()
        assert db_path.is_relative_to(PROJECT_ROOT)
        assert db_path.drive == PROJECT_ROOT.drive == "D:"
        assert user_version(conn) == 1
        assert _EXPECTED_TABLES <= _tables(conn)
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL


def test_repeated_initialization_is_idempotent(storage_tmp: Path) -> None:
    db_path = storage_tmp / "idempotent.db"
    with closing(initialize_database(db_path)) as conn:
        baseline = _tables(conn)
        assert initialize(conn) == 1
        assert initialize(conn) == 1
        assert user_version(conn) == 1
        assert _tables(conn) == baseline
    with closing(initialize_database(db_path)) as conn:  # reopen from disk
        assert user_version(conn) == 1
        assert _tables(conn) == baseline


def test_unsupported_future_version_rejected(storage_tmp: Path) -> None:
    with closing(open_db(storage_tmp / "future.db")) as conn:
        conn.execute("PRAGMA user_version = 99")
        with pytest.raises(UnsupportedSchemaVersionError, match="newer than supported"):
            initialize(conn)
        assert user_version(conn) == 99
        assert _tables(conn) == set()  # nothing created, nothing dropped


def test_failed_baseline_migration_rolls_back_completely(storage_tmp: Path) -> None:
    with closing(open_db(storage_tmp / "conflict.db")) as conn:
        # Half-initialized/corrupt state: schema objects exist while
        # user_version is still 0. Baseline DDL is plain CREATE, so the
        # conflict must fail loudly - never silently adopt unknown state.
        conn.execute("CREATE TABLE traces (id INTEGER PRIMARY KEY)")
        with pytest.raises(MigrationError, match="rolled back"):
            initialize(conn)
        assert user_version(conn) == 0
        tables = _tables(conn)
        assert "chunk_fts" not in tables  # partial DDL from the failed run gone
        assert "trace_stages" not in tables
        assert "traces" in tables  # pre-existing stray object untouched


def test_existing_v1_upgrades_without_destructive_reset(storage_tmp: Path) -> None:
    db_path = storage_tmp / "upgrade.db"
    with closing(initialize_database(db_path)) as conn:
        conn.execute(
            "INSERT INTO traces (request_id, created_at) VALUES (?, ?)",
            ("r-1", "2026-10-19T00:00:00Z"),
        )
        extended = MIGRATIONS + (_PROBE_MIGRATION,)
        assert initialize(conn, migrations=extended) == 2
        assert user_version(conn) == 2
        assert "probe_v2" in _tables(conn)
        row = conn.execute("SELECT request_id FROM traces WHERE id = 1").fetchone()
        assert row["request_id"] == "r-1"  # v1 data survived the upgrade
        assert _EXPECTED_TABLES <= _tables(conn)  # v1 objects intact
        # The default registry supports v1 only: it refuses a v2 database.
        with pytest.raises(UnsupportedSchemaVersionError):
            initialize(conn)


def test_fresh_database_with_full_registry_sequence(storage_tmp: Path) -> None:
    extended = MIGRATIONS + (_PROBE_MIGRATION,)
    with closing(
        initialize_database(storage_tmp / "fresh2.db", migrations=extended)
    ) as conn:
        assert user_version(conn) == 2
        assert _EXPECTED_TABLES | {"probe_v2"} <= _tables(conn)


def test_invalid_migration_registry_rejected_before_any_statement(
    storage_tmp: Path,
) -> None:
    with closing(open_db(storage_tmp / "bad_registry.db")) as conn:
        with pytest.raises(MigrationError, match="contiguous"):
            initialize(conn, migrations=(_PROBE_MIGRATION,))  # starts at v2
        with pytest.raises(MigrationError, match="empty"):
            initialize(conn, migrations=())
        with pytest.raises(MigrationError, match="no SQL"):
            initialize(conn, migrations=(Migration(1, "blank", "   "),))
        assert user_version(conn) == 0
        assert _tables(conn) == set()  # rejected before any statement ran


def test_initialize_rejects_open_transaction(storage_tmp: Path) -> None:
    with closing(initialize_database(storage_tmp / "txn.db")) as conn:
        conn.execute("BEGIN")
        with pytest.raises(MigrationError, match="no open transaction"):
            initialize(conn)
        conn.execute("ROLLBACK")


# --- FTS5 ------------------------------------------------------------------


def test_fts5_available_and_basic_indexed_search(storage_tmp: Path) -> None:
    assert fts5_available() is True
    with closing(initialize_database(storage_tmp / "fts.db")) as conn:
        rows = [
            ("c1", "d1", "A Walrus swims slowly past the ice floe"),
            ("c2", "d2", "walrus walrus appears twice in this sentence about a walrus"),
            ("c3", "d3", "unrelated content with no match on other words"),
        ]
        conn.executemany(
            "INSERT INTO chunk_fts (chunk_id, doc_id, content) VALUES (?, ?, ?)",
            rows,
        )
        hits = conn.execute(
            "SELECT chunk_id FROM chunk_fts WHERE chunk_fts MATCH ? ORDER BY rank",
            ("walrus",),
        ).fetchall()
        hit_ids = [row["chunk_id"] for row in hits]
        assert set(hit_ids) == {"c1", "c2"}  # unicode61 lowercases the query match
        assert hit_ids[0] == "c2"  # BM25 ranks the denser occurrence first
        assert (
            conn.execute(
                "SELECT chunk_id FROM chunk_fts WHERE chunk_fts MATCH ?", ("elephant",)
            ).fetchall()
            == []
        )


# --- trace tables: columns, constraints, cascade ---------------------------


def test_trace_tables_expected_columns_and_foreign_keys(storage_tmp: Path) -> None:
    with closing(initialize_database(storage_tmp / "trace_schema.db")) as conn:
        for table, expected in _TRACE_COLUMNS.items():
            actual = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            assert actual == expected, table
        for table in ("trace_stages", "trace_authz"):
            fks = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            assert [(fk["table"], fk["on_delete"]) for fk in fks] == [
                ("traces", "CASCADE")
            ], table


def test_trace_table_constraints_enforced(storage_tmp: Path) -> None:
    with closing(initialize_database(storage_tmp / "trace_constraints.db")) as conn:
        conn.execute("INSERT INTO traces (request_id, created_at) VALUES ('r-1', 't1')")
        with pytest.raises(sqlite3.IntegrityError):  # non-normative stage name
            conn.execute(
                "INSERT INTO trace_stages (trace_id, stage, outcome) "
                "VALUES (1, 'not_a_stage', 'ok')"
            )
        conn.execute(
            "INSERT INTO trace_stages (trace_id, stage, outcome) "
            "VALUES (1, 'authn', 'ok')"
        )
        with pytest.raises(sqlite3.IntegrityError):  # UNIQUE(trace_id, stage)
            conn.execute(
                "INSERT INTO trace_stages (trace_id, stage, outcome) "
                "VALUES (1, 'authn', 'ok')"
            )
        with pytest.raises(sqlite3.IntegrityError):  # I7 excerpt cap (<= 200 chars)
            conn.execute(
                "INSERT INTO trace_stages (trace_id, stage, outcome, excerpt) "
                "VALUES (1, 'retrieval', 'ok', ?)",
                ("x" * 201,),
            )
        conn.execute(  # 200 chars exactly is accepted
            "INSERT INTO trace_stages (trace_id, stage, outcome, excerpt) "
            "VALUES (1, 'retrieval', 'ok', ?)",
            ("x" * 200,),
        )
        with pytest.raises(sqlite3.IntegrityError):  # negative timing
            conn.execute(
                "INSERT INTO trace_stages (trace_id, stage, outcome, timing_ms) "
                "VALUES (1, 'cache_lookup', 'ok', -0.5)"
            )
        with pytest.raises(sqlite3.IntegrityError):  # dangling trace_id (FK on)
            conn.execute(
                "INSERT INTO trace_stages (trace_id, stage, outcome) "
                "VALUES (999, 'authn', 'ok')"
            )
        conn.execute(
            "INSERT INTO trace_authz (trace_id, doc_id, decision) "
            "VALUES (1, 'd1', 'allowed')"
        )
        with pytest.raises(sqlite3.IntegrityError):  # decision enum CHECK
            conn.execute(
                "INSERT INTO trace_authz (trace_id, doc_id, decision) "
                "VALUES (1, 'd2', 'maybe')"
            )


def test_trace_scores_roundtrip_and_cascade_on_trace_delete(storage_tmp: Path) -> None:
    with closing(initialize_database(storage_tmp / "trace_cascade.db")) as conn:
        trace_id = conn.execute(
            "INSERT INTO traces (request_id, created_at, tenant_id, served_by, "
            "degradation_reason, cache_status) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "r-9",
                "2026-10-19T12:00:00Z",
                "tenant-A",
                "retrieval-only",
                "no_generation_configured",
                "miss",
            ),
        ).lastrowid
        conn.execute(
            "INSERT INTO trace_stages (trace_id, stage, outcome, timing_ms, "
            "inputs_digest, outputs_digest, scores_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                trace_id,
                "retrieval",
                "ok",
                12.5,
                "in-digest",
                "out-digest",
                "[0.91, 0.72, 0.65]",
            ),
        )
        conn.execute(
            "INSERT INTO trace_authz (trace_id, doc_id, decision) VALUES (?, ?, ?)",
            (trace_id, "d1", "allowed"),
        )
        conn.execute(
            "INSERT INTO trace_authz (trace_id, doc_id, decision) VALUES (?, ?, ?)",
            (trace_id, "d2", "denied"),
        )
        row = conn.execute(
            "SELECT timing_ms, scores_json, inputs_digest FROM trace_stages "
            "WHERE trace_id = ? AND stage = 'retrieval'",
            (trace_id,),
        ).fetchone()
        assert row["timing_ms"] == 12.5
        assert row["scores_json"] == "[0.91, 0.72, 0.65]"
        assert row["inputs_digest"] == "in-digest"
        conn.execute("DELETE FROM traces WHERE id = ?", (trace_id,))
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM trace_stages").fetchone()["n"] == 0
        )  # ON DELETE CASCADE
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM trace_authz").fetchone()["n"] == 0
        )


# --- path containment and connection handling ------------------------------


def test_relative_path_resolves_against_project_root_not_cwd(storage_tmp: Path) -> None:
    elsewhere = storage_tmp / "elsewhere"
    elsewhere.mkdir()
    relative = (storage_tmp / "relative.db").relative_to(PROJECT_ROOT)
    # cwd restore happens in this test's own finally: fixture teardown must
    # never run while the process cwd is inside the directory it removes
    # (Windows cannot delete a process's cwd, and ignore_errors would hide it).
    previous_cwd = os.getcwd()
    try:
        os.chdir(elsewhere)  # cwd deliberately NOT the project root
        with closing(initialize_database(relative)) as conn:
            assert user_version(conn) == 1
        assert (PROJECT_ROOT / relative).exists()  # anchored at root, not cwd
        assert not (elsewhere / relative).exists()
    finally:
        os.chdir(previous_cwd)


def test_paths_outside_project_root_rejected_and_untouched(storage_tmp: Path) -> None:
    strays = [
        Path("D:/") / f"phase2_stray_{uuid4().hex}.db",  # on D: but outside project
        Path(r"C:\Windows") / f"phase2_stray_{uuid4().hex}.db",  # never on C:
        PROJECT_ROOT,  # the root itself is not an acceptable storage path
    ]
    for stray in strays:
        with pytest.raises(PathContainmentError):
            resolve_under_project(stray)
        with pytest.raises(PathContainmentError):
            open_db(stray)
    for stray in strays[:2]:
        assert not stray.exists()  # validation precedes any mkdir/connect


def test_open_db_rejects_directory(storage_tmp: Path) -> None:
    with pytest.raises(StorageError, match="directory"):
        open_db(storage_tmp)  # a directory is not a database file


def test_wal_and_shm_sidecars_stay_under_project_root(storage_tmp: Path) -> None:
    db_path = storage_tmp / "wal.db"
    conn = open_db(db_path)
    try:
        initialize(conn)
        conn.execute(
            "INSERT INTO traces (request_id, created_at) VALUES ('r-wal', 't1')"
        )
        names = {entry.name for entry in storage_tmp.iterdir()}
        assert "wal.db-wal" in names  # WAL sidecar co-located with the database
        for entry in storage_tmp.iterdir():
            assert entry.is_relative_to(PROJECT_ROOT)  # every artifact on D:
    finally:
        conn.close()
    names = {entry.name for entry in storage_tmp.iterdir()}
    assert names == {"wal.db"}  # clean close checkpointed -wal/-shm away
