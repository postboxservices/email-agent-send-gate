"""The path from a send request to the provider: checks, review, then dry run or live send."""

import json
import logging
import sqlite3
import uuid
from collections.abc import Callable
from datetime import datetime

from db import iso
from gate import addresses, providers
from gate.checks import Draft, run_checks
from gate.config import Settings
from gate.message import build_message
from gate.providers.outbox import write_outbox

log = logging.getLogger(__name__)

ProviderFactory = Callable[[Settings], providers.Provider]


def live_sending_status(conn: sqlite3.Connection, settings: Settings) -> tuple[bool, str]:
    """Live sending needs DRY_RUN=false AND a passing selftest for this exact provider."""
    if settings.dry_run:
        return False, "DRY_RUN is true"
    try:
        fp = providers.fingerprint(settings)
    except providers.ProviderError as e:
        return False, str(e)
    row = conn.execute(
        "SELECT passed, created_at FROM selftest_results WHERE fingerprint = ? "
        "ORDER BY id DESC LIMIT 1",
        (fp,),
    ).fetchone()
    if row is None:
        return False, f"no gate selftest has run for this provider ({fp}); run `gate selftest`"
    if not row["passed"]:
        return False, f"the last gate selftest for this provider ({fp}) failed"
    return True, f"live: selftest passed for {fp} at {row['created_at']}"


def result(row: sqlite3.Row, note: str = "") -> dict:
    out = {"id": row["id"], "status": row["status"], "refusals": json.loads(row["refusals"])}
    if note:
        out["note"] = note
    return out


def _load(conn: sqlite3.Connection, send_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM send_requests WHERE id = ?", (send_id,)).fetchone()
    if row is None:
        raise KeyError(send_id)
    return row


def _update(conn: sqlite3.Connection, send_id: str, now: datetime, **fields) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE send_requests SET {sets}, updated_at = ? WHERE id = ?",
        (*fields.values(), iso(now), send_id),
    )


def submit(
    conn: sqlite3.Connection,
    settings: Settings,
    draft: Draft,
    *,
    now: datetime,
    contact_ref: str = "",
    reason: str = "",
    provider_factory: ProviderFactory = providers.get_provider,
) -> dict:
    """Handle one request from the agent. Every request is recorded, refused or not."""
    send_id = uuid.uuid4().hex
    try:
        to = addresses.parse(draft.to)
        to_addr, to_domain, to_company = str(to), to.domain, addresses.company_domain(to.domain)
    except addresses.AddressError:
        to_addr, to_domain, to_company = draft.to.strip().lower()[:254], "", ""

    # BEGIN IMMEDIATE takes the write lock first, so two requests at once cannot both
    # squeeze under the same cap.
    conn.execute("BEGIN IMMEDIATE")
    try:
        refusals = run_checks(conn, settings, draft, now)
        if refusals:
            status = "refused"
        else:
            status = "pending_review" if settings.review_mode else "approved"
        conn.execute(
            "INSERT INTO send_requests (id, created_at, updated_at, to_addr, to_domain, to_company, "
            "to_name, from_addr, sending_domain, subject, body_text, contact_ref, reason, status, "
            "refusals) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (send_id, iso(now), iso(now), to_addr, to_domain, to_company, draft.to_name,
             settings.from_address, settings.sending_domain, draft.subject, draft.body_text,
             contact_ref, reason, status, json.dumps([r.__dict__ for r in refusals])),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    if status == "approved":
        return deliver(conn, settings, send_id, now=now, provider_factory=provider_factory)
    note = "waiting for a person to approve it" if status == "pending_review" else ""
    return result(_load(conn, send_id), note)


def deliver(
    conn: sqlite3.Connection,
    settings: Settings,
    send_id: str,
    *,
    now: datetime,
    provider_factory: ProviderFactory = providers.get_provider,
) -> dict:
    """Send (or dry-run) an approved request, after checking it one more time."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = _load(conn, send_id)
        if row["status"] != "approved":
            conn.execute("ROLLBACK")
            return result(row, f"not sent: status is {row['status']}")
        draft = Draft(row["to_addr"], row["to_name"], row["subject"], row["body_text"])
        refusals = run_checks(conn, settings, draft, now, exclude_id=send_id)
        if refusals:
            _update(conn, send_id, now, status="refused",
                    refusals=json.dumps([r.__dict__ for r in refusals]))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    if refusals:
        return result(_load(conn, send_id), "refused at send time")

    # From here on every failure ends as status "failed": an "approved" row left behind would
    # count against the caps forever and never be retried.
    try:
        msg = build_message(settings, send_id=send_id, to_addr=row["to_addr"], to_name=row["to_name"],
                            subject=row["subject"], body_text=row["body_text"])
        live, why = live_sending_status(conn, settings)
        if not live:
            path = write_outbox(settings.gate_outbox_dir, send_id, msg)
            _update(conn, send_id, now, status="dry_run", outbox_path=path,
                    message_id=msg["Message-ID"])
            return result(_load(conn, send_id), f"dry run ({why}); written to {path}")
        provider = provider_factory(settings)
        provider_id = provider.send(msg)
    except Exception as e:
        log.exception("send %s failed", send_id)
        _update(conn, send_id, now, status="failed", error=f"{type(e).__name__}: {e}")
        note = ("the provider refused the message" if isinstance(e, providers.ProviderError)
                else "the gate could not send the message; see its log")
        return result(_load(conn, send_id), note)
    _update(conn, send_id, now, status="sent", sent_at=iso(now), provider=provider.name,
            provider_message_id=provider_id, message_id=msg["Message-ID"])
    return result(_load(conn, send_id))
