"""SQLite connection layer (plan v2.4 Phase 2; D5, I9, Z9 path policy).

Phase 2 scope - connection configuration and path containment only:

- All database files, including SQLite ``-wal``/``-shm`` sidecars, live under
  the project root on D: (plan section 10.2/11: ``data\\sqlite\\``). Relative
  paths resolve against PROJECT_ROOT, never the process working directory.
- Connections are configured deliberately: WAL journal mode (D5: SQLite
  WAL + FTS5 approved), enforced foreign keys, ``synchronous=NORMAL`` (the
  standard WAL pairing), and ``isolation_level=None`` so there are no
  implicit BEGINs - transactions are opened deliberately by callers and by
  the migration runner (auditable transaction behaviour).
- FTS5 availability is detected against the existing stdlib SQLite runtime
  (plan section 12, FTS5 row): a plain in-memory probe, no extension
  installs, no network, no project code beyond this check.

Opening or initializing a database is always an explicit caller decision:
this module is never invoked by application startup or ``/health`` (Phase 2
task section 6 - initialization stays separate from startup).

Path containment (Z9) is mirrored from :mod:`rag_platform.config`: a path
that resolves outside PROJECT_ROOT is rejected before any directory or file
is created.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from rag_platform.config import PROJECT_ROOT


class StorageError(Exception):
    """Base error for Phase 2 storage operations."""


class PathContainmentError(StorageError):
    """A storage path resolved outside the project root (Z9)."""


def resolve_under_project(path: str | Path) -> Path:
    """Resolve *path* against PROJECT_ROOT and require strict containment.

    Relative inputs are anchored at PROJECT_ROOT (never the cwd). The project
    root itself is not an acceptable storage location - only paths strictly
    below it. Validation happens before any mkdir/connect, so a rejected path
    leaves no artifacts behind.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(PROJECT_ROOT) or resolved == PROJECT_ROOT:
        raise PathContainmentError(
            f"storage path must resolve strictly under {PROJECT_ROOT}; got {resolved}"
        )
    return resolved


def open_db(path: str | Path, *, timeout: float = 5.0) -> sqlite3.Connection:
    """Open a contained SQLite connection with the Phase 2 pragmas applied.

    Creates the parent directory (which must itself be inside the project
    root). Failures raise :class:`StorageError` (or ``sqlite3.Error`` from
    the driver) with no half-open connection returned: pragma failures close
    the connection before raising so no leaked handle survives an error.
    """
    resolved = resolve_under_project(path)
    if resolved.is_dir():
        raise StorageError(f"database path is a directory: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(resolved), timeout=timeout, isolation_level=None)
    except sqlite3.Error as exc:  # e.g. unopenable path - fail loud, no retry
        raise StorageError(f"failed to open database at {resolved}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    try:
        journal = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(journal).lower() != "wal":
            raise StorageError(
                f"WAL journal mode unavailable at {resolved} (got {journal!r})"
            )
        # The setters foreign_keys/synchronous return no rows - set first,
        # then read back via the query form to verify the setting took.
        conn.execute("PRAGMA foreign_keys=ON")
        if int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise StorageError("foreign key enforcement could not be enabled")
        conn.execute("PRAGMA synchronous=NORMAL")
        synchronous = int(conn.execute("PRAGMA synchronous").fetchone()[0])
        if synchronous != 1:  # 1 == NORMAL
            raise StorageError(f"expected synchronous=NORMAL, got {synchronous!r}")
    except BaseException:
        conn.close()  # never leak a handle from a failed initialization
        raise
    return conn


def fts5_available() -> bool:
    """True when the runtime SQLite build can create FTS5 virtual tables.

    Mirrors the plan section 12 Phase 0 check, against the same stdlib
    ``sqlite3`` the application uses. No project database is touched.
    """
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE probe_fts USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        probe.close()
