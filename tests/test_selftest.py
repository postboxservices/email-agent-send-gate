"""gate selftest: DKIM h= coverage of the unsubscribe headers, checked on the received message."""

from dataclasses import replace

import pytest
from conftest import NOW, DkimKey, dns_for

from gate import selftest
from gate.message import build_message
from gate.sending import live_sending_status

BASE = ["from", "to", "subject", "date", "message-id"]
BOTH = BASE + ["list-unsubscribe", "list-unsubscribe-post"]


@pytest.fixture(scope="module")
def own_key():
    return DkimKey("outreach.example.com")


@pytest.fixture(scope="module")
def provider_key():
    return DkimKey("provider-mail.net", selector="pm1")


def raw_message(settings) -> bytes:
    msg = build_message(settings, send_id="abc", to_addr="inbox@test.example.org", to_name="",
                        subject="email-agent-send-gate selftest-x", body_text="test")
    return msg.as_bytes()


def test_pass_when_a_valid_signature_covers_both(settings, own_key):
    raw = own_key.sign(raw_message(settings), BOTH)
    report = selftest.analyse(raw, dnsfunc=dns_for(own_key))
    assert report.passed, report.problems
    [sig] = report.signatures
    assert sig.verified and sig.aligned
    assert sig.covers == {"list-unsubscribe": True, "list-unsubscribe-post": True}
    assert "PASS" in selftest.format_report(report)


def test_fail_when_post_header_is_not_signed(settings, own_key):
    raw = own_key.sign(raw_message(settings), BASE + ["list-unsubscribe"])
    report = selftest.analyse(raw, dnsfunc=dns_for(own_key))
    assert not report.passed
    assert report.signatures[0].covers["list-unsubscribe-post"] is False
    assert "does NOT cover list-unsubscribe-post" in selftest.format_report(report)


def test_fail_when_the_covering_signature_does_not_verify(settings, own_key):
    raw = own_key.sign(raw_message(settings), BOTH)
    tampered = raw.replace(b"One-Click", b"One-Clack")
    report = selftest.analyse(tampered, dnsfunc=dns_for(own_key))
    assert not report.passed
    assert report.signatures[0].verified is False


def test_fail_when_the_key_is_not_published(settings, own_key):
    raw = own_key.sign(raw_message(settings), BOTH)
    report = selftest.analyse(raw, dnsfunc=lambda name, timeout=5: None)
    assert not report.passed
    assert report.signatures[0].verified is False


def test_two_signatures_reported_separately(settings, own_key, provider_key):
    """A common case: the provider's own signature covers the headers, ours does not."""
    raw = own_key.sign(raw_message(settings), BASE)
    raw = provider_key.sign(raw, BOTH)
    report = selftest.analyse(raw, dnsfunc=dns_for(own_key, provider_key))
    assert report.passed
    by_domain = {s.domain: s for s in report.signatures}
    assert by_domain["provider-mail.net"].covers["list-unsubscribe-post"] is True
    assert by_domain["provider-mail.net"].aligned is False
    assert by_domain["outreach.example.com"].covers["list-unsubscribe"] is False
    assert by_domain["outreach.example.com"].aligned is True


def test_fail_without_any_signature(settings):
    report = selftest.analyse(raw_message(settings))
    assert not report.passed
    assert "no DKIM-Signature" in report.problems[0]


def test_fail_when_headers_were_stripped_in_transit(settings, own_key):
    raw = own_key.sign(raw_message(settings), BOTH)
    head, _, body = raw.partition(b"\r\n\r\n")
    head = b"\r\n".join(line for line in head.split(b"\r\n")
                        if not line.startswith(b"List-Unsubscribe-Post"))
    report = selftest.analyse(head + b"\r\n\r\n" + body, dnsfunc=dns_for(own_key))
    assert not report.passed
    assert any("List-Unsubscribe-Post" in p for p in report.problems)


class SigningProvider:
    """Stands in for a provider that DKIM-signs with a fixed header list."""

    name = "signing"

    def __init__(self, key, headers, mailbox):
        self.key, self.headers, self.mailbox = key, headers, mailbox

    def send(self, msg):
        self.mailbox.append(self.key.sign(msg.as_bytes(), self.headers))
        return "id-1"


class ListFetcher:
    def __init__(self, mailbox, arrive_after=0):
        self.mailbox, self.calls, self.arrive_after = mailbox, 0, arrive_after

    def find(self, marker):
        self.calls += 1
        if self.calls <= self.arrive_after:
            return None
        for raw in self.mailbox:
            if marker.encode() in raw:
                return "INBOX", raw
        return None


def test_run_records_result_and_unlocks_live_sending(conn, live, own_key):
    mailbox = []
    assert live_sending_status(conn, live)[0] is False
    report = selftest.run(
        conn, live,
        provider=SigningProvider(own_key, BOTH, mailbox),
        fetcher=ListFetcher(mailbox, arrive_after=2),
        now=NOW, sleep=lambda s: None, dnsfunc=dns_for(own_key),
    )
    assert report.passed and report.folder == "INBOX"
    assert live_sending_status(conn, live)[0] is True
    # A different provider is a different fingerprint: locked again.
    assert live_sending_status(conn, replace(live, provider="ses", ses_region="eu-west-1"))[0] is False


def test_run_failure_keeps_live_sending_off(conn, live, own_key):
    mailbox = []
    report = selftest.run(
        conn, live,
        provider=SigningProvider(own_key, BASE, mailbox),
        fetcher=ListFetcher(mailbox), now=NOW, sleep=lambda s: None, dnsfunc=dns_for(own_key),
    )
    assert not report.passed
    live_ok, why = live_sending_status(conn, live)
    assert not live_ok and "failed" in why


def test_run_times_out(conn, live, own_key):
    ticks = iter(range(0, 1000, 10))
    with pytest.raises(selftest.SelftestError, match="did not arrive"):
        selftest.run(conn, live, provider=SigningProvider(own_key, BOTH, []),
                     fetcher=ListFetcher([]), now=NOW, timeout=30,
                     sleep=lambda s: None, clock=lambda: next(ticks))
    assert conn.execute("SELECT count(*) FROM selftest_results").fetchone()[0] == 0


def test_run_refuses_a_broken_configuration(conn, live, own_key):
    with pytest.raises(selftest.SelftestError, match="POSTAL_ADDRESS"):
        selftest.run(conn, replace(live, postal_address=""),
                     provider=SigningProvider(own_key, BOTH, []), fetcher=ListFetcher([]), now=NOW)


def test_imap_fetcher_logs_in_once_for_the_whole_wait(conn, live, own_key, monkeypatch):
    import imaplib

    mailbox, events = [], []

    class FakeImap:
        def __init__(self, host, port, ssl_context=None):
            events.append("connect")

        def login(self, user, password):
            events.append("login")

        def select(self, folder, readonly=False):
            return "OK", [b"1"]

        def search(self, charset, *criteria):
            return "OK", [b"1" if mailbox else b""]

        def fetch(self, num, parts):
            return "OK", [(b"1", mailbox[0])]

        def logout(self):
            events.append("logout")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeImap)
    fetcher = selftest.ImapFetcher("imap.example.org", 993, "u", "p", ("INBOX",))
    ticks = iter(range(100))

    class LateProvider(SigningProvider):
        def send(self, msg):
            self.pending = msg
            return "id"

    provider = LateProvider(own_key, BOTH, mailbox)

    def sleep(seconds):
        if next(ticks) == 3:  # the message arrives after a few polls
            mailbox.append(own_key.sign(provider.pending.as_bytes(), BOTH))

    report = selftest.run(conn, live, provider=provider, fetcher=fetcher, now=NOW, sleep=sleep,
                          dnsfunc=dns_for(own_key))
    assert report.passed
    assert events == ["connect", "login", "logout"]


def test_unaligned_covering_signature_passes_with_a_warning(settings, own_key, provider_key):
    raw = own_key.sign(raw_message(settings), BASE)
    raw = provider_key.sign(raw, BOTH)
    report = selftest.analyse(raw, dnsfunc=dns_for(own_key, provider_key))
    assert report.passed
    assert len(report.warnings) == 1 and "d=provider-mail.net" in report.warnings[0]
    assert "warning:" in selftest.format_report(report)


def test_aligned_covering_signature_has_no_warning(settings, own_key):
    report = selftest.analyse(own_key.sign(raw_message(settings), BOTH), dnsfunc=dns_for(own_key))
    assert report.passed and report.warnings == []


def test_run_registers_its_marker_before_sending(conn, live, own_key):
    seen = []

    class Recording(SigningProvider):
        def send(self, msg):
            seen.append(conn.execute("SELECT count(*) FROM selftest_markers").fetchone()[0])
            return super().send(msg)

    mailbox = []
    selftest.run(conn, live, provider=Recording(own_key, BOTH, mailbox), fetcher=ListFetcher(mailbox),
                 now=NOW, sleep=lambda s: None, dnsfunc=dns_for(own_key))
    assert seen == [1]
