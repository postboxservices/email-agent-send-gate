"""What the gate does with bounces, complaints and replies.

Every outcome here makes the gate send less, never more: hard bounces, complaints and
opt-outs are suppressed; a real reply only lets the conversation continue with that person.
"""

import email
import email.policy
import re
import sqlite3
from datetime import datetime
from email.utils import parseaddr

from db import iso
from gate.config import Settings
from gate.suppression import record_event, suppress
from inbound.classify import LabelFn, classify

_MSGID = re.compile(r"<([^<>@\s]+)@([^<>\s]+)>")
_SELFTEST_MARKER = re.compile(r"\bselftest-[0-9a-f]{12}\b")


def find_send(conn: sqlite3.Connection, *message_ids: str) -> sqlite3.Row | None:
    """Match In-Reply-To / References (or a provider message id) to one of our sends."""
    for value in message_ids:
        for local, _domain in _MSGID.findall(value or "") or [((value or "").strip(), "")]:
            if not local:
                continue
            row = conn.execute(
                "SELECT id, to_addr, contact_ref FROM send_requests "
                "WHERE id = ? OR provider_message_id = ?", (local, local),
            ).fetchone()
            if row:
                return row
    return None


def record_bounce(conn: sqlite3.Connection, address: str, hard: bool, send_id: str | None,
                  detail: dict, now: datetime) -> None:
    record_event(conn, "bounce", address, send_id, detail | {"hard": hard}, now)
    if hard:
        suppress(conn, address, "hard_bounce", f"send:{send_id}" if send_id else "inbound", now)


def record_complaint(conn: sqlite3.Connection, address: str, send_id: str | None,
                     detail: dict, now: datetime) -> None:
    record_event(conn, "complaint", address, send_id, detail, now)
    suppress(conn, address, "complaint", f"send:{send_id}" if send_id else "inbound", now)


def _seen(conn: sqlite3.Connection, message_id: str) -> bool:
    return bool(message_id) and conn.execute(
        "SELECT 1 FROM inbound_seen WHERE message_id = ?", (message_id,)).fetchone() is not None


def handle_message(conn: sqlite3.Connection, settings: Settings, raw: bytes, *, now: datetime,
                   label_fn: LabelFn | None = None, authenticated: bool = True) -> dict:
    """Process one raw message (a reply, an automatic reply or a bounce report).

    `authenticated` is False when the receiving system says the message failed DMARC (for
    example SES's dmarcVerdict). A reply counts as verified, and lets the conversation with
    that person continue past the per-person cap, only if it is authenticated, answers one of
    our sends (In-Reply-To / References carry our random send id) and comes from the address
    we wrote to. Anything else is still recorded and shown to the agent, but changes no cap.
    """
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = str(msg.get("Subject") or "")

    marker = _SELFTEST_MARKER.search(subject)
    known = marker and conn.execute(
        "SELECT 1 FROM selftest_markers WHERE marker = ?", (marker.group(0),)).fetchone()
    if known:  # keep the raw bytes untouched: gate selftest verifies the DKIM signatures
        # The first message to arrive wins; a later one with the same marker cannot replace it.
        conn.execute("INSERT OR IGNORE INTO inbound_selftest (marker, received_at, raw) VALUES (?, ?, ?)",
                     (marker.group(0), iso(now), raw))
        return {"kind": "selftest", "marker": marker.group(0)}

    message_id = str(msg.get("Message-ID") or "").strip()
    if _seen(conn, message_id):  # cheap early exit before the optional classifier call
        return {"kind": "duplicate"}
    verdict = classify(msg, unsubscribe_base_url=settings.unsubscribe_base_url, label_fn=label_fn)
    sender = parseaddr(str(msg.get("From") or ""))[1].lower()

    # Everything below is one transaction: if any write fails, the message is not marked as
    # seen either, so a redelivery processes it again instead of dropping it as a duplicate.
    conn.execute("BEGIN IMMEDIATE")
    try:
        if message_id:
            cur = conn.execute("INSERT OR IGNORE INTO inbound_seen (message_id, received_at) VALUES (?, ?)",
                               (message_id, iso(now)))
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                return {"kind": "duplicate"}
        out = _record(conn, msg, verdict, sender, subject, message_id, now, authenticated)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return out


def _record(conn, msg, verdict, sender, subject, message_id, now, authenticated) -> dict:
    send = find_send(conn, str(msg.get("In-Reply-To") or ""), str(msg.get("References") or ""))
    send_id = send["id"] if send else None

    if verdict.kind == "bounce":
        for address, hard in verdict.bounced:
            record_bounce(conn, address, hard, send_id, {"source": "dsn"}, now)
        return {"kind": "bounce", "bounced": verdict.bounced}

    if verdict.kind == "auto_reply" or not sender:
        record_event(conn, "auto_reply", sender, send_id, {"reason": verdict.reason}, now)
        return {"kind": "auto_reply", "reason": verdict.reason}

    # Suppressing on an opt-out needs no proof: it only ever makes the gate send less.
    if verdict.kind == "opt_out":
        suppress(conn, sender, "opt_out_reply", f"send:{send_id}" if send_id else "reply", now)
    verified = bool(authenticated and send is not None and send["to_addr"] == sender)
    record_event(conn, "reply" if verified else "reply_unverified", sender, send_id,
                 {"classification": verdict.kind, "label": verdict.label, "reason": verdict.reason}, now)
    conn.execute(
        "INSERT INTO replies (received_at, from_addr, subject, text, classification, label, send_id, "
        "message_id, verified) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (iso(now), sender, subject[:300], verdict.text, verdict.kind, verdict.label, send_id, message_id,
         int(verified)),
    )
    return {"kind": verdict.kind, "reason": verdict.reason, "send_id": send_id, "verified": verified}
