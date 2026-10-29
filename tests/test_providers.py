import email
import socket
from dataclasses import replace

import pytest
from aiosmtpd.controller import Controller

from gate import providers
from gate.message import build_message
from gate.providers.ses import SesProvider
from gate.providers.smtp import SmtpProvider


class Sink:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 OK"


@pytest.fixture
def smtp_server():
    sink = Sink()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    controller = Controller(sink, hostname="127.0.0.1", port=port)
    controller.start()
    yield controller, sink
    controller.stop()


def make(settings):
    return build_message(settings, send_id="abc", to_addr="lee@acme-corp.com", to_name="Lee",
                         subject="Hello", body_text="Hi Lee.")


def test_smtp_sends_headers_unchanged(settings, smtp_server):
    controller, sink = smtp_server
    provider = SmtpProvider("127.0.0.1", controller.port, "", "", security="none")
    assert provider.send(make(settings)) == "<abc@outreach.example.com>"
    [envelope] = sink.messages
    assert envelope.rcpt_tos == ["lee@acme-corp.com"]
    received = email.message_from_bytes(envelope.content)
    assert received["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert received["List-Unsubscribe"].startswith("<https://gate.example.com/u/")


def test_smtp_connection_failure_is_a_provider_error(settings):
    provider = SmtpProvider("127.0.0.1", 1, "", "", security="none", timeout=2)
    with pytest.raises(providers.ProviderError):
        provider.send(make(settings))


class FakeSes:
    def __init__(self):
        self.calls = []

    def send_email(self, **kwargs):
        self.calls.append(kwargs)
        return {"MessageId": "0100abc"}


def test_ses_sends_the_raw_message(settings):
    client = FakeSes()
    provider = SesProvider(region="", configuration_set="outreach", client=client)
    assert provider.send(make(settings)) == "0100abc"
    [call] = client.calls
    assert call["ConfigurationSetName"] == "outreach"
    assert b"List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n" in call["Content"]["Raw"]["Data"]


def test_fingerprint_changes_with_provider_but_not_secrets(settings):
    base = providers.fingerprint(settings)
    assert providers.fingerprint(replace(settings, smtp_password="rotated")) == base
    assert providers.fingerprint(replace(settings, smtp_host="smtp.other.net")) != base
    assert providers.fingerprint(replace(settings, from_address="a@other.example.com")) != base
    assert "not-a-real-password" not in providers.describe(settings)
    with pytest.raises(providers.ProviderError):
        providers.fingerprint(replace(settings, provider="carrier-pigeon"))
