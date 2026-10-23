"""Versioned schema migrations over ``PRAGMA user_version`` (plan v2.4
Phase 2; section 6 "versioned SQL migrations", section 11 ``schema.sql``).

Mechanism - deterministic, repeatable, auditable:

- The DDL for schema v1 lives in ``schema.sql`` next to this module (the
  file name the plan's directory structure specifies) and is registered as
  migration v1. Future migrations append to :data:`MIGRATIONS` in version
  order; nothing is ever edited in place.
- ``PRAGMA user_version`` is the single version marker. It is written
  **inside the same transaction** as its migration's DDL, so version and
  schema can never disagree after a failure.
- Each migration executes as one explicit ``BEGIN IMMEDIATE ... COMMIT``
  script (autocommit connection, no implicit transactions). Any statement
  failure rolls the whole migration back: ``user_version`` is unchanged and
  no partial objects remain (:class:`MigrationError`).
- A database whose ``user_version`` exceeds the supported set fails loudly
  with :class:`UnsupportedSchemaVersionError` - never a destructive reset,
  never a silent downgrade (Phase 2 task sections 3.5/3.6).
- Running :func:`initialize` on an up-to-date database is a no-op
  (idempotent); a registry that is not contiguous from v1 is rejected before
  a single statement runs (invalid migration state).
- Partial/half-initialized files (schema objects present while
  ``user_version`` is still 0) fail loudly: baseline DDL uses plain
  ``CREATE`` statements, so conflicts surface as :class:`MigrationError`
  instead of silently adopting unknown state.

Migration SQL is trusted code (reviewed, committed): it must not contain
transaction-control statements - the runner owns BEGIN/COMMIT/ROLLBACK.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rag_platform.db.sqlite import StorageError, open_db


class MigrationError(StorageError):
    """A migration failed (and was rolled back) or the registry is invalid."""


class UnsupportedSchemaVersionError(MigrationError):
    """The database reports a schema version newer than this code supports."""


@dataclass(frozen=True)
class Migration:
    """One schema migration: strictly increasing versions starting at 1."""

    version: int
    name: str
    sql: str


_SCHEMA_SQL_PATH = Path(__file__).with_name("schema.sql")
_MIGRATION_V2_SQL_PATH = Path(__file__).with_name("migration_v2_ingestion.sql")


def _load_migration_sql(path: Path, label: str) -> str:
    sql = path.read_text(encoding="utf-8")
    if not sql.strip():
        raise MigrationError(f"{label} schema file is empty: {path}")
    return sql


#: The registry this code supports. v1 is the Phase 2 baseline; v2 adds the
#: Phase 4 ingestion corpus tables (documents/chunks). Appended in version
#: order - nothing historical is edited in place.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "baseline_schema", _load_migration_sql(_SCHEMA_SQL_PATH, "baseline")),
    Migration(
        2,
        "ingestion_corpus",
        _load_migration_sql(_MIGRATION_V2_SQL_PATH, "migration v2"),
    ),
)

#: Version of the newest migration in :data:`MIGRATIONS`.
SCHEMA_VERSION = MIGRATIONS[-1].version


def _validate_registry(registry: Sequence[Migration]) -> None:
    if not registry:
        raise MigrationError("migration registry is empty")
    for expected, migration in enumerate(registry, start=1):
        if migration.version != expected:
            raise MigrationError(
                "migration registry must be contiguous from v1; "
                f"expected v{expected}, got v{migration.version} ({migration.name})"
            )
        if not migration.sql.strip():
            raise MigrationError(
                f"migration v{migration.version} ({migration.name}) contains no SQL"
            )


def user_version(conn: sqlite3.Connection) -> int:
    """Current ``PRAGMA user_version`` of *conn* as ``int``."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def initialize(
    conn: sqlite3.Connection,
    *,
    migrations: Sequence[Migration] | None = None,
) -> int:
    """Bring *conn* up to the newest supported schema version; return it.

    With *migrations* omitted the shipped :data:`MIGRATIONS` registry is
    used; passing an explicit registry is the seam used by the migration
    tests to exercise sequencing and failure rollback.

    Raises :class:`UnsupportedSchemaVersionError` when the database is newer
    than the registry, and :class:`MigrationError` when a step fails or the
    registry/state is invalid. Requires a connection with no open
    transaction (the runner owns transaction control).
    """
    registry = tuple(migrations) if migrations is not None else MIGRATIONS
    _validate_registry(registry)
    if conn.in_transaction:
        raise MigrationError(
            "initialize() requires a connection with no open transaction"
        )
    current = user_version(conn)
    supported = registry[-1].version
    if current > supported:
        raise UnsupportedSchemaVersionError(
            f"database schema v{current} is newer than supported v{supported}; "
            "refusing to modify it (no downgrade, no reset)"
        )
    for migration in registry[current:]:
        script = (
            "BEGIN IMMEDIATE;\n"
            f"{migration.sql}\n"
            f"PRAGMA user_version = {migration.version};\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(
                f"migration v{migration.version} ({migration.name}) failed and was "
                f"rolled back; user_version remains {user_version(conn)}"
            ) from exc
    return user_version(conn)


def initialize_database(
    path: str | Path,
    *,
    migrations: Sequence[Migration] | None = None,
) -> sqlite3.Connection:
    """Open *path* (contained, WAL pragmas) and migrate it; return the open connection.

    The caller owns the connection and must close it. On migration failure
    the connection is closed before the error propagates - a failed
    initialization leaves no open handle and no partial schema state.
    """
    conn = open_db(path)
    try:
        initialize(conn, migrations=migrations)
    except BaseException:  # includes KeyboardInterrupt - never leak the handle
        conn.close()
        raise
    return conn
