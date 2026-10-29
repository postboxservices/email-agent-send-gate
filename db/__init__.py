"""SQLite helpers and a tiny migration runner.

Each process owns its own database: the gate's (sends, suppression, events, selftest
results) and, later, the agent's (companies and contacts). The agent never opens the
gate's database; it can only ask the gate over HTTP.
"""

import sqlite3
import time
from datetime import UTC, datetime
from importlib import resources


def connect(path: str) -> sqlite3.Connection:
    # Autocommit mode: code that needs a transaction says BEGIN IMMEDIATE itself.
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    _use_wal(conn)
    return conn


def _use_wal(conn: sqlite3.Connection) -> None:
    # Switching a new file to WAL does not wait on the busy timeout, so two services
    # opening the same new database at once can see "database is locked". Retry briefly.
    for _ in range(50):
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e):
                raise
            time.sleep(0.1)
    conn.execute("PRAGMA journal_mode=WAL")


def _statements(sql: str):
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer
            buffer = ""


def migrate(conn: sqlite3.Connection, schema: str) -> int:
    """Apply db/migrations/<schema>/NNNN_*.sql files newer than PRAGMA user_version.

    The version is read under the write lock, so two services starting at once on the
    same database file cannot both apply the same migration.
    """
    folder = resources.files("db").joinpath("migrations", schema)
    files = sorted(f for f in folder.iterdir() if f.name.endswith(".sql"))
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for f in files:
            version = int(f.name.split("_", 1)[0])
            if version > current:
                for statement in _statements(f.read_text()):
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version={version}")
                current = version
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return current


def iso(dt: datetime) -> str:
    """Timestamps are stored as fixed-width UTC strings, so they compare as text."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def utcnow() -> datetime:
    return datetime.now(UTC)
