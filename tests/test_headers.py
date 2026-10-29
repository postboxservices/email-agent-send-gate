import email
import email.policy

from gate.headers import make_token, read_token, unsubscribe_headers
from gate.message import build_message

SECRET = "k" * 40


def test_token_round_trip():
    token = make_token(SECRET, "abc123")
    assert read_token(SECRET, token) == "abc123"
    assert "@" not in token


def test_token_rejects_tampering_and_other_secrets():
    token = make_token(SECRET, "abc123")
    payload, sig = token.split(".")
    other = make_token(SECRET, "zzz999").split(".")[0]
    assert read_token(SECRET, f"{other}.{sig}") is None
    assert read_token(SECRET, f"{payload}.{sig[:-2]}AA") is None
    assert read_token("x" * 40, token) is None
    for junk in ("", ".", "abc", "a.b.c", "!!!.???", payload):
        assert read_token(SECRET, junk) is None


def test_header_values_follow_rfc_8058():
    h = unsubscribe_headers("https://gate.example.com/", SECRET, "abc123")
    assert h["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert h["List-Unsubscribe"].startswith("<https://gate.example.com/u/")
    assert h["List-Unsubscribe"].endswith(">")
    assert "//u/" not in h["List-Unsubscribe"]


def test_built_message(settings):
    msg = build_message(settings, send_id="abc123", to_addr="lee@acme-corp.com", to_name="Lee",
                        subject="Support cover", body_text="Hi Lee.\n")
    parsed = email.message_from_bytes(msg.as_bytes(policy=email.policy.SMTP),
                                      policy=email.policy.default)
    assert parsed["From"] == "Asha Rao <asha@outreach.example.com>"
    assert parsed["To"] == "Lee <lee@acme-corp.com>"
    assert parsed["Reply-To"] == "asha@example.com"
    assert parsed["Message-ID"] == "<abc123@outreach.example.com>"
    assert parsed["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    link = parsed["List-Unsubscribe"].strip("<>")
    body = parsed.get_content()
    assert settings.postal_address in body
    assert "Example Ltd" in body
    assert link in body
    assert read_token(settings.unsubscribe_secret, link.rsplit("/", 1)[1]) == "abc123"


def test_long_unsubscribe_url_is_not_encoded_or_folded(settings):
    """Python's email package RFC 2047-encodes long header words unless told not to."""
    from dataclasses import replace

    long_base = replace(settings, unsubscribe_base_url="https://unsubscribe.mail.some-long-company-name.example.com")
    msg = build_message(long_base, send_id="f" * 32, to_addr="lee@acme-corp.com", to_name="Lee",
                        subject="Hello", body_text="Hi.")
    raw = msg.as_bytes()
    line = next(x for x in raw.split(b"\r\n") if x.startswith(b"List-Unsubscribe:"))
    assert line.startswith(b"List-Unsubscribe: <https://unsubscribe.mail.")
    assert line.endswith(b">")
    assert b"=?" not in line
