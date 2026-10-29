from conftest import FakeProvider

from gate.mailtester import Mailtester, format_outcome, run_mailtest

CREATED = {"id": "abc", "address": "test-abc@mailtester.example.net",
           "result_url": "https://mailtester.example.net/r/abc", "status": "pending"}


def fake(results):
    calls = []

    async def call(tool, arguments):
        calls.append((tool, arguments))
        return CREATED if tool == "create_mailtest" else results.pop(0)

    return Mailtester(call=call), calls


def test_sends_to_the_disposable_address_and_reports_the_score(live):
    provider = FakeProvider()
    mailtester, calls = fake([{"status": "pending"}, {"status": "complete", "score": 9.5,
                                                      "result_url": CREATED["result_url"]}])
    outcome = run_mailtest(live, provider, "selftest-0123456789ab", mailtester, sleep=lambda s: None)
    assert (outcome.status, outcome.score) == ("done", 9.5)
    [msg] = provider.sent
    assert msg["To"] == CREATED["address"]
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert calls[1] == ("get_mailtest_result", {"id": "abc"})
    assert "score 9.5" in format_outcome(outcome)


def test_still_pending_after_the_timeout(live):
    mailtester, _ = fake([{"status": "pending"}] * 10)
    ticks = iter(range(0, 1000, 50))
    outcome = run_mailtest(live, FakeProvider(), "m", mailtester, timeout=100, sleep=lambda s: None,
                           clock=lambda: next(ticks))
    assert outcome.status == "pending"
    assert CREATED["result_url"] in format_outcome(outcome)


def test_failures_never_break_the_selftest(live):
    async def down(tool, arguments):
        raise ConnectionError("mcp unreachable")

    outcome = run_mailtest(live, FakeProvider(), "m", Mailtester(call=down))
    assert outcome.status == "error"
    assert "the selftest result above stands" in format_outcome(outcome)
