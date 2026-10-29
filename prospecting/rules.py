"""Who may go into the contact database at all. Nothing here sends anything.

A contact is refused when:
- the address is not valid, is a role address (abuse@, postmaster@, ...) or a consumer mailbox;
- the address is not at the company's own domain;
- we cannot say where it came from (source type, source URL) or on what lawful basis;
- the company already has the maximum number of contacts;
- the gate has the person (or their domain) on its suppression list;
- the address is already in the database.
"""

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from gate import addresses
from prospecting.config import AgentSettings
from prospecting.store import SOURCE_TYPES, ContactCandidate, insert_contact, upsert_company

SuppressionCheck = Callable[[str], bool]


@dataclass(frozen=True)
class Rejection:
    code: str
    detail: str


def company_problems(conn: sqlite3.Connection, settings: AgentSettings, company_domain: str,
                     lawful_basis: str) -> list[Rejection]:
    """Reasons no new contact at this company could be added, known before looking anyone up."""
    problems = []
    if lawful_basis not in settings.lawful_bases:
        allowed = ", ".join(settings.lawful_bases)
        problems.append(Rejection("lawful_basis", f"lawful basis must be one of {allowed}"))
    company = company_domain.lower().strip().removeprefix("www.")
    count = conn.execute(
        "SELECT count(*) FROM contacts c JOIN companies co ON co.id = c.company_id WHERE co.domain = ?",
        (company,),
    ).fetchone()[0]
    if count >= settings.max_contacts_per_company:
        problems.append(Rejection("company_cap", f"{company} already has {count} contacts"))
    return problems


def check_contact(conn: sqlite3.Connection, settings: AgentSettings, c: ContactCandidate,
                  is_suppressed: SuppressionCheck) -> list[Rejection]:
    try:
        address = addresses.parse(c.email)
    except addresses.AddressError as e:
        return [Rejection("invalid_address", str(e))]
    rejections = []
    company = c.company_domain.lower().strip().removeprefix("www.")

    if address.is_role:
        rejections.append(Rejection("role_address", f"{address} is a role address, not a person"))
    if address.is_free_mail:
        rejections.append(Rejection("free_mail", f"{address.domain} is a consumer mailbox provider"))
    if not company or not (address.domain == company or address.domain.endswith("." + company)):
        rejections.append(Rejection("domain_mismatch", f"{address} is not at {company or '(no company)'}"))
    if c.source_type not in SOURCE_TYPES or not c.source_url.strip():
        rejections.append(Rejection("provenance", "record the source type and where the address was found"))
    if conn.execute("SELECT 1 FROM contacts WHERE email = ?", (str(address),)).fetchone():
        rejections.append(Rejection("duplicate", f"{address} is already in the database"))
    rejections += company_problems(conn, settings, company, c.lawful_basis)
    if not rejections and is_suppressed(str(address)):  # ask the gate last: it is a network call
        rejections.append(Rejection("suppressed", f"{address} is on the gate's suppression list"))
    return rejections


def add_contact(conn: sqlite3.Connection, settings: AgentSettings, c: ContactCandidate,
                is_suppressed: SuppressionCheck, now: datetime) -> tuple[int | None, list[Rejection]]:
    """Add one contact if every rule allows it. A gate that cannot be reached is an error,
    never a pass."""
    rejections = check_contact(conn, settings, c, is_suppressed)
    if rejections:
        return None, rejections
    company_id = upsert_company(conn, domain=c.company_domain, name=c.company_name,
                                source_type=c.source_type, source_url=c.source_url, now=now)
    c.email = str(addresses.parse(c.email))
    return insert_contact(conn, c, company_id, now), []


def add_many(conn: sqlite3.Connection, settings: AgentSettings, candidates: Iterable[ContactCandidate],
             is_suppressed: SuppressionCheck, now: datetime) -> dict:
    added, rejected = 0, {}
    for c in candidates:
        contact_id, rejections = add_contact(conn, settings, c, is_suppressed, now)
        if contact_id:
            added += 1
        for r in rejections:
            rejected[r.code] = rejected.get(r.code, 0) + 1
    return {"added": added, "rejected": rejected}
