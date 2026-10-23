"""Migration v2 tests (plan v2.4 Phase 4: corpus tables arrive as a new
versioned migration; historical migrations are never edited in place).

Offline, deterministic, tiny; every database lives inside the project's
``data/tmp`` workspace. Verifies fresh v2 creation, the v1 -> v2 upgrade
path with data preservation, schema constraints, and cascade behavior.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from rag_platform.db import (
    MIGRATIONS,
    SCHEMA_VERSION,
    initialize,
    initialize_database,
    open_db,
    user_version,
)

_DOCUMENT_COLUMNS = {
    "doc_id",
    "source_path",
    "source_name",
    "format",
    "content_sha256",
    "size_bytes",
    "source_mtime",
    "declared_date",
    "declared_date_status",
    "ingested_at",
    "chunk_count",
}

_CHUNK_COLUMNS = {
    "chunk_id",
    "doc_id",
    "ordinal",
    "content",
    "char_start",
    "char_end",
    "injection_quarantined",
    "injection_findings",
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _insert_doc_chunk(conn: sqlite3.Connection, doc_id: str, chunk_id: str) -> None:
    conn.execute(
        "INSERT INTO documents (doc_id, source_path, source_name, format, "
        "content_sha256, size_bytes, source_mtime, declared_date, "
        "declared_date_status, ingested_at, chunk_count) "
        "VALUES (?, ?, 'a.txt', 'txt', 'sha', 10, NULL, NULL, 'missing', 't', 1)",
        (doc_id, f"corpus/{doc_id}.txt"),
    )
    conn.execute(
        "INSERT INTO chunks (chunk_id, doc_id, ordinal, content, char_start, "
        "char_end, injection_quarantined, injection_findings) "
        "VALUES (?, ?, 0, 'hello', 0, 5, 0, NULL)",
        (chunk_id, doc_id),
    )


def test_fresh_database_is_v2_with_corpus_tables(storage_tmp: Path) -> None:
    assert SCHEMA_VERSION == 2
    with closing(initialize_database(storage_tmp / "v2.db")) as conn:
        assert user_version(conn) == 2
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"documents", "chunks"} <= tables
        assert _columns(conn, "documents") == _DOCUMENT_COLUMNS
        assert _columns(conn, "chunks") == _CHUNK_COLUMNS
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert "chunks_doc_id_idx" in indexes


def test_v1_to_v2_upgrade_preserves_v1_data(storage_tmp: Path) -> None:
    db_path = storage_tmp / "upgrade.db"
    with closing(open_db(db_path)) as conn:
        initialize(conn, migrations=MIGRATIONS[:1])  # Phase 2 baseline only
        assert user_version(conn) == 1
        conn.execute(
            "INSERT INTO traces (request_id, created_at) VALUES ('keep-me', 't1')"
        )
        conn.execute(
            "INSERT INTO chunk_fts (chunk_id, doc_id, content) VALUES ('c1', 'd1', 'walrus')"
        )
        assert initialize(conn) == 2  # upgrade to the shipped registry
        assert user_version(conn) == 2
        assert (
            conn.execute("SELECT request_id FROM traces WHERE id = 1").fetchone()[
                "request_id"
            ]
            == "keep-me"
        )
        assert (  # FTS rows survive too
            conn.execute("SELECT COUNT(*) AS n FROM chunk_fts").fetchone()["n"] == 1
        )
        assert {"documents", "chunks"} <= {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def test_v2_constraints_are_enforced(storage_tmp: Path) -> None:
    with closing(initialize_database(storage_tmp / "constraints.db")) as conn:
        _insert_doc_chunk(conn, "doc-1", "chunk-1")
        with pytest.raises(sqlite3.IntegrityError):  # chunk content hard max 2000
            conn.execute(
                "INSERT INTO chunks (chunk_id, doc_id, ordinal, content, char_start, "
                "char_end, injection_quarantined) VALUES ('c-long', 'doc-1', 1, ?, 0, 1, 0)",
                ("x" * 2001,),
            )
        with pytest.raises(sqlite3.IntegrityError):  # negative char_start
            conn.execute(
                "INSERT INTO chunks (chunk_id, doc_id, ordinal, content, char_start, "
                "char_end, injection_quarantined) VALUES ('c-neg', 'doc-1', 1, 'ok', -1, 2, 0)"
            )
        with pytest.raises(sqlite3.IntegrityError):  # inverted offsets
            conn.execute(
                "INSERT INTO chunks (chunk_id, doc_id, ordinal, content, char_start, "
                "char_end, injection_quarantined) VALUES ('c-bad', 'doc-1', 1, 'ok', 5, 5, 0)"
            )
        with pytest.raises(sqlite3.IntegrityError):  # quarantine enum
            conn.execute(
                "INSERT INTO chunks (chunk_id, doc_id, ordinal, content, char_start, "
                "char_end, injection_quarantined) VALUES ('c-q', 'doc-1', 1, 'ok', 0, 2, 7)"
            )
        with pytest.raises(sqlite3.IntegrityError):  # UNIQUE(doc_id, ordinal)
            conn.execute(
                "INSERT INTO chunks (chunk_id, doc_id, ordinal, content, char_start, "
                "char_end, injection_quarantined) VALUES ('c-dup', 'doc-1', 0, 'ok', 0, 2, 0)"
            )
        with pytest.raises(sqlite3.IntegrityError):  # orphan chunk (FK)
            conn.execute(
                "INSERT INTO chunks (chunk_id, doc_id, ordinal, content, char_start, "
                "char_end, injection_quarantined) VALUES ('c-fk', 'missing', 0, 'ok', 0, 2, 0)"
            )
        with pytest.raises(sqlite3.IntegrityError):  # UNIQUE(source_path)
            conn.execute(
                "INSERT INTO documents (doc_id, source_path, source_name, format, "
                "content_sha256, size_bytes, declared_date_status, ingested_at, chunk_count) "
                "VALUES ('doc-2', 'corpus/doc-1.txt', 'a.txt', 'txt', 'sha', 10, "
                "'missing', 't', 1)"
            )
        with pytest.raises(sqlite3.IntegrityError):  # declared_date_status enum
            conn.execute(
                "INSERT INTO documents (doc_id, source_path, source_name, format, "
                "content_sha256, size_bytes, declared_date_status, ingested_at, chunk_count) "
                "VALUES ('doc-3', 'corpus/doc-3.txt', 'a.txt', 'txt', 'sha', 10, "
                "'weird', 't', 1)"
            )
        with pytest.raises(sqlite3.IntegrityError):  # documents need >= 1 chunk
            conn.execute(
                "INSERT INTO documents (doc_id, source_path, source_name, format, "
                "content_sha256, size_bytes, declared_date_status, ingested_at, chunk_count) "
                "VALUES ('doc-4', 'corpus/doc-4.txt', 'a.txt', 'txt', 'sha', 10, "
                "'missing', 't', 0)"
            )
        # The valid row and chunk are intact after all the rejections.
        assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == 1


def test_document_delete_cascades_to_chunks(storage_tmp: Path) -> None:
    with closing(initialize_database(storage_tmp / "cascade.db")) as conn:
        _insert_doc_chunk(conn, "doc-1", "chunk-1")
        conn.execute("DELETE FROM documents WHERE doc_id = 'doc-1'")
        assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == 0
