"""The agent's only way to the gate: four HTTP calls with the agent's API token.

There is no way to send email from this side except asking the gate, and the gate decides.
"""

import httpx


class GateError(RuntimeError):
    pass


class GateClient:
    def __init__(self, base_url: str, token: str, http: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=30)
        self.headers = {"Authorization": f"Bearer {token}"}

    def _call(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self.http.request(method, self.base_url + path, headers=self.headers, **kwargs)
        except httpx.HTTPError as e:
            raise GateError(f"gate unreachable: {e}") from e
        if response.status_code >= 400:
            raise GateError(f"gate answered {response.status_code}: {response.text[:200]}")
        return response.json()

    def is_suppressed(self, address: str) -> bool:
        return bool(self._call("POST", "/suppression/check", json={"address": address})["suppressed"])

    def suppress(self, address: str, reason: str) -> dict:
        return self._call("POST", "/suppression", json={"address": address, "reason": reason})

    def request_send(self, *, to: str, to_name: str, subject: str, body_text: str,
                     contact_ref: str, reason: str) -> dict:
        return self._call("POST", "/send-requests", json={
            "to": to, "to_name": to_name, "subject": subject, "body_text": body_text,
            "contact_ref": contact_ref, "reason": reason,
        })

    def replies(self, after: int = 0, limit: int = 50) -> dict:
        return self._call("GET", "/replies", params={"after": after, "limit": limit})
