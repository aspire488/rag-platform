# scripts\check_fts5.py - FTS5 availability check (plan v2.3 section 15 Phase 0, risk R20).
# stdlib-only; no network; no project code required.
# Called as a script file (not python -c) because Windows PowerShell 5.1 strips
# embedded double quotes from native-command arguments (R14 finding).
import sqlite3

c = sqlite3.connect(":memory:")
c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
c.execute("INSERT INTO t VALUES ('hello rag platform')")
row = c.execute("SELECT * FROM t WHERE t MATCH 'rag'").fetchone()
assert row is not None, "FTS5 match query returned no row"
print("FTS5 OK; sqlite", sqlite3.sqlite_version, "| match test:", row[0])
