"""Address verification through a verification service.

Never probe mail servers from your own sending IPs: that is what spammers do, and receivers
treat it that way. A service does it from its own infrastructure.

Verification confirms a mailbox exists. It does not mean the person agreed to hear from you,
and it cannot reliably spot spam traps. Only "valid" contacts are offered to the agent;
catch-all domains (which accept every address) stay out unless verified otherwise.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx

from db import iso

STATUSES = ("valid", "invalid", "catch_all", "risky", "unknown")


@dataclass
class Verification:
    status: str  # one of STATUSES
    provider: str
    detail: dict = field(default_factory=dict)


class Verifier(Protocol):
    name: str

    def verify(self, email: str) -> Verification: ...


class VerifierError(RuntimeError):
    pass


class BouncerVerifier:
    """Bouncer (usebouncer.com) real-time API: GET /v1.1/email/verify."""

    name = "bouncer"
    URL = "https://api.usebouncer.com/v1.1/email/verify"

    def __init__(self, api_key: str, http: httpx.Client | None = None):
        if not api_key:
            raise VerifierError("BOUNCER_API_KEY is empty")
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=40)

    def verify(self, email: str) -> Verification:
        try:
            response = self.http.get(self.URL, params={"email": email, "timeout": 30},
                                     headers={"x-api-key": self.api_key})
        except httpx.HTTPError as e:
            raise VerifierError(f"Bouncer unreachable: {e}") from e
        if response.status_code >= 400:
            raise VerifierError(f"Bouncer answered {response.status_code}: {response.text[:200]}")
        data = response.json()
        status, reason = data.get("status"), data.get("reason", "")
        accept_all = (data.get("domain") or {}).get("acceptAll") == "yes"
        role = (data.get("account") or {}).get("role") == "yes"
        if status == "deliverable" and not accept_all and not role:
            mapped = "valid"
        elif status == "undeliverable":
            mapped = "invalid"
        elif accept_all:
            mapped = "catch_all"
        elif status in ("risky", "deliverable"):  # deliverable but a role account
            mapped = "risky"
        else:
            mapped = "unknown"
        return Verification(mapped, self.name, {"status": status, "reason": reason,
                                                "accept_all": accept_all, "role": role})


def set_verification(conn: sqlite3.Connection, contact_id: int, result: Verification, now: datetime) -> None:
    conn.execute(
        "UPDATE contacts SET verification_status = ?, verification_provider = ?, verified_at = ?, "
        "updated_at = ? WHERE id = ?",
        (result.status, result.provider, iso(now), iso(now), contact_id),
    )


def verify_pending(conn: sqlite3.Connection, verifier: Verifier, now: datetime, limit: int = 50) -> dict:
    rows = conn.execute(
        "SELECT id, email FROM contacts WHERE verification_status = 'unverified' AND status = 'new' "
        "ORDER BY id LIMIT ?", (limit,),
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        result = verifier.verify(row["email"])
        set_verification(conn, row["id"], result, now)
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts
