from dataclasses import replace
from datetime import timedelta

from conftest import NOW

from db import iso
from gate.checks import Draft, run_checks
from gate.sending import submit
from gate.suppression import record_event, suppress


def draft(to="lee@acme-corp.com", subject="Support cover", body="Hi Lee, a short note."):
    return Draft(to=to, to_name="Lee", subject=subject, body_text=body)


def codes(refusals):
    return [r.code for r in refusals]


def test_clean_draft_passes(conn, settings):
    assert run_checks(conn, settings, draft(), NOW) == []


def test_invalid_address(conn, settings):
    assert codes(run_checks(conn, settings, draft(to="not an address"), NOW)) == ["invalid_address"]


def test_role_and_free_mail_addresses(conn, settings):
    assert codes(run_checks(conn, settings, draft(to="postmaster@acme-corp.com"), NOW)) == ["role_address"]
    assert codes(run_checks(conn, settings, draft(to="abuse+x@acme-corp.com"), NOW)) == ["role_address"]
    assert codes(run_checks(conn, settings, draft(to="lee@gmail.com"), NOW)) == ["free_mail"]
    allow_free = replace(settings, block_free_mail=False)
    assert run_checks(conn, allow_free, draft(to="lee@gmail.com"), NOW) == []


def test_suppressed_address_and_domain(conn, settings):
    suppress(conn, "lee@acme-corp.com", "unsubscribe", "test", NOW)
    assert "suppressed" in codes(run_checks(conn, settings, draft(), NOW))
    assert run_checks(conn, settings, draft(to="kim@acme-corp.com"), NOW) == []
    suppress(conn, "@acme-corp.com", "complaint", "test", NOW)
    assert "suppressed" in codes(run_checks(conn, settings, draft(to="kim@acme-corp.com"), NOW))


def test_suppression_is_case_insensitive(conn, settings):
    suppress(conn, "Lee@ACME-corp.com", "unsubscribe", "test", NOW)
    assert "suppressed" in codes(run_checks(conn, settings, draft(to="LEE@acme-corp.com"), NOW))


def test_one_message_per_recipient_until_they_reply(conn, settings):
    submit(conn, settings, draft(), now=NOW)
    later = NOW + timedelta(days=5)
    assert codes(run_checks(conn, settings, draft(), later)) == ["recipient_cap"]
    record_event(conn, "reply", "lee@acme-corp.com", None, {}, NOW + timedelta(days=1))
    assert run_checks(conn, settings, draft(), later) == []


def test_refused_requests_do_not_use_up_caps(conn, settings):
    submit(conn, settings, draft(subject="RE: our call"), now=NOW)  # refused
    assert run_checks(conn, settings, draft(), NOW) == []


def test_company_cap_is_per_rolling_24_hours(conn, settings):
    for name in ("a", "b", "c"):
        submit(conn, settings, draft(to=f"{name}@acme-corp.com"), now=NOW)
    assert codes(run_checks(conn, settings, draft(to="d@acme-corp.com"), NOW)) == ["recipient_domain_cap"]
    assert run_checks(conn, settings, draft(to="d@other-corp.com"), NOW) == []
    tomorrow = NOW + timedelta(hours=24, minutes=1)
    assert run_checks(conn, settings, draft(to="d@acme-corp.com"), tomorrow) == []


def test_sending_domain_and_daily_volume_caps(conn, settings):
    tight = replace(settings, cap_per_sending_domain_per_day=2, cap_total_per_day=100)
    submit(conn, tight, draft(to="a@one-corp.com"), now=NOW)
    submit(conn, tight, draft(to="b@two-corp.com"), now=NOW)
    assert codes(run_checks(conn, tight, draft(to="c@three-corp.com"), NOW)) == ["sending_domain_cap"]

    other_domain = replace(tight, from_address="asha@mail2.example.com", cap_total_per_day=2)
    assert codes(run_checks(conn, other_domain, draft(to="c@three-corp.com"), NOW)) == ["daily_volume"]


def test_content_rules(conn, settings):
    assert codes(run_checks(conn, settings, draft(subject="Re: following up"), NOW)) == ["deceptive_subject"]
    assert codes(run_checks(conn, settings, draft(subject="Fwd: intro"), NOW)) == ["deceptive_subject"]
    assert codes(run_checks(conn, settings, draft(subject="Hi\r\nBcc: x@y.com"), NOW)) == ["header_injection"]
    assert codes(run_checks(conn, settings, draft(subject=""), NOW)) == ["subject"]
    assert codes(run_checks(conn, settings, draft(body="x" * 5001), NOW)) == ["body"]


def test_configuration_problems_refuse_every_request(conn, settings):
    broken = replace(settings, sender_name="", postal_address="", unsubscribe_base_url="http://x")
    found = run_checks(conn, broken, draft(), NOW)
    assert {r.code for r in found} == {"identity", "footer"}
    assert len(found) == 3


def test_allowed_sending_domains(conn, settings):
    ok = replace(settings, allowed_sending_domains=("outreach.example.com",))
    assert run_checks(conn, ok, draft(), NOW) == []
    wrong = replace(settings, allowed_sending_domains=("mail.example.com",))
    assert codes(run_checks(conn, wrong, draft(), NOW)) == ["identity"]


def test_all_refusals_are_reported_together(conn, settings):
    suppress(conn, "lee@acme-corp.com", "unsubscribe", "test", NOW)
    found = codes(run_checks(conn, settings, draft(subject="Re: hi"), NOW))
    assert found == ["suppressed", "deceptive_subject"]


def test_old_sends_fall_out_of_the_window(conn, settings):
    conn.execute(
        "INSERT INTO send_requests (id, created_at, updated_at, to_addr, to_domain, from_addr, "
        "sending_domain, subject, body_text, status) VALUES ('old', ?, ?, 'x@acme-corp.com', "
        "'acme-corp.com', 'a@b.c', 'outreach.example.com', 's', 'b', 'sent')",
        (iso(NOW - timedelta(days=2)), iso(NOW - timedelta(days=2))),
    )
    tight = replace(settings, cap_per_recipient_domain_per_day=1)
    assert run_checks(conn, tight, draft(), NOW) == []


def test_re_is_allowed_only_after_a_real_reply(conn, settings):
    reply_subject = draft(subject="Re: Support cover")
    assert codes(run_checks(conn, settings, reply_subject, NOW)) == ["deceptive_subject"]
    record_event(conn, "reply", "lee@acme-corp.com", None, {}, NOW - timedelta(hours=1))
    assert run_checks(conn, settings, draft(subject="Re: Support cover"), NOW) == []


def test_company_cap_counts_subdomains_together(conn, settings):
    for name, domain in (("a", "acme-corp.com"), ("b", "uk.acme-corp.com"), ("c", "de.acme-corp.com")):
        submit(conn, settings, draft(to=f"{name}@{domain}"), now=NOW)
    assert codes(run_checks(conn, settings, draft(to="d@us.acme-corp.com"), NOW)) == ["recipient_domain_cap"]
    assert run_checks(conn, settings, draft(to="d@other-corp.com"), NOW) == []


def test_company_domain():
    from gate.addresses import company_domain

    assert company_domain("uk.acme-corp.com") == "acme-corp.com"
    assert company_domain("acme-corp.com") == "acme-corp.com"
    assert company_domain("mail.acme.co.uk") == "acme.co.uk"
    assert company_domain("acme.co.uk") == "acme.co.uk"
    assert company_domain("sales.acme.de") == "acme.de"
