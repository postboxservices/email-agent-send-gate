"""The agent's database: companies, the signals found about them, and contacts."""

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

import db
from db import iso

SOURCE_TYPES = ("own_data", "apollo", "manual")


@dataclass
class Signal:
    kind: str  # a short label: hiring | funding | expansion | ...
    value: str  # a short value if the check has one, else ""
    summary: str  # one line a person (or the agent) can quote
    source_url: str  # where the fact came from
    detail: dict = field(default_factory=dict)


@dataclass
class ContactCandidate:
    email: str
    company_domain: str
    source_type: str
    source_url: str
    lawful_basis: str
    found_at: str = ""  # YYYY-MM-DD; today if empty
    company_name: str = ""
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    source_ref: str = ""
    source_note: str = ""
    lawful_basis_note: str = ""


def connect(path: str) -> sqlite3.Connection:
    conn = db.connect(path)
    db.migrate(conn, "agent")
    return conn


def upsert_company(conn: sqlite3.Connection, *, domain: str, name: str, source_type: str,
                   source_url: str, now: datetime, source_ref: str = "") -> int:
    domain = domain.lower().strip().removeprefix("www.")
    row = conn.execute("SELECT id, name FROM companies WHERE domain = ?", (domain,)).fetchone()
    if row:
        if name and not row["name"]:
            conn.execute("UPDATE companies SET name = ?, updated_at = ? WHERE id = ?",
                         (name, iso(now), row["id"]))
        return row["id"]
    cur = conn.execute(
        "INSERT INTO companies (domain, name, source_type, source_url, source_ref, found_at, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (domain, name, source_type, source_url, source_ref, now.date().isoformat(), iso(now), iso(now)),
    )
    return cur.lastrowid


def add_signal(conn: sqlite3.Connection, company_id: int, signal: Signal, now: datetime) -> int:
    cur = conn.execute(
        "INSERT INTO signals (company_id, kind, value, summary, detail, source_url, checked_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (company_id, signal.kind, signal.value, signal.summary, json.dumps(signal.detail),
         signal.source_url, iso(now)),
    )
    return cur.lastrowid


_KIND = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_EMAIL = re.compile(r"[^\s@<>()]+@[^\s@<>()]+\.[a-z]{2,}", re.IGNORECASE)


def signal_problems(kind: str, summary: str, source_url: str) -> list[str]:
    """Why a recorded signal is not acceptable (an empty list means it is fine).

    Signals are facts about companies: a summary must not carry an email address, so nothing
    found while researching a company can turn into someone to write to.
    """
    problems = []
    if not _KIND.match(kind):
        problems.append("kind must be a short lower-case label, e.g. hiring or funding")
    if not summary.strip() or len(summary) > 300:
        problems.append("summary must be one sentence of at most 300 characters")
    if _EMAIL.search(summary):
        problems.append("a summary must not contain an email address")
    if not source_url.strip():
        problems.append("say where the fact came from (source_url)")
    return problems


def signals_for(conn: sqlite3.Connection, domain: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT s.kind, s.value, s.summary, s.source_url, s.checked_at FROM signals s "
        "JOIN companies co ON co.id = s.company_id WHERE co.domain = ? ORDER BY s.id",
        (domain.lower().removeprefix("www."),),
    ).fetchall()


def insert_contact(conn: sqlite3.Connection, c: ContactCandidate, company_id: int, now: datetime) -> int:
    cur = conn.execute(
        "INSERT INTO contacts (company_id, email, first_name, last_name, title, source_type, "
        "source_url, source_ref, source_note, found_at, lawful_basis, lawful_basis_note, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (company_id, c.email.lower(), c.first_name, c.last_name, c.title, c.source_type,
         c.source_url, c.source_ref, c.source_note, c.found_at or now.date().isoformat(),
         c.lawful_basis, c.lawful_basis_note, iso(now), iso(now)),
    )
    return cur.lastrowid


def provenance(conn: sqlite3.Connection, email: str) -> dict | None:
    """Everything needed to answer "where did you get my email address?"."""
    row = conn.execute(
        "SELECT c.*, co.domain AS company_domain, co.name AS company_name FROM contacts c "
        "JOIN companies co ON co.id = c.company_id WHERE c.email = ?", (email.lower(),),
    ).fetchone()
    if row is None:
        return None
    keys = ("email", "first_name", "last_name", "title", "company_name", "company_domain",
            "source_type", "source_url", "source_note", "found_at", "lawful_basis",
            "lawful_basis_note", "verification_status", "verified_at", "status")
    return {k: row[k] for k in keys}
