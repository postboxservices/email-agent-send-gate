"""Contact database rules, CSV import, provenance and the gate client."""

from dataclasses import replace

import httpx
import pytest
from conftest import AGENT_TOKEN, NOW
from fastapi.testclient import TestClient

from agent.gate_client import GateClient, GateError
from gate.api import create_internal_app
from prospecting import store
from prospecting.config import AgentSettings
from prospecting.rules import add_contact, add_many
from prospecting.sources.csv_import import read_csv
from prospecting.store import ContactCandidate


@pytest.fixture
def agent_settings(tmp_path):
    return AgentSettings(agent_db_path=str(tmp_path / "agent.db"), gate_api_token=AGENT_TOKEN,
                         default_lawful_basis="legitimate_interest")


@pytest.fixture
def agent_conn(agent_settings):
    conn = store.connect(agent_settings.agent_db_path)
    yield conn
    conn.close()


def never_suppressed(address):
    return False


def candidate(**kw) -> ContactCandidate:
    base = dict(email="lee@acme-corp.com", company_domain="acme-corp.com", company_name="Acme",
                first_name="Lee", title="CTO", source_type="own_data",
                source_url="crm://contacts/42", lawful_basis="legitimate_interest", found_at="2026-10-20")
    return ContactCandidate(**(base | kw))


def codes(rejections):
    return [r.code for r in rejections]


def test_good_contact_is_added_with_provenance(agent_conn, agent_settings):
    contact_id, rejections = add_contact(agent_conn, agent_settings, candidate(email="Lee@Acme-Corp.com"),
                                         never_suppressed, NOW)
    assert contact_id and rejections == []
    p = store.provenance(agent_conn, "lee@acme-corp.com")
    assert p["source_url"] == "crm://contacts/42"
    assert p["found_at"] == "2026-10-20"
    assert p["lawful_basis"] == "legitimate_interest"
    assert p["company_domain"] == "acme-corp.com"
    assert p["verification_status"] == "unverified"


@pytest.mark.parametrize("kw,code", [
    ({"email": "postmaster@acme-corp.com"}, "role_address"),
    ({"email": "lee@gmail.com", "company_domain": "gmail.com"}, "free_mail"),
    ({"email": "lee@other-corp.com"}, "domain_mismatch"),
    ({"source_url": ""}, "provenance"),
    ({"source_type": "bought_list"}, "provenance"),
    ({"lawful_basis": ""}, "lawful_basis"),
    ({"lawful_basis": "we_felt_like_it"}, "lawful_basis"),
    ({"email": "not-an-address"}, "invalid_address"),
])
def test_refusals(agent_conn, agent_settings, kw, code):
    contact_id, rejections = add_contact(agent_conn, agent_settings, candidate(**kw), never_suppressed, NOW)
    assert contact_id is None
    assert code in codes(rejections)


def test_subdomain_address_is_at_the_company(agent_conn, agent_settings):
    contact_id, _ = add_contact(agent_conn, agent_settings, candidate(email="lee@uk.acme-corp.com"),
                                never_suppressed, NOW)
    assert contact_id


def test_duplicate_and_company_cap(agent_conn, agent_settings):
    tight = replace(agent_settings, max_contacts_per_company=2)
    def add(email):
        return add_contact(agent_conn, tight, candidate(email=email), never_suppressed, NOW)

    assert add("a@acme-corp.com")[0] and add("b@acme-corp.com")[0]
    assert codes(add("a@acme-corp.com")[1]) == ["duplicate", "company_cap"]
    assert codes(add("c@acme-corp.com")[1]) == ["company_cap"]


def test_suppressed_people_are_never_re_added(agent_conn, agent_settings):
    asked = []

    def gate_says(address):
        asked.append(address)
        return address == "lee@acme-corp.com"

    assert codes(add_contact(agent_conn, agent_settings, candidate(), gate_says, NOW)[1]) == ["suppressed"]
    # The gate is asked only when nothing else already refused the contact.
    add_contact(agent_conn, agent_settings, candidate(email="abuse@acme-corp.com"), gate_says, NOW)
    assert asked == ["lee@acme-corp.com"]


def test_unreachable_gate_adds_nobody(agent_conn, agent_settings):
    def down(address):
        raise GateError("gate unreachable")

    with pytest.raises(GateError):
        add_contact(agent_conn, agent_settings, candidate(), down, NOW)
    assert agent_conn.execute("SELECT count(*) FROM contacts").fetchone()[0] == 0


def test_csv_import(tmp_path, agent_conn, agent_settings):
    path = tmp_path / "contacts.csv"
    path.write_text(
        "email,company_domain,first_name,title,source_url,lawful_basis\n"
        "lee@acme-corp.com,acme-corp.com,Lee,CTO,,\n"
        "kim@beta-corp.com,beta-corp.com,Kim,CEO,https://events.example.org/signups/7,consent\n"
        "postmaster@gamma-corp.com,gamma-corp.com,,,,\n"
        "sam@gmail.com,gmail.com,Sam,,,\n",
        encoding="utf-8",
    )
    rows = read_csv(path, source_url="crm://export/2026-10-28", lawful_basis="existing_customer")
    out = add_many(agent_conn, agent_settings, rows, never_suppressed, NOW)
    assert out == {"added": 2, "rejected": {"role_address": 1, "free_mail": 1}}
    assert store.provenance(agent_conn, "lee@acme-corp.com")["source_url"] == "crm://export/2026-10-28"
    assert store.provenance(agent_conn, "kim@beta-corp.com")["lawful_basis"] == "consent"


def test_csv_without_provenance_is_refused(tmp_path, agent_conn, agent_settings):
    path = tmp_path / "list.csv"
    path.write_text("email,company_domain\nlee@acme-corp.com,acme-corp.com\n", encoding="utf-8")
    out = add_many(agent_conn, agent_settings, read_csv(path), never_suppressed, NOW)
    assert out == {"added": 0, "rejected": {"provenance": 1, "lawful_basis": 1}}


# --- the gate client --------------------------------------------------------------------

@pytest.fixture
def gate(settings, provider):
    http = TestClient(create_internal_app(settings, provider_factory=lambda s: provider))
    return GateClient("http://testserver", AGENT_TOKEN, http=http)


def test_gate_client_suppression_is_add_only(gate):
    assert gate.is_suppressed("lee@acme-corp.com") is False
    assert gate.suppress("lee@acme-corp.com", "erasure_request") == {"added": True}
    assert gate.is_suppressed("lee@acme-corp.com") is True
    with pytest.raises(GateError, match="422"):
        gate.suppress("lee@acme-corp.com", "remove_from_suppression")


def test_gate_client_send_request_and_replies(gate):
    r = gate.request_send(to="lee@acme-corp.com", to_name="Lee", subject="Support cover",
                          body_text="Hi Lee.", contact_ref="1", reason="hiring support staff")
    assert r["status"] == "pending_review"
    assert gate.replies()["replies"] == []


def test_gate_client_wrong_token_and_unreachable(settings, provider):
    http = TestClient(create_internal_app(settings, provider_factory=lambda s: provider))
    with pytest.raises(GateError, match="401"):
        GateClient("http://testserver", "wrong", http=http).is_suppressed("a@b.com")

    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    http = httpx.Client(transport=httpx.MockTransport(refuse))
    down = GateClient("http://gate:8000", AGENT_TOKEN, http=http)
    with pytest.raises(GateError, match="unreachable"):
        down.is_suppressed("a@b.com")
