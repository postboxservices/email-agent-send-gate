"""The suppression list: addresses (or '@domain' entries) we never email again."""

import json
import sqlite3
from datetime import datetime

from db import iso


def suppress(conn: sqlite3.Connection, entry: str, reason: str, source: str, now: datetime) -> bool:
    """Add an entry; return True if it was new. The first reason recorded is kept."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO suppression (entry, reason, source, created_at) VALUES (?, ?, ?, ?)",
        (entry.lower(), reason, source, iso(now)),
    )
    return cur.rowcount == 1


def is_suppressed(conn: sqlite3.Connection, address: str) -> bool:
    domain = address.rpartition("@")[2].lower()
    row = conn.execute(
        "SELECT 1 FROM suppression WHERE entry IN (?, ?)", (address.lower(), "@" + domain)
    ).fetchone()
    return row is not None


def record_event(conn: sqlite3.Connection, kind: str, address: str, send_id: str | None,
                 detail: dict, now: datetime) -> None:
    conn.execute(
        "INSERT INTO events (created_at, kind, address, send_id, detail) VALUES (?, ?, ?, ?, ?)",
        (iso(now), kind, address.lower(), send_id, json.dumps(detail)),
    )
