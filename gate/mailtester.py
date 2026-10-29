"""Optional extra for `gate selftest --mailtester`: a full deliverability test.

It asks the Postbox Services mailtester (over its MCP server: create_mailtest, then
get_mailtest_result) for a disposable address, sends one more selftest message there through
the same provider, and prints the score and the link to the full report (SPF, DKIM, DMARC
alignment, blacklists, reverse DNS, content).

It is reported only. It never decides whether the selftest passes: that is the gate's own DKIM
coverage check in gate/selftest.py. Install with `pip install .[mailtester]`.
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from gate.config import Settings
from gate.message import build_message
from gate.providers import Provider

DEFAULT_URL = "https://mcp.postboxservices.com/mcp"


@dataclass
class MailtestOutcome:
    status: str  # done | pending | error
    result_url: str = ""
    score: object = None
    detail: dict = field(default_factory=dict)
    error: str = ""


class Mailtester:
    def __init__(self, url: str = DEFAULT_URL, call=None):
        self.url = url
        self._call = call or self._call_mcp

    async def _call_mcp(self, tool: str, arguments: dict) -> dict:
        from mcp import Client

        async with Client(self.url) as client:
            result = await client.call_tool(tool, arguments)
        if result.is_error or not isinstance(result.structured_content, dict):
            raise RuntimeError(f"{tool} failed")
        return result.structured_content

    def create(self) -> dict:
        return asyncio.run(self._call("create_mailtest", {}))

    def result(self, test_id: str) -> dict:
        return asyncio.run(self._call("get_mailtest_result", {"id": test_id}))


def run_mailtest(
    settings: Settings,
    provider: Provider,
    marker: str,
    mailtester: Mailtester,
    *,
    timeout: float = 120,
    poll_seconds: float = 10,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> MailtestOutcome:
    try:
        test = mailtester.create()
        msg = build_message(
            settings, send_id=f"{marker}-mt", to_addr=test["address"], to_name="",
            subject=f"email-agent-send-gate {marker} mailtester",
            body_text="This message is a deliverability test sent by gate selftest.",
        )
        provider.send(msg)
        deadline = clock() + timeout
        while True:
            data = mailtester.result(test["id"])
            if data.get("status") != "pending":
                score = data.get("score", data.get("total_score"))
                return MailtestOutcome("done", data.get("result_url") or test.get("result_url", ""),
                                       score, data)
            if clock() >= deadline:
                return MailtestOutcome("pending", test.get("result_url", ""))
            sleep(poll_seconds)
    except Exception as e:  # never let the optional check break the selftest
        return MailtestOutcome("error", error=str(e))


def format_outcome(outcome: MailtestOutcome) -> str:
    if outcome.status == "error":
        return f"  mailtester: not available ({outcome.error}); the selftest result above stands"
    if outcome.status == "pending":
        return f"  mailtester: still analysing; see {outcome.result_url}"
    score = f"score {outcome.score}" if outcome.score is not None else "finished"
    return f"  mailtester: {score}; full report {outcome.result_url}"
