import base64
from dataclasses import replace
from datetime import UTC, datetime

import dkim
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import db
from gate.config import Settings

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
AGENT_TOKEN = "agent-token-for-tests"
REVIEW_PASSWORD = "review-password-for-tests"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        gate_db_path=str(tmp_path / "gate.db"),
        gate_outbox_dir=str(tmp_path / "outbox"),
        sender_name="Asha Rao",
        from_address="asha@outreach.example.com",
        reply_to="asha@example.com",
        company_name="Example Ltd",
        postal_address="1 Test Street, Testville 560001, India",
        unsubscribe_base_url="https://gate.example.com",
        unsubscribe_secret="s" * 40,
        gate_api_token=AGENT_TOKEN,
        gate_review_password=REVIEW_PASSWORD,
        provider="smtp",
        smtp_host="smtp.example.net",
        smtp_username="asha",
        smtp_password="not-a-real-password",
        selftest_to="inbox@test.example.org",
    )


@pytest.fixture
def conn(settings):
    c = db.connect(settings.gate_db_path)
    db.migrate(c, "gate")
    yield c
    c.close()


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return f"fake-{len(self.sent)}"


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def live(settings):
    """Settings with DRY_RUN off. Live sending still needs a passing selftest row."""
    return replace(settings, dry_run=False, review_mode=False)


def pass_selftest(conn, settings):
    from gate import providers

    conn.execute(
        "INSERT INTO selftest_results (created_at, fingerprint, descriptor, passed, report) "
        "VALUES (?, ?, ?, 1, '{}')",
        (db.iso(NOW), providers.fingerprint(settings), providers.describe(settings)),
    )


class DkimKey:
    """A throwaway RSA key and a fake DNS lookup that publishes it."""

    def __init__(self, domain: str, selector: str = "test"):
        self.domain, self.selector = domain, selector
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.private_pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption())
        public_der = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.txt = b"v=DKIM1; k=rsa; p=" + base64.b64encode(public_der)

    def sign(self, raw: bytes, headers: list[str]) -> bytes:
        sig = dkim.sign(raw, self.selector.encode(), self.domain.encode(), self.private_pem,
                        include_headers=[h.encode() for h in headers])
        return sig + raw


def dns_for(*keys: DkimKey):
    records = {f"{k.selector}._domainkey.{k.domain}.".encode(): k.txt for k in keys}

    def dnsfunc(name, timeout=5):
        return records.get(name if name.endswith(b".") else name + b".")

    return dnsfunc
