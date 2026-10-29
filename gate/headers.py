"""One-click unsubscribe (RFC 8058): the two headers and the HMAC token in the URL.

The token carries only the send id, signed with UNSUBSCRIBE_SECRET, so the URL holds no
email address and cannot be forged or edited to unsubscribe someone else. It does not
expire: an unsubscribe link has to keep working.
"""

import base64
import hashlib
import hmac

LIST_UNSUBSCRIBE_POST = "List-Unsubscribe=One-Click"
_CONTEXT = b"email-agent-send-gate/unsubscribe/v1:"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, payload: bytes) -> bytes:
    return hmac.new(secret.encode(), _CONTEXT + payload, hashlib.sha256).digest()[:16]


def make_token(secret: str, send_id: str) -> str:
    payload = send_id.encode()
    return f"{_b64(payload)}.{_b64(_sign(secret, payload))}"


def read_token(secret: str, token: str) -> str | None:
    """Return the send id inside a valid token, or None for anything else."""
    try:
        payload_part, sig_part = token.split(".")
        payload, sig = _unb64(payload_part), _unb64(sig_part)
        if not hmac.compare_digest(sig, _sign(secret, payload)):
            return None
        return payload.decode()
    except ValueError:  # bad split, bad base64 or bad UTF-8
        return None


def unsubscribe_url(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/u/{token}"


def unsubscribe_headers(base_url: str, secret: str, send_id: str) -> dict[str, str]:
    url = unsubscribe_url(base_url, make_token(secret, send_id))
    return {
        "List-Unsubscribe": f"<{url}>",
        "List-Unsubscribe-Post": LIST_UNSUBSCRIBE_POST,
    }
