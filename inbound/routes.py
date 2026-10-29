"""Inbound webhooks, served by the public gate service.

POST /inbound/sns     Amazon SES via SNS (bounces, complaints, received replies). Every
                      message's SNS signature is verified and its topic must be listed in
                      INBOUND_SNS_TOPIC_ARNS.
POST /inbound/events  any other provider: {"type": "bounce", "address": ..., "hard": true}
                      or {"type": "complaint", "address": ...}. Bearer INBOUND_TOKEN.
POST /inbound/raw     a raw RFC 5322 message (a reply or a bounce report), for example from
                      a Postfix pipe or an email worker. Bearer INBOUND_TOKEN.
"""

import json
import logging
import secrets
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from db import utcnow
from gate.config import Settings
from inbound.classify import LabelFn
from inbound.process import handle_message, record_bounce, record_complaint
from inbound.ses import handle_ses
from inbound.sns import SnsError, SnsVerifier, is_sns_url

log = logging.getLogger(__name__)
MAX_BODY = 2 * 1024 * 1024


class InboundEvent(BaseModel):
    type: Literal["bounce", "complaint"]
    address: str = Field(max_length=254)
    hard: bool = True
    send_id: str | None = Field(None, max_length=64)


async def _body(request: Request) -> bytes:
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(413, "message too large")
    return body


def build_router(settings: Settings, get_conn, *, label_fn: LabelFn | None = None,
                 sns_verifier: SnsVerifier | None = None, fetch=None) -> APIRouter:
    router = APIRouter(prefix="/inbound")
    verifier = sns_verifier or SnsVerifier()
    allowed_topics = set(settings.inbound_sns_topic_arns)

    def token(authorization: str = Header("")) -> None:
        if not settings.inbound_token:
            raise HTTPException(404)
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
                value.encode(), settings.inbound_token.encode()):
            raise HTTPException(401, "missing or wrong inbound token")

    def handle_sns(body: bytes, conn: sqlite3.Connection) -> dict:
        try:
            message = json.loads(body)
            verifier.verify(message)
        except (ValueError, SnsError) as e:
            log.warning("rejected SNS message: %s", e)
            raise HTTPException(403, "not a valid SNS message") from None
        if message.get("TopicArn") not in allowed_topics:
            raise HTTPException(403, "topic not allowed")

        if message["Type"] == "SubscriptionConfirmation":
            url = message.get("SubscribeURL", "")
            if not is_sns_url(url):
                raise HTTPException(403, "bad SubscribeURL")
            (fetch or verifier.fetch)(url)
            return {"confirmed": message["TopicArn"]}
        if message["Type"] != "Notification":
            return {"ignored": message["Type"]}
        try:
            notification = json.loads(message["Message"])
        except ValueError:
            return {"ignored": "message is not JSON"}
        return handle_ses(conn, settings, notification, now=utcnow(), label_fn=label_fn)

    # The handlers fetch certificates, call the optional classifier and write SQLite, so they
    # run in a worker thread rather than on the event loop.
    @router.post("/sns")
    async def sns(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        if not allowed_topics:
            raise HTTPException(404)
        return await run_in_threadpool(handle_sns, await _body(request), conn)

    @router.post("/events", dependencies=[Depends(token)])
    def events(event: InboundEvent, conn: sqlite3.Connection = Depends(get_conn)):
        detail = {"source": "webhook"}
        if event.type == "bounce":
            record_bounce(conn, event.address, event.hard, event.send_id, detail, utcnow())
        else:
            record_complaint(conn, event.address, event.send_id, detail, utcnow())
        return {"kind": event.type}

    @router.post("/raw", dependencies=[Depends(token)])
    async def raw(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        body = await _body(request)
        return await run_in_threadpool(handle_message, conn, settings, body, now=utcnow(), label_fn=label_fn)

    return router
