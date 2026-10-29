import base64
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from mail import reply

import db
from gate.api import create_public_app
from inbound.sns import SnsVerifier, string_to_sign

TOPIC = "arn:aws:sns:eu-west-1:123456789012:ses-events"
CERT_URL = "https://sns.eu-west-1.amazonaws.com/SimpleNotificationService-test.pem"


@pytest.fixture(scope="module")
def signer():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sns.amazonaws.com")])
    now = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()))
    return key, cert.public_bytes(serialization.Encoding.PEM)


def signed(signer, message: dict, version="2", cert_url=CERT_URL) -> str:
    key, _ = signer
    message = {"TopicArn": TOPIC, "MessageId": "m-1", "Timestamp": "2026-10-29T12:00:00Z", **message,
               "SignatureVersion": version, "SigningCertURL": cert_url}
    algorithm = hashes.SHA256() if version == "2" else hashes.SHA1()
    sig = key.sign(string_to_sign(message), padding.PKCS1v15(), algorithm)
    return json.dumps(message | {"Signature": base64.b64encode(sig).decode()})


def notification(signer, payload: dict, **kw) -> str:
    return signed(signer, {"Type": "Notification", "Message": json.dumps(payload)}, **kw)


@pytest.fixture
def env(settings, signer):
    fetched = []

    def fetch(url):
        fetched.append(url)
        return signer[1]

    s = replace(settings, inbound_sns_topic_arns=(TOPIC,))
    client = TestClient(create_public_app(s, sns_verifier=SnsVerifier(fetch=fetch), fetch=fetch))
    conn = db.connect(s.gate_db_path)
    yield client, conn, fetched
    conn.close()


def suppressed(conn):
    return {r["entry"]: r["reason"] for r in conn.execute("SELECT entry, reason FROM suppression")}


BOUNCE = {"notificationType": "Bounce", "mail": {"messageId": "0100abc"},
          "bounce": {"bounceType": "Permanent", "bounceSubType": "General",
                     "bouncedRecipients": [{"emailAddress": "gone@acme-corp.com"}]}}


def test_signed_bounce_suppresses(env, signer):
    client, conn, _ = env
    r = client.post("/inbound/sns", content=notification(signer, BOUNCE))
    assert r.status_code == 200 and r.json()["hard"] is True
    assert suppressed(conn) == {"gone@acme-corp.com": "hard_bounce"}


def test_signature_version_1(env, signer):
    client, conn, _ = env
    assert client.post("/inbound/sns", content=notification(signer, BOUNCE, version="1")).status_code == 200


def test_transient_bounce_does_not_suppress(env, signer):
    client, conn, _ = env
    soft = BOUNCE | {"bounce": BOUNCE["bounce"] | {"bounceType": "Transient"}}
    client.post("/inbound/sns", content=notification(signer, soft))
    assert suppressed(conn) == {}


def test_configuration_set_complaint_event(env, signer):
    client, conn, _ = env
    event = {"eventType": "Complaint", "mail": {"messageId": "0100abc"},
             "complaint": {"complainedRecipients": [{"emailAddress": "angry@acme-corp.com"}],
                           "complaintFeedbackType": "abuse"}}
    client.post("/inbound/sns", content=notification(signer, event))
    assert suppressed(conn) == {"angry@acme-corp.com": "complaint"}


def test_received_reply_base64(env, signer):
    client, conn, _ = env
    received = {"notificationType": "Received", "mail": {"messageId": "in-1"},
                "receipt": {"action": {"type": "SNS", "encoding": "BASE64"}},
                "content": base64.b64encode(reply("Please remove me.")).decode()}
    assert client.post("/inbound/sns", content=notification(signer, received)).json()["kind"] == "opt_out"
    assert suppressed(conn) == {"lee@acme-corp.com": "opt_out_reply"}


def test_forged_or_tampered_messages_are_refused(env, signer):
    client, conn, _ = env
    good = json.loads(notification(signer, BOUNCE))
    tampered = good | {"Message": json.dumps(BOUNCE | {"bounce": BOUNCE["bounce"] | {
        "bouncedRecipients": [{"emailAddress": "ceo@rival.com"}]}})}
    assert client.post("/inbound/sns", content=json.dumps(tampered)).status_code == 403
    off_host = notification(signer, BOUNCE, cert_url="https://evil.example.com/cert.pem")
    assert client.post("/inbound/sns", content=off_host).status_code == 403
    assert client.post("/inbound/sns", content="not json").status_code == 403
    assert suppressed(conn) == {}


def test_other_topics_are_refused(env, signer):
    client, conn, _ = env
    other = json.loads(notification(signer, BOUNCE))
    key, _ = signer
    other["TopicArn"] = "arn:aws:sns:eu-west-1:999999999999:someone-else"
    other["Signature"] = base64.b64encode(
        key.sign(string_to_sign(other), padding.PKCS1v15(), hashes.SHA256())).decode()
    assert client.post("/inbound/sns", content=json.dumps(other)).status_code == 403


def test_subscription_confirmation(env, signer):
    client, conn, fetched = env
    url = "https://sns.eu-west-1.amazonaws.com/?Action=ConfirmSubscription&Token=t"
    body = signed(signer, {"Type": "SubscriptionConfirmation", "Message": "confirm", "Token": "t",
                           "SubscribeURL": url})
    assert client.post("/inbound/sns", content=body).json() == {"confirmed": TOPIC}
    assert fetched[-1] == url


def test_received_reply_failing_dmarc_is_not_verified(env, signer, settings):
    from gate.checks import Draft
    from gate.sending import submit

    client, conn, _ = env
    sent = submit(conn, settings, Draft("lee@acme-corp.com", "Lee", "Hi", "Hi."), now=db.utcnow())
    content = reply("Sure, tell me more.", in_reply_to=f"<{sent['id']}@outreach.example.com>")
    for n, verdict in enumerate(("FAIL", "PASS")):
        raw = content.replace(b"<r1@acme-corp.com>", f"<r{n}x@acme-corp.com>".encode())
        received = {"notificationType": "Received", "mail": {"messageId": f"in-{n}"},
                    "receipt": {"action": {"type": "SNS", "encoding": "BASE64"},
                                "dmarcVerdict": {"status": verdict}},
                    "content": base64.b64encode(raw).decode()}
        out = client.post("/inbound/sns", content=notification(signer, received)).json()
        assert out["verified"] is (verdict == "PASS")
