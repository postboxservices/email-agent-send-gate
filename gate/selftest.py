"""`gate selftest`: prove the provider's DKIM signature covers the unsubscribe headers.

Mailbox providers only trust List-Unsubscribe and List-Unsubscribe-Post when a valid DKIM
signature covers them (RFC 8058, section 4). Whether yours does depends on the provider,
not on this code, so the gate checks the real thing:

1. send one message through the configured provider to a mailbox you control,
2. fetch the message as it arrived (IMAP, or the gate's own inbound/ endpoints),
3. verify every DKIM-Signature on it and read its h= list.

It passes only if at least one signature that verifies covers both headers. Live sending
stays off until it passes for the current provider, and again after any provider change.
"""

import email
import email.policy
import imaplib
import json
import logging
import re
import sqlite3
import ssl
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from email.utils import parseaddr
from typing import Protocol

import dkim

from db import iso
from gate import providers
from gate.checks import config_problems
from gate.config import Settings
from gate.headers import LIST_UNSUBSCRIBE_POST
from gate.message import build_message

REQUIRED = ("list-unsubscribe", "list-unsubscribe-post")


class SelftestError(RuntimeError):
    pass


@dataclass
class SignatureResult:
    domain: str
    selector: str
    algorithm: str
    verified: bool
    error: str
    signed_headers: list[str]
    aligned: bool  # d= is the From domain or a parent of it (relaxed alignment)

    @property
    def covers(self) -> dict[str, bool]:
        return {h: h in self.signed_headers for h in REQUIRED}


@dataclass
class SelftestReport:
    passed: bool
    signatures: list[SignatureResult]
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    folder: str = ""
    marker: str = ""

    def to_json(self) -> str:
        data = asdict(self)
        for sig, obj in zip(data["signatures"], self.signatures, strict=True):
            sig["covers"] = obj.covers
        return json.dumps(data)


def _tags(header_value: str) -> dict[str, str]:
    tags = {}
    for part in header_value.split(";"):
        name, eq, value = part.partition("=")
        if eq:
            tags[name.strip()] = value.strip()
    return tags


def analyse(raw: bytes, dnsfunc: Callable | None = None) -> SelftestReport:
    """Check a received message. `dnsfunc` replaces the DNS lookup of DKIM keys in tests."""
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    problems = []

    unsub = msg.get("List-Unsubscribe", "")
    if not re.search(r"<https://[^>]+>", unsub):
        problems.append("List-Unsubscribe with an https:// URL is missing from the received message")
    if re.sub(r"\s+", "", msg.get("List-Unsubscribe-Post", "")) != LIST_UNSUBSCRIBE_POST:
        problems.append(f"List-Unsubscribe-Post: {LIST_UNSUBSCRIBE_POST} is missing")

    from_domain = parseaddr(msg.get("From", ""))[1].rpartition("@")[2].lower()
    quiet = logging.getLogger("gate.selftest.dkim")
    quiet.addHandler(logging.NullHandler())
    quiet.propagate = False

    signatures = []
    for idx, value in enumerate(msg.get_all("DKIM-Signature") or []):
        tags = _tags(value)
        domain = tags.get("d", "").lower()
        error = ""
        try:
            kwargs = {"dnsfunc": dnsfunc} if dnsfunc else {}
            verified = bool(dkim.DKIM(raw, logger=quiet).verify(idx=idx, **kwargs))
        except dkim.DKIMException as e:
            verified, error = False, str(e)
        if not verified and not error:
            error = "signature does not verify"
        signatures.append(SignatureResult(
            domain=domain,
            selector=tags.get("s", ""),
            algorithm=tags.get("a", ""),
            verified=verified,
            error=error,
            signed_headers=[h.lower() for h in re.sub(r"\s+", "", tags.get("h", "")).split(":") if h],
            aligned=bool(domain) and (from_domain == domain or from_domain.endswith("." + domain)),
        ))

    if not signatures:
        problems.append("the received message has no DKIM-Signature at all")
    good = [s for s in signatures if s.verified and all(s.covers.values())]
    warnings = []
    if good and not any(s.aligned for s in good):
        warnings.append(
            f"the signature that covers the unsubscribe headers (d={good[0].domain}) is not your From "
            f"domain ({from_domain}); it passes RFC 8058, but receivers that expect your own domain's "
            "signature may not trust it. Ask your provider to sign with your domain")
    if signatures and not good:
        problems.append("no signature that verifies covers both List-Unsubscribe and "
                        "List-Unsubscribe-Post; ask your provider to sign them, or sign them yourself")
    return SelftestReport(passed=not problems, signatures=signatures, problems=problems, warnings=warnings)


def format_report(report: SelftestReport) -> str:
    lines = []
    for i, s in enumerate(report.signatures, 1):
        lines.append(
            f"  signature {i}: d={s.domain} s={s.selector} a={s.algorithm} "
            f"{'verified' if s.verified else 'FAILED (' + s.error + ')'}"
            f"{', aligned with From' if s.aligned else ''}"
        )
        for h in REQUIRED:
            lines.append(f"    {'covers' if s.covers[h] else 'does NOT cover'} {h}")
    if report.folder:
        lines.append(f"  found in folder: {report.folder}")
    for p in report.problems:
        lines.append(f"  problem: {p}")
    for w in report.warnings:
        lines.append(f"  warning: {w}")
    lines.append("PASS" if report.passed else "FAIL")
    return "\n".join(lines)


# --- fetching the message back ----------------------------------------------------------

class Fetcher(Protocol):
    def find(self, marker: str) -> tuple[str, bytes] | None:
        """Return (folder, raw message) for the message whose subject has `marker`."""
        ...


class ImapFetcher:
    def __init__(self, host: str, port: int, username: str, password: str,
                 folders: tuple[str, ...]):
        if not (host and username and password):
            raise SelftestError("set SELFTEST_IMAP_HOST, SELFTEST_IMAP_USERNAME and "
                                "SELFTEST_IMAP_PASSWORD")
        self.host, self.port, self.username, self.password = host, port, username, password
        self.folders = folders
        self._imap: imaplib.IMAP4_SSL | None = None

    def find(self, marker: str) -> tuple[str, bytes] | None:
        # One login for the whole wait: logging in again on every poll can trip a mailbox
        # provider's rate limits. A dropped connection is reopened on the next poll; a failed
        # login (wrong password) is raised at once.
        if self._imap is None:
            self._imap = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ssl.create_default_context())
            self._imap.login(self.username, self.password)
        try:
            for folder in self.folders:
                typ, _ = self._imap.select(f'"{folder}"', readonly=True)  # re-select sees new mail
                if typ != "OK":
                    continue
                typ, data = self._imap.search(None, "SUBJECT", f'"{marker}"')
                ids = data[0].split() if typ == "OK" and data and data[0] else []
                if ids:
                    typ, parts = self._imap.fetch(ids[-1], "(BODY.PEEK[])")
                    if typ == "OK":
                        return folder, parts[0][1]
        except (imaplib.IMAP4.abort, OSError):
            self.close()
        return None

    def close(self) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
            self._imap = None


class InboundFetcher:
    """Reads selftest messages that arrived through the gate's own inbound/ endpoints."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def find(self, marker: str) -> tuple[str, bytes] | None:
        row = self.conn.execute("SELECT raw FROM inbound_selftest WHERE marker = ?", (marker,)).fetchone()
        return ("inbound", bytes(row["raw"])) if row else None


def fetcher_from_settings(settings: Settings, conn: sqlite3.Connection, via: str = "") -> Fetcher:
    via = via or settings.selftest_via
    if via == "inbound":
        return InboundFetcher(conn)
    if via != "imap":
        raise SelftestError("SELFTEST_VIA must be imap or inbound")
    return ImapFetcher(settings.selftest_imap_host, settings.selftest_imap_port,
                       settings.selftest_imap_username, settings.selftest_imap_password,
                       settings.selftest_imap_folders)


# --- the whole run ----------------------------------------------------------------------

def run(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    provider: providers.Provider,
    fetcher: Fetcher,
    now: datetime,
    to: str = "",
    timeout: float | None = None,
    poll_seconds: float = 5,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    dnsfunc: Callable | None = None,
) -> SelftestReport:
    """Send, wait for the message to arrive, analyse it and record the result."""
    to = to or settings.selftest_to
    if not to:
        raise SelftestError("no test mailbox: set SELFTEST_TO or pass --to")
    problems = config_problems(settings)
    if problems:
        raise SelftestError("fix the configuration first: " + "; ".join(p.detail for p in problems))

    marker = f"selftest-{uuid.uuid4().hex[:12]}"
    # Registered first: inbound/ only keeps a selftest message whose marker the gate sent.
    conn.execute("INSERT INTO selftest_markers (marker, created_at) VALUES (?, ?)", (marker, iso(now)))
    msg = build_message(
        settings,
        send_id=marker,  # the unsubscribe link in a selftest message points at no real send
        to_addr=to,
        to_name="",
        subject=f"email-agent-send-gate {marker}",
        body_text="This message checks that DKIM covers the unsubscribe headers. You can delete it.",
    )
    provider.send(msg)

    deadline = clock() + (timeout if timeout is not None else settings.selftest_timeout_seconds)
    try:
        while (found := fetcher.find(marker)) is None:
            if clock() >= deadline:
                raise SelftestError(f"the message did not arrive at {to} in time; check spam "
                                    "folders (SELFTEST_IMAP_FOLDERS) and the provider's logs")
            sleep(poll_seconds)
    finally:
        close = getattr(fetcher, "close", None)
        if close:
            close()

    folder, raw = found
    report = analyse(raw, dnsfunc=dnsfunc)
    report.folder = folder
    report.marker = marker
    conn.execute(
        "INSERT INTO selftest_results (created_at, fingerprint, descriptor, passed, report) "
        "VALUES (?, ?, ?, ?, ?)",
        (iso(now), providers.fingerprint(settings), providers.describe(settings),
         int(report.passed), report.to_json()),
    )
    return report
