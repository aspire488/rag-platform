"""SQLite storage package (plan v2.4 Phase 2): contained WAL connections and
versioned ``user_version`` migrations. Application startup never imports
initialization here - opening a database is always an explicit decision."""

from __future__ import annotations

from rag_platform.db.migrations import (
    MIGRATIONS,
    SCHEMA_VERSION,
    Migration,
    MigrationError,
    UnsupportedSchemaVersionError,
    initialize,
    initialize_database,
    user_version,
)
from rag_platform.db.sqlite import (
    PathContainmentError,
    StorageError,
    fts5_available,
    open_db,
    resolve_under_project,
)

__all__ = [
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "Migration",
    "MigrationError",
    "PathContainmentError",
    "StorageError",
    "UnsupportedSchemaVersionError",
    "fts5_available",
    "initialize",
    "initialize_database",
    "open_db",
    "resolve_under_project",
    "user_version",
]
