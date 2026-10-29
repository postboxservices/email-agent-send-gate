"""Apollo.io REST API: search and enrich ONLY.

Apollo can also put people into sequences and send email itself. That would go around the
gate, so this adapter can call exactly three endpoints and refuses any other path:

  POST /mixed_companies/search   find companies (1 credit per page)
  POST /mixed_people/api_search  find people at those companies (no credits, no emails)
  POST /people/match             enrich one person to get a work email (credits)

Personal emails and phone numbers are never requested.
"""

import httpx

from prospecting.store import ContactCandidate

BASE_URL = "https://api.apollo.io/api/v1"
ALLOWED_PATHS = frozenset({"/mixed_companies/search", "/mixed_people/api_search", "/people/match"})


class ApolloError(RuntimeError):
    pass


def _array(name: str, values) -> list[tuple[str, str]]:
    return [(f"{name}[]", str(v)) for v in values or ()]


class ApolloClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None):
        if not api_key:
            raise ApolloError("APOLLO_API_KEY is empty")
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=30)

    def _post(self, path: str, params: list[tuple[str, str]]) -> dict:
        if path not in ALLOWED_PATHS:
            raise ApolloError(f"{path} is not allowed: this adapter only searches and enriches. "
                              "Sequences would let Apollo send email outside the gate.")
        try:
            response = self.http.post(BASE_URL + path, params=params,
                                      headers={"x-api-key": self.api_key, "Cache-Control": "no-cache"})
        except httpx.HTTPError as e:
            raise ApolloError(f"Apollo unreachable: {e}") from e
        if response.status_code >= 400:
            raise ApolloError(f"Apollo answered {response.status_code}: {response.text[:200]}")
        return response.json()

    def search_companies(self, *, keywords=(), locations=(), employee_ranges=(), domains=(),
                         page: int = 1, per_page: int = 25) -> list[dict]:
        data = self._post("/mixed_companies/search", [
            *_array("q_organization_keyword_tags", keywords),
            *_array("organization_locations", locations),
            *_array("organization_num_employees_ranges", employee_ranges),
            *_array("q_organization_domains_list", domains),
            ("page", str(page)), ("per_page", str(per_page)),
        ])
        return [
            {"apollo_id": o.get("id", ""), "name": o.get("name", ""), "domain": o["primary_domain"],
             "website_url": o.get("website_url") or ""}
            for o in data.get("organizations", []) + data.get("accounts", [])
            if o.get("primary_domain")
        ]

    def search_people(self, *, domains, titles=(), seniorities=(), page: int = 1,
                      per_page: int = 25) -> list[dict]:
        data = self._post("/mixed_people/api_search", [
            *_array("q_organization_domains_list", domains),
            *_array("person_titles", titles),
            *_array("person_seniorities", seniorities),
            ("page", str(page)), ("per_page", str(per_page)),
        ])
        return [
            {"apollo_id": p.get("id", ""), "first_name": p.get("first_name") or "",
             "title": p.get("title") or "", "company": (p.get("organization") or {}).get("name", ""),
             "has_email": bool(p.get("has_email"))}
            for p in data.get("people", [])
        ]

    def enrich(self, *, person_id: str, company_domain: str, lawful_basis: str) -> ContactCandidate | None:
        """Look up one person's work email. Returns None when Apollo has no usable email."""
        data = self._post("/people/match", [
            ("id", person_id), ("domain", company_domain),
            ("reveal_personal_emails", "false"), ("reveal_phone_number", "false"),
        ])
        person = data.get("person") or {}
        email = person.get("email") or ""
        if not email or person.get("match_confidence") in ("low", "none"):
            return None
        return ContactCandidate(
            email=email,
            company_domain=company_domain,
            company_name=(person.get("organization") or {}).get("name", ""),
            first_name=person.get("first_name") or "",
            last_name=person.get("last_name") or "",
            title=person.get("title") or "",
            source_type="apollo",
            source_url=f"https://app.apollo.io/#/people/{person.get('id') or person_id}",
            source_ref=person.get("id") or person_id,
            source_note=f"apollo email_status={person.get('email_status')}",
            lawful_basis=lawful_basis,
        )
