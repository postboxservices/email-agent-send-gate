"""Address parsing and the address rules shared by the gate and the prospecting pipeline."""

import re
from dataclasses import dataclass

# RFC 2142 mailbox names and other addresses that never belong to a prospect.
ROLE_LOCAL_PARTS = frozenset({
    "abuse", "postmaster", "hostmaster", "webmaster", "security", "noc",
    "mailer-daemon", "noreply", "no-reply", "donotreply", "do-not-reply",
    "bounce", "bounces", "unsubscribe", "root", "list", "listserv", "majordomo", "spam",
})

# Consumer mailbox providers. Business-to-business outreach goes to business domains.
FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "yahoo.co.in", "ymail.com",
    "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com", "msn.com", "aol.com",
    "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com", "pm.me",
    "gmx.com", "gmx.de", "gmx.net", "web.de", "mail.com", "yandex.com", "yandex.ru",
    "mail.ru", "zoho.com", "fastmail.com", "tutanota.com", "rediffmail.com",
    "qq.com", "163.com", "126.com", "naver.com",
})

_LOCAL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}$")
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


# Second-level labels under country codes that are public suffixes (example.co.uk), so the
# company is the label before them. An approximation of the Public Suffix List that covers
# the common cases without a dependency.
_SECOND_LEVEL = frozenset({"co", "com", "net", "org", "gov", "ac", "edu", "ltd", "plc", "or", "ne", "go"})


def company_domain(domain: str) -> str:
    """The organisation's domain: acme.com for uk.acme.com, acme.co.uk for mail.acme.co.uk."""
    labels = domain.lower().rstrip(".").split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


class AddressError(ValueError):
    pass


@dataclass(frozen=True)
class Address:
    local: str
    domain: str

    def __str__(self) -> str:
        return f"{self.local}@{self.domain}"

    @property
    def is_role(self) -> bool:
        return self.local.split("+", 1)[0] in ROLE_LOCAL_PARTS

    @property
    def is_free_mail(self) -> bool:
        return self.domain in FREE_MAIL_DOMAINS


def parse(raw: str) -> Address:
    """Parse a bare address (no display name) and normalize it to lower case."""
    local, at, domain = raw.strip().rpartition("@")
    if not at or not _LOCAL.match(local) or ".." in local:
        raise AddressError(f"not a valid email address: {raw!r}")
    domain = domain.lower().rstrip(".")
    if not _DOMAIN.match(domain):
        raise AddressError(f"not a valid email domain: {domain!r}")
    return Address(local.lower(), domain)
