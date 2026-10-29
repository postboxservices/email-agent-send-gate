"""Amazon SES notifications delivered through SNS: bounces, complaints and received mail.

Works with both SES notification styles: identity notifications ("notificationType") and
configuration-set event publishing ("eventType"). For replies, point an SES receipt rule's
SNS action at the same topic; the full message arrives in "content".
"""

import base64
import sqlite3
from datetime import datetime

from gate.config import Settings
from inbound.classify import LabelFn
from inbound.process import find_send, handle_message, record_bounce, record_complaint


def handle_ses(conn: sqlite3.Connection, settings: Settings, notification: dict, *, now: datetime,
               label_fn: LabelFn | None = None) -> dict:
    kind = notification.get("notificationType") or notification.get("eventType") or ""
    mail = notification.get("mail") or {}
    send = find_send(conn, mail.get("messageId", "")) if kind in ("Bounce", "Complaint") else None
    send_id = send["id"] if send else None

    if kind == "Bounce":
        bounce = notification.get("bounce") or {}
        hard = bounce.get("bounceType") == "Permanent"
        addresses = [r.get("emailAddress", "") for r in bounce.get("bouncedRecipients", [])]
        for address in filter(None, addresses):
            record_bounce(conn, address, hard, send_id,
                          {"source": "ses", "type": bounce.get("bounceType"),
                           "subtype": bounce.get("bounceSubType")}, now)
        return {"kind": "bounce", "hard": hard, "addresses": addresses}

    if kind == "Complaint":
        complaint = notification.get("complaint") or {}
        addresses = [r.get("emailAddress", "") for r in complaint.get("complainedRecipients", [])]
        for address in filter(None, addresses):
            record_complaint(conn, address, send_id,
                             {"source": "ses", "type": complaint.get("complaintFeedbackType")}, now)
        return {"kind": "complaint", "addresses": addresses}

    if kind == "Received":
        content = notification.get("content") or ""
        encoding = ((notification.get("receipt") or {}).get("action") or {}).get("encoding", "UTF8")
        raw = base64.b64decode(content) if encoding.upper() == "BASE64" else content.encode("utf-8")
        # SES checks the incoming message; a DMARC failure means the From address may be forged.
        dmarc = (((notification.get("receipt") or {}).get("dmarcVerdict") or {}).get("status") or "").upper()
        return handle_message(conn, settings, raw, now=now, label_fn=label_fn,
                              authenticated=dmarc != "FAIL")

    return {"kind": "ignored", "type": kind}
