"""The container split is a security property, so it is tested like one."""

import re
from pathlib import Path

import yaml

from gate.config import Settings

ROOT = Path(__file__).resolve().parent.parent
PROVIDER_SECRETS = ("SMTP_", "SES_", "AWS_", "GATE_REVIEW_PASSWORD", "SELFTEST_IMAP_PASSWORD")


def keys(env_file: str) -> set[str]:
    """Every variable named in an example file, commented out or not."""
    text = (ROOT / "env" / f"{Path(env_file).name}.example").read_text()
    return set(re.findall(r"^#? ?([A-Z][A-Z0-9_]+)=", text, re.MULTILINE))


def compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())


def test_only_the_internal_gate_gets_provider_credentials():
    services = compose()["services"]
    holders = [name for name, svc in services.items() if "env/provider.env" in svc.get("env_file", [])]
    assert holders == ["gate"]


def test_shared_gate_env_holds_no_provider_secrets():
    leaked = [k for k in keys("env/gate.env") if k.startswith(PROVIDER_SECRETS)]
    assert leaked == []


def test_public_service_is_not_on_the_agent_network():
    services = compose()["services"]
    assert "agent-net" not in services["gate-public"].get("networks", [])


def test_every_example_key_is_a_real_setting():
    known = {f.upper() for f in Settings.__dataclass_fields__}
    aws = {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"}
    for env_file in ("gate.env", "provider.env"):
        assert keys(env_file) - known - aws == set(), env_file


def test_anthropic_key_only_reaches_gate_public():
    services = compose()["services"]
    holders = [name for name, svc in services.items()
               if any(isinstance(e, dict) and e.get("path") == "env/classifier.env"
                      for e in svc.get("env_file", []))]
    assert holders == ["gate-public"]
    assert "ANTHROPIC_API_KEY" not in keys("env/gate.env") | keys("env/provider.env")


def test_agent_env_holds_no_gate_secrets():
    from prospecting.config import AgentSettings

    agent_keys = keys("env/agent.env")
    gate_only = PROVIDER_SECRETS + ("UNSUBSCRIBE_SECRET", "INBOUND_TOKEN", "ANTHROPIC_API_KEY_FOR_CLASSIFIER")
    assert [k for k in agent_keys if k.startswith(gate_only)] == []
    read_by_sdk = {"ANTHROPIC_API_KEY"}
    assert agent_keys - {f.upper() for f in AgentSettings.__dataclass_fields__} == read_by_sdk
