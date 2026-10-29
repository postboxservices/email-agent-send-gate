"""Gate settings, read from the environment.

Safe defaults: DRY_RUN and REVIEW_MODE are on and the caps are low. Each field is read
from the environment variable of the same name in upper case (gate_db_path -> GATE_DB_PATH).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields
from email.utils import parseaddr


def load[T](cls: type[T], env: Mapping[str, str] = os.environ) -> T:
    """Build a settings dataclass from environment variables named after its fields."""
    values = {}
    for f in fields(cls):
        raw = env.get(f.name.upper())
        if raw is None:
            continue
        if f.type is bool:
            values[f.name] = raw.strip().lower() in ("1", "true", "yes", "on")
        elif f.type is int:
            values[f.name] = int(raw)
        elif f.type == tuple[str, ...]:
            values[f.name] = tuple(p.strip() for p in raw.split(",") if p.strip())
        else:
            values[f.name] = raw
    return cls(**values)


@dataclass(frozen=True)
class Settings:
    gate_db_path: str = "gate.db"
    gate_outbox_dir: str = "outbox"

    # Nothing leaves the machine until DRY_RUN is false AND `gate selftest` has passed
    # for the configured provider. Every send waits for a person while REVIEW_MODE is on.
    dry_run: bool = True
    review_mode: bool = True

    # Identity. The agent never chooses who a message is from: the gate sets it.
    sender_name: str = ""
    from_address: str = ""
    reply_to: str = ""
    company_name: str = ""
    postal_address: str = ""
    allowed_sending_domains: tuple[str, ...] = ()  # empty = only the From address's domain

    # One-click unsubscribe (RFC 8058).
    unsubscribe_base_url: str = ""  # public HTTPS origin of the gate-public service
    unsubscribe_secret: str = ""  # HMAC key, at least 32 characters

    # Caps. "Per day" means per rolling 24 hours.
    cap_per_recipient_without_reply: int = 1
    cap_per_recipient_domain_per_day: int = 3
    cap_per_sending_domain_per_day: int = 50
    cap_total_per_day: int = 50
    block_free_mail: bool = True

    # Access. The agent holds GATE_API_TOKEN; only people hold GATE_REVIEW_PASSWORD.
    gate_api_token: str = ""
    gate_review_password: str = ""

    # Email provider: smtp or ses. Only the internal gate service gets these values.
    provider: str = "smtp"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: str = "starttls"  # starttls | tls | none (none: local test relays only)
    ses_region: str = ""
    ses_configuration_set: str = ""

    # Inbound webhooks (gate-public). Empty values switch an endpoint off.
    inbound_token: str = ""  # bearer token for /inbound/events and /inbound/raw
    inbound_sns_topic_arns: tuple[str, ...] = ()  # SNS topics allowed to post to /inbound/sns
    reply_classifier: str = "off"  # off | claude (needs ANTHROPIC_API_KEY on gate-public)
    reply_classifier_model: str = "claude-haiku-5-5"

    # gate selftest: a mailbox you control, read over IMAP, or messages arriving at inbound/.
    selftest_via: str = "imap"  # imap | inbound
    selftest_to: str = ""
    selftest_imap_host: str = ""
    selftest_imap_port: int = 993
    selftest_imap_username: str = ""
    selftest_imap_password: str = ""
    selftest_imap_folders: tuple[str, ...] = ("INBOX",)
    selftest_timeout_seconds: int = 180

    @property
    def sending_domain(self) -> str:
        return parseaddr(self.from_address)[1].rpartition("@")[2].lower()

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        return load(cls, env)
