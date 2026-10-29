"""The send gate's checks. Every send request passes all of them, every time.

The agent cannot skip a check or change a cap: it can only ask. A request that fails
gets back the list of refusals, so the agent (and the person reading the log) can see
exactly why. Checks run again right before a reviewed message goes out, because someone
may have unsubscribed while it waited.
"""

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from db import iso
from gate import addresses
from gate.config import Settings

# Statuses that use up a cap. Refused, rejected and failed requests do not.
COUNTED = ("pending_review", "approved", "dry_run", "sent")


@dataclass(frozen=True)
class Refusal:
    code: str
    detail: str


@dataclass(frozen=True)
class Draft:
    to: str
    to_name: str
    subject: str
    body_text: str


@dataclass(frozen=True)
class Context:
    conn: sqlite3.Connection
    settings: Settings
    draft: Draft
    to: addresses.Address
    now: datetime
    exclude_id: str  # the request being re-checked does not count against itself


Check = Callable[[Context], Refusal | None]


def _count(ctx: Context, where: str, *params) -> int:
    placeholders = ",".join("?" * len(COUNTED))
    sql = (
        f"SELECT count(*) FROM send_requests WHERE status IN ({placeholders}) "
        f"AND id != ? AND {where}"
    )
    return ctx.conn.execute(sql, (*COUNTED, ctx.exclude_id, *params)).fetchone()[0]


def _day_ago(ctx: Context) -> str:
    return iso(ctx.now - timedelta(hours=24))


# --- who we may email -----------------------------------------------------------------

def check_address_kind(ctx: Context) -> Refusal | None:
    if ctx.to.is_role:
        return Refusal("role_address", f"{ctx.to} is a role address, not a person")
    if ctx.settings.block_free_mail and ctx.to.is_free_mail:
        return Refusal("free_mail", f"{ctx.to.domain} is a consumer mailbox provider")
    return None


def check_suppression(ctx: Context) -> Refusal | None:
    row = ctx.conn.execute(
        "SELECT entry, reason FROM suppression WHERE entry IN (?, ?)",
        (str(ctx.to), "@" + ctx.to.domain),
    ).fetchone()
    if row:
        return Refusal("suppressed", f"{row['entry']} is suppressed ({row['reason']})")
    return None


# --- how much ---------------------------------------------------------------------------

def check_recipient_cap(ctx: Context) -> Refusal | None:
    """At most N messages to one person until they reply."""
    last_reply = ctx.conn.execute(
        "SELECT max(created_at) FROM events WHERE kind = 'reply' AND address = ?",
        (str(ctx.to),),
    ).fetchone()[0]
    sent = _count(ctx, "to_addr = ? AND created_at > ?", str(ctx.to), last_reply or "")
    cap = ctx.settings.cap_per_recipient_without_reply
    if sent >= cap:
        return Refusal("recipient_cap", f"{sent} message(s) to {ctx.to} without a reply (cap {cap})")
    return None


def check_recipient_domain_cap(ctx: Context) -> Refusal | None:
    """At most N people at one company per 24 hours, counting its subdomains together."""
    company = addresses.company_domain(ctx.to.domain)
    sent = _count(ctx, "to_company = ? AND created_at > ?", company, _day_ago(ctx))
    cap = ctx.settings.cap_per_recipient_domain_per_day
    if sent >= cap:
        return Refusal("recipient_domain_cap", f"{sent} message(s) to {company} in 24h (cap {cap})")
    return None


def check_sending_domain_cap(ctx: Context) -> Refusal | None:
    sending = ctx.settings.sending_domain
    sent = _count(ctx, "sending_domain = ? AND created_at > ?", sending, _day_ago(ctx))
    cap = ctx.settings.cap_per_sending_domain_per_day
    if sent >= cap:
        return Refusal("sending_domain_cap", f"{sent} message(s) from {sending} in 24h (cap {cap})")
    return None


def check_daily_volume(ctx: Context) -> Refusal | None:
    sent = _count(ctx, "created_at > ?", _day_ago(ctx))
    cap = ctx.settings.cap_total_per_day
    if sent >= cap:
        return Refusal("daily_volume", f"{sent} message(s) in 24h (cap {cap})")
    return None


# --- what it says -----------------------------------------------------------------------

_REPLY_PREFIX = re.compile(r"^\s*(re|fw|fwd|aw|sv)\s*:", re.IGNORECASE)


def _has_replied(ctx: Context) -> bool:
    return ctx.conn.execute(
        "SELECT 1 FROM events WHERE kind = 'reply' AND address = ?", (str(ctx.to),)
    ).fetchone() is not None


def check_content(ctx: Context) -> Refusal | None:
    d = ctx.draft
    if any(c in s for s in (d.subject, d.to_name) for c in "\r\n"):
        return Refusal("header_injection", "subject and name must be a single line")
    if not d.subject.strip() or len(d.subject) > 150:
        return Refusal("subject", "subject must be 1 to 150 characters")
    if _REPLY_PREFIX.match(d.subject) and not _has_replied(ctx):
        return Refusal("deceptive_subject", "only an answer to a real reply may start with Re: or Fwd:")
    if not d.body_text.strip() or len(d.body_text) > 5000:
        return Refusal("body", "body must be 1 to 5000 characters")
    return None


# --- who it is from (configuration, checked on every request) -------------------------

def identity_problems(s: Settings) -> list[Refusal]:
    problems = []
    if not s.sender_name.strip():
        problems.append(Refusal("identity", "SENDER_NAME is empty: say who is writing"))
    try:
        addresses.parse(s.from_address)
    except addresses.AddressError:
        problems.append(Refusal("identity", "FROM_ADDRESS is not a valid address"))
    allowed = {d.lower() for d in s.allowed_sending_domains}
    if allowed and s.sending_domain not in allowed:
        problems.append(Refusal("identity", f"{s.sending_domain} is not in ALLOWED_SENDING_DOMAINS"))
    if s.reply_to:
        try:
            addresses.parse(s.reply_to)
        except addresses.AddressError:
            problems.append(Refusal("identity", "REPLY_TO is not a valid address"))
    return problems


def footer_problems(s: Settings) -> list[Refusal]:
    problems = []
    if not s.company_name.strip():
        problems.append(Refusal("footer", "COMPANY_NAME is empty"))
    if not s.postal_address.strip():
        problems.append(Refusal("footer", "POSTAL_ADDRESS is empty: commercial email needs one"))
    if not s.unsubscribe_base_url.startswith("https://"):
        problems.append(Refusal("footer", "UNSUBSCRIBE_BASE_URL must be an https:// URL"))
    if len(s.unsubscribe_secret) < 32:
        problems.append(Refusal("footer", "UNSUBSCRIBE_SECRET must be at least 32 characters"))
    return problems


def config_problems(s: Settings) -> list[Refusal]:
    return identity_problems(s) + footer_problems(s)


CHECKS: list[Check] = [
    check_address_kind,
    check_suppression,
    check_recipient_cap,
    check_recipient_domain_cap,
    check_sending_domain_cap,
    check_daily_volume,
    check_content,
]


def run_checks(
    conn: sqlite3.Connection,
    settings: Settings,
    draft: Draft,
    now: datetime,
    exclude_id: str = "",
) -> list[Refusal]:
    """Return every reason to refuse this draft; an empty list means it may go ahead."""
    try:
        to = addresses.parse(draft.to)
    except addresses.AddressError as e:
        return [Refusal("invalid_address", str(e))]
    ctx = Context(conn, settings, draft, to, now, exclude_id)
    refusals = config_problems(settings)
    refusals += [r for check in CHECKS if (r := check(ctx)) is not None]
    return refusals
