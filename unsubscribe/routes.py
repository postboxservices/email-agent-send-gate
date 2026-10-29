"""One-click unsubscribe endpoints (RFC 8058), served by the public gate service.

POST /u/<token>  what mailbox providers call when someone clicks "Unsubscribe". It works
                 with no cookies, no login and no confirmation step, and answers 200.
GET  /u/<token>  the link in the footer. It shows a button and changes nothing by itself,
                 because link scanners open every URL in a message.
"""

import html
import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from db import utcnow
from gate.config import Settings
from gate.headers import LIST_UNSUBSCRIBE_POST, read_token
from gate.suppression import record_event, suppress

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{font:17px system-ui;max-width:32rem;margin:3rem auto;padding:0 1rem}}
button{{font:inherit;padding:.6rem 1.2rem}}</style></head><body>{body}</body></html>"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(title=html.escape(title), body=body), status_code=status)


_NOT_FOUND = ("Link not recognised",
              "<h1>This link is not recognised</h1><p>Reply to the email and ask to be removed; "
              "we will do it by hand.</p>")


def build_router(settings: Settings, get_conn) -> APIRouter:
    router = APIRouter()

    def recipient(token: str, conn: sqlite3.Connection) -> tuple[str, str] | None:
        send_id = read_token(settings.unsubscribe_secret, token)
        if send_id is None:
            return None
        row = conn.execute("SELECT to_addr FROM send_requests WHERE id = ?", (send_id,)).fetchone()
        return (send_id, row["to_addr"]) if row else None

    @router.get("/u/{token}")
    def confirm_page(token: str, conn: sqlite3.Connection = Depends(get_conn)):
        if recipient(token, conn) is None:
            return _page(*_NOT_FOUND, status=404)
        return _page("Unsubscribe", (
            f"<h1>Unsubscribe</h1><p>Stop all email from {html.escape(settings.company_name)} "
            "to this address?</p>"
            f'<form method="post" action="/u/{html.escape(token)}">'
            '<button name="List-Unsubscribe" value="One-Click">Unsubscribe</button></form>'))

    @router.post("/u/{token}")
    async def one_click(token: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        found = recipient(token, conn)
        if found is None:
            return _page(*_NOT_FOUND, status=404)
        send_id, address = found
        one_click_body = LIST_UNSUBSCRIBE_POST in (await request.body()).decode("utf-8", "replace")
        now = utcnow()
        if suppress(conn, address, "unsubscribe", f"send:{send_id}", now):
            record_event(conn, "unsubscribe", address, send_id, {"one_click": one_click_body}, now)
        return _page("Unsubscribed", "<h1>You are unsubscribed</h1>"
                     "<p>You will not get any more email from us at this address.</p>")

    return router
