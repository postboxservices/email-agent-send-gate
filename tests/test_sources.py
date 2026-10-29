"""Apollo (search and enrich only) and Bouncer verification, against fake HTTP."""


import httpx
import pytest
from conftest import NOW

from prospecting import store
from prospecting.sources.apollo import ALLOWED_PATHS, ApolloClient, ApolloError
from prospecting.verify import BouncerVerifier, verify_pending


def mock_http(handler, calls):
    def record(request):
        calls.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(record))


# --- Apollo -----------------------------------------------------------------------------

def test_only_search_and_enrich_paths_exist():
    assert ALLOWED_PATHS == {"/mixed_companies/search", "/mixed_people/api_search", "/people/match"}
    client = ApolloClient("key", http=mock_http(lambda r: httpx.Response(200, json={}), []))
    for path in ("/emailer_campaigns/123/add_contact_ids", "/contacts", "/sequences"):
        with pytest.raises(ApolloError, match="not allowed"):
            client._post(path, [])


def test_company_search():
    calls = []
    body = {"organizations": [{"id": "o1", "name": "Acme", "primary_domain": "acme-corp.com"},
                              {"id": "o2", "name": "No Domain"}]}
    client = ApolloClient("key", http=mock_http(lambda r: httpx.Response(200, json=body), calls))
    found = client.search_companies(keywords=["saas"], locations=["Germany"], employee_ranges=["11,50"])
    assert found == [{"apollo_id": "o1", "name": "Acme", "domain": "acme-corp.com", "website_url": ""}]
    [req] = calls
    assert req.method == "POST" and req.url.path == "/api/v1/mixed_companies/search"
    assert req.headers["x-api-key"] == "key"
    assert req.url.params.get_list("q_organization_keyword_tags[]") == ["saas"]
    assert req.url.params.get_list("organization_num_employees_ranges[]") == ["11,50"]


def test_people_search_returns_no_emails():
    body = {"people": [{"id": "p1", "first_name": "Lee", "title": "CTO", "has_email": True,
                        "organization": {"name": "Acme"}}]}
    client = ApolloClient("key", http=mock_http(lambda r: httpx.Response(200, json=body), []))
    [person] = client.search_people(domains=["acme-corp.com"], titles=["CTO"])
    assert person == {"apollo_id": "p1", "first_name": "Lee", "title": "CTO", "company": "Acme",
                      "has_email": True}


def test_enrich_never_asks_for_personal_data():
    calls = []
    body = {"person": {"id": "p1", "first_name": "Lee", "last_name": "Park", "title": "CTO",
                       "email": "lee@acme-corp.com", "email_status": "verified", "match_confidence": "high",
                       "organization": {"name": "Acme"}}}
    client = ApolloClient("key", http=mock_http(lambda r: httpx.Response(200, json=body), calls))
    c = client.enrich(person_id="p1", company_domain="acme-corp.com", lawful_basis="legitimate_interest")
    assert (c.email, c.source_type, c.source_ref) == ("lee@acme-corp.com", "apollo", "p1")
    assert c.source_url == "https://app.apollo.io/#/people/p1"
    params = calls[0].url.params
    assert params["reveal_personal_emails"] == "false"
    assert params["reveal_phone_number"] == "false"


def test_enrich_skips_weak_matches_and_missing_emails():
    for person in ({"id": "p1", "email": "x@acme-corp.com", "match_confidence": "low"}, {"id": "p1"}):
        body = {"person": person}
        client = ApolloClient("key", http=mock_http(lambda r, body=body: httpx.Response(200, json=body), []))
        assert client.enrich(person_id="p1", company_domain="acme-corp.com", lawful_basis="x") is None


def test_apollo_errors():
    client = ApolloClient("key", http=mock_http(lambda r: httpx.Response(429, text="slow down"), []))
    with pytest.raises(ApolloError, match="429"):
        client.search_people(domains=["acme-corp.com"])
    with pytest.raises(ApolloError):
        ApolloClient("")


# --- verification -----------------------------------------------------------------------

def bouncer(status, accept_all="no", role="no"):
    return {"email": "x", "status": status, "reason": "accepted_email",
            "domain": {"acceptAll": accept_all}, "account": {"role": role}}


@pytest.mark.parametrize("body,expected", [
    (bouncer("deliverable"), "valid"),
    (bouncer("deliverable", accept_all="yes"), "catch_all"),
    (bouncer("risky", accept_all="yes"), "catch_all"),
    (bouncer("deliverable", role="yes"), "risky"),
    (bouncer("undeliverable"), "invalid"),
    (bouncer("unknown"), "unknown"),
])
def test_bouncer_mapping(body, expected):
    calls = []
    v = BouncerVerifier("key", http=mock_http(lambda r: httpx.Response(200, json=body), calls))
    assert v.verify("lee@acme-corp.com").status == expected
    assert calls[0].url.path == "/v1.1/email/verify"
    assert calls[0].headers["x-api-key"] == "key"


def test_verify_pending_updates_contacts(tmp_path):
    conn = store.connect(str(tmp_path / "agent.db"))
    company = store.upsert_company(conn, domain="acme-corp.com", name="Acme", source_type="own_data",
                                   source_url="crm", now=NOW)
    for email in ("a@acme-corp.com", "b@acme-corp.com"):
        store.insert_contact(conn, store.ContactCandidate(
            email=email, company_domain="acme-corp.com", source_type="own_data", source_url="crm",
            lawful_basis="consent"), company, NOW)

    def answer(request):
        good = request.url.params["email"].startswith("a@")
        return httpx.Response(200, json=bouncer("deliverable" if good else "undeliverable"))

    counts = verify_pending(conn, BouncerVerifier("key", http=mock_http(answer, [])), NOW)
    assert counts == {"valid": 1, "invalid": 1}
    statuses = dict(conn.execute("SELECT email, verification_status FROM contacts").fetchall())
    assert statuses == {"a@acme-corp.com": "valid", "b@acme-corp.com": "invalid"}
    assert verify_pending(conn, BouncerVerifier("key", http=mock_http(answer, [])), NOW) == {}
