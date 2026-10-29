"""Amazon SNS message signature verification.

SNS signs every message it delivers to an HTTPS endpoint. Without this check, anyone who
finds the URL could post fake complaints and bounces (or fake replies) to the gate.
"""

import base64
import re
from collections.abc import Callable
from urllib.parse import urlparse

import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

_SNS_HOST = re.compile(r"^sns\.[a-z0-9-]+\.amazonaws\.com(\.cn)?$")

_SIGNED_FIELDS = {
    "Notification": ("Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"),
    "SubscriptionConfirmation": ("Message", "MessageId", "SubscribeURL", "Timestamp", "Token",
                                 "TopicArn", "Type"),
    "UnsubscribeConfirmation": ("Message", "MessageId", "SubscribeURL", "Timestamp", "Token",
                                "TopicArn", "Type"),
}


class SnsError(ValueError):
    pass


def is_sns_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(_SNS_HOST.match(parsed.hostname or ""))


def _fetch(url: str) -> bytes:
    response = httpx.get(url, timeout=10, follow_redirects=False)
    response.raise_for_status()
    return response.content


def string_to_sign(message: dict) -> bytes:
    fields = _SIGNED_FIELDS.get(message.get("Type", ""))
    if fields is None:
        raise SnsError(f"unknown SNS message type {message.get('Type')!r}")
    out = ""
    for name in fields:
        if name in message:  # Subject is optional
            out += f"{name}\n{message[name]}\n"
    return out.encode()


class SnsVerifier:
    def __init__(self, fetch: Callable[[str], bytes] = _fetch):
        self.fetch = fetch
        self._certs: dict[str, x509.Certificate] = {}

    def verify(self, message: dict) -> None:
        cert_url = message.get("SigningCertURL") or message.get("SigningCertUrl") or ""
        if not is_sns_url(cert_url) or not urlparse(cert_url).path.endswith(".pem"):
            raise SnsError("signing certificate is not on an amazonaws.com SNS host")
        algorithm = {"1": hashes.SHA1(), "2": hashes.SHA256()}.get(str(message.get("SignatureVersion")))
        if algorithm is None:
            raise SnsError("unsupported SignatureVersion")
        if cert_url not in self._certs:
            self._certs[cert_url] = x509.load_pem_x509_certificate(self.fetch(cert_url))
        try:
            signature = base64.b64decode(message.get("Signature", ""), validate=True)
            self._certs[cert_url].public_key().verify(
                signature, string_to_sign(message), padding.PKCS1v15(), algorithm)
        except (InvalidSignature, ValueError) as e:
            raise SnsError("SNS signature does not verify") from e
