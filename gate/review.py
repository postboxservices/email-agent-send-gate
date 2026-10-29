"""The human approval queue: a CLI (`gate review ...`) and a minimal web page at /review.

The review page uses its own password (GATE_REVIEW_PASSWORD). The agent's API token
cannot approve anything, so the agent can never wave its own drafts through.
"""

import hashlib
import hmac
import html
import secrets
import sqlite3
from datetime import datetime
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from db import iso, utcnow
from gate import providers
from gate.config import Settings
from gate.sending import ProviderFactory, _load, _update, deliver, live_sending_status, result


def pending(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM send_requests WHERE status = 'pending_review' ORDER BY created_at"
    ).fetchall()


def approve(conn: sqlite3.Connection, settings: Settings, send_id: str, reviewer: str, *,
            now: datetime, provider_factory: ProviderFactory = providers.get_provider) -> dict:
    cur = conn.execute(
        "UPDATE send_requests SET status = 'approved', reviewed_by = ?, reviewed_at = ?, "
        "updated_at = ? WHERE id = ? AND status = 'pending_review'",
        (reviewer, iso(now), iso(now), send_id),
    )
    if cur.rowcount == 0:
        row = _load(conn, send_id)
        return result(row, f"not approved: status is {row['status']}")
    return deliver(conn, settings, send_id, now=now, provider_factory=provider_factory)


def reject(conn: sqlite3.Connection, send_id: str, reviewer: str, note: str, *,
           now: datetime) -> dict:
    row = _load(conn, send_id)
    if row["status"] != "pending_review":
        return result(row, f"not rejected: status is {row['status']}")
    _update(conn, send_id, now, status="rejected", reviewed_by=reviewer, reviewed_at=iso(now),
            review_note=note)
    return result(_load(conn, send_id))


# --- web page ---------------------------------------------------------------------------

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Review queue</title>
<style>body{{font:16px system-ui;max-width:48rem;margin:2rem auto;padding:0 1rem}}
section{{border:1px solid #ccc;border-radius:8px;padding:1rem;margin:1rem 0}}
pre{{white-space:pre-wrap;background:#f6f6f6;padding:.75rem}}
form{{display:inline-block;margin-right:1rem}}</style></head>
<body><h1>Review queue</h1><p>Signed in as {who}. {mode}.</p>{cards}</body></html>"""

_CARD = """<section>
<p><b>To:</b> {to_name} &lt;{to_addr}&gt;, requested {created_at}</p>
<p><b>Why this person:</b> {reason}</p>
<p><b>Subject:</b> {subject}</p>
<pre>{body_text}</pre>
<form method="post" action="/review/{id}/approve">
<input type="hidden" name="csrf" value="{csrf}"><button>Approve and send</button></form>
<form method="post" action="/review/{id}/reject">
<input type="hidden" name="csrf" value="{csrf}">
<input name="note" placeholder="Reason for rejecting"><button>Reject</button></form>
</section>"""


def build_router(settings: Settings, get_conn, provider_factory: ProviderFactory) -> APIRouter:
    router = APIRouter(prefix="/review")
    basic = HTTPBasic(realm="gate review")
    csrf = hmac.new(settings.gate_review_password.encode(), b"review-csrf", hashlib.sha256).hexdigest()

    def reviewer(creds: HTTPBasicCredentials = Depends(basic)) -> str:
        if not settings.gate_review_password:
            raise HTTPException(503, "review page disabled: set GATE_REVIEW_PASSWORD")
        if not secrets.compare_digest(creds.password.encode(), settings.gate_review_password.encode()):
            raise HTTPException(401, "wrong password", headers={"WWW-Authenticate": "Basic"})
        return creds.username or "reviewer"

    async def form(request: Request) -> dict[str, str]:
        body = (await request.body()).decode("utf-8", "replace")
        fields = {k: v[0] for k, v in parse_qs(body).items()}
        # Compare bytes: compare_digest refuses non-ASCII str, which would be a 500, not a 403.
        if not hmac.compare_digest(fields.get("csrf", "").encode(), csrf.encode()):
            raise HTTPException(403, "bad form token; reload the review page")
        return fields

    @router.get("", response_class=HTMLResponse)
    def page(who: str = Depends(reviewer), conn: sqlite3.Connection = Depends(get_conn)):
        live, why = live_sending_status(conn, settings)
        cards = "".join(
            _CARD.format(csrf=csrf, **{k: html.escape(str(row[k] or "")) for k in row.keys()})
            for row in pending(conn)
        )
        mode = "LIVE sending" if live else f"Dry run: {why}"
        return _PAGE.format(who=html.escape(who), mode=html.escape(mode),
                            cards=cards or "<p>Nothing is waiting for review.</p>")

    @router.post("/{send_id}/approve")
    def approve_route(send_id: str, who: str = Depends(reviewer), _: dict = Depends(form),
                      conn: sqlite3.Connection = Depends(get_conn)):
        try:
            approve(conn, settings, send_id, who, now=utcnow(), provider_factory=provider_factory)
        except KeyError:
            raise HTTPException(404, "no such request") from None
        return RedirectResponse("/review", status_code=303)

    @router.post("/{send_id}/reject")
    def reject_route(send_id: str, who: str = Depends(reviewer), fields: dict = Depends(form),
                     conn: sqlite3.Connection = Depends(get_conn)):
        try:
            reject(conn, send_id, who, fields.get("note", ""), now=utcnow())
        except KeyError:
            raise HTTPException(404, "no such request") from None
        return RedirectResponse("/review", status_code=303)

    return router
