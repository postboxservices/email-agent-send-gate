"""Signals: plug-in checks, record_signal, web research, and signals from your own data."""

import json
from dataclasses import replace
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from conftest import AGENT_TOKEN
from fastapi.testclient import TestClient
from test_agent import ROOT, ScriptedClaude, named, text, tool

from agent.gate_client import GateClient
from agent.run import run
from agent.tools import build_tools
from db import utcnow
from gate.api import create_internal_app
from prospecting import cli, plugins, store
from prospecting.config import AgentSettings


@pytest.fixture
def agent(settings, provider, tmp_path):
    gate = GateClient("http://testserver", AGENT_TOKEN,
                      http=TestClient(create_internal_app(settings, provider_factory=lambda s: provider)))
    agent_settings = AgentSettings(agent_db_path=str(tmp_path / "agent.db"), gate_api_token=AGENT_TOKEN,
                                   agent_brief_path=str(ROOT / "brief.example.md"))
    conn = store.connect(agent_settings.agent_db_path)
    store.upsert_company(conn, domain="acme-corp.com", name="Acme", source_type="own_data",
                         source_url="crm://1", now=utcnow())
    return SimpleNamespace(settings=agent_settings, conn=conn, gate=gate)


# --- plug-in checks ---------------------------------------------------------------------

def test_plugins_are_loaded_in_name_order(monkeypatch):
    def check_b(domain):
        return store.Signal("b", "", "b", "x")

    def check_a(domain):
        return store.Signal("a", "", "a", "x")

    fake = [SimpleNamespace(name="zeta", load=lambda: check_b),
            SimpleNamespace(name="alpha", load=lambda: check_a)]
    monkeypatch.setattr(plugins, "entry_points", lambda group: fake if group == plugins.GROUP else [])
    assert plugins.load_signal_checks() == [check_a, check_b]


def test_no_plugins_installed_in_this_repo():
    assert plugins.load_signal_checks() == []


def test_check_signal_exists_only_with_checks(agent):
    names = [t.name for t in build_tools(agent.settings, agent.conn, agent.gate)]
    assert "check_signal" not in names and "record_signal" in names

    def tender(domain):
        return store.Signal("tender", "open", f"{domain} has an open tender", "https://tenders.example/1")

    tools = build_tools(agent.settings, agent.conn, agent.gate, signal_fns=[tender])
    out = json.loads(named(tools, "check_signal")(domain="acme-corp.com"))
    expected = {"kind": "tender", "value": "open", "summary": "acme-corp.com has an open tender"}
    assert out["signals"] == [expected]
    assert store.signals_for(agent.conn, "acme-corp.com")[0]["source_url"] == "https://tenders.example/1"


# --- record_signal ----------------------------------------------------------------------

def test_record_signal_stores_a_sourced_fact(agent):
    record = named(build_tools(agent.settings, agent.conn, agent.gate), "record_signal")
    out = json.loads(record(domain="ACME-corp.com", kind="hiring",
                            summary="Advertises two customer-support roles in Berlin.",
                            source_url="https://acme-corp.com/careers"))
    assert out["recorded"]["kind"] == "hiring"
    [row] = store.signals_for(agent.conn, "acme-corp.com")
    assert (row["kind"], row["source_url"]) == ("hiring", "https://acme-corp.com/careers")


@pytest.mark.parametrize("kw,message", [
    ({"domain": "unknown-corp.com"}, "not in the database"),
    ({"kind": "Hiring Now!"}, "short lower-case label"),
    ({"source_url": "acme-corp.com/careers"}, r"http\(s\)"),
    ({"source_url": ""}, "source_url"),
    ({"summary": "Contact jobs@acme-corp.com to apply."}, "email address"),
    ({"summary": ""}, "one sentence"),
])
def test_record_signal_refuses(agent, kw, message):
    record = named(build_tools(agent.settings, agent.conn, agent.gate), "record_signal")
    args = {"domain": "acme-corp.com", "kind": "hiring", "summary": "Hiring support staff.",
            "source_url": "https://acme-corp.com/careers"} | kw
    with pytest.raises(ValueError, match=message):
        record(**args)
    assert store.signals_for(agent.conn, "acme-corp.com") == []


# --- web research -----------------------------------------------------------------------

def test_web_research_is_off_by_default_and_capped_when_on(agent):
    tools = build_tools(agent.settings, agent.conn, agent.gate)
    off = ScriptedClaude([text("ok")])
    run(agent.settings, agent.conn, tools, trigger="daily", client=off.client)
    assert not any(t.get("type", "").startswith("web_") for t in off.requests[0]["tools"])

    on = ScriptedClaude([text("ok")])
    settings = replace(agent.settings, agent_web_research=True, agent_web_searches=3, agent_web_fetches=4)
    run(settings, agent.conn, tools, trigger="daily", client=on.client)
    server = {t["name"]: t for t in on.requests[0]["tools"] if "type" in t and t["type"].startswith("web_")}
    assert server["web_search"] == {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}
    assert server["web_fetch"] == {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 4}


def test_paused_turn_is_continued(agent):
    turns = [("pause_turn", [text("Searching job boards...")]),
             ("tool_use", [tool("record_signal", domain="acme-corp.com", kind="hiring",
                                summary="Hiring a support agent.", source_url="https://acme-corp.com/jobs/1")]),
             ("end_turn", [text("Recorded one signal.")])]
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        stop, content = turns.pop(0)
        return httpx2.Response(200, json={
            "id": f"m{len(requests)}", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5}})

    client = anthropic.Anthropic(api_key="test", max_retries=0,
                                 http_client=httpx2.Client(transport=httpx2.MockTransport(handle)))
    out = run(agent.settings, agent.conn, build_tools(agent.settings, agent.conn, agent.gate),
              trigger="task", task="research acme-corp.com", client=client)
    assert out["stop_reason"] == "end_turn" and out["turns"] == 3
    assert len(requests) == 3  # the paused turn was sent back and continued, not dropped
    assert store.signals_for(agent.conn, "acme-corp.com")[0]["kind"] == "hiring"


# --- signals from your own data ---------------------------------------------------------

def test_import_signals_from_csv(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_DB_PATH", str(tmp_path / "agent.db"))
    path = tmp_path / "signals.csv"
    path.write_text(
        "domain,kind,summary,source_url\n"
        "beta-corp.com,funding,Raised a Series A on 1 Oct 2026.,crm://deals/88\n"
        "gamma-corp.com,hiring,Email hr@gamma-corp.com for the role.,https://gamma-corp.com/jobs\n"
        "delta-corp.com,expansion,Opened an office in Pune.,\n",
        encoding="utf-8",
    )
    assert cli.main(["import-signals", str(path)]) == 1  # two rows refused
    out = json.loads(capsys.readouterr().out)
    assert out["added"] == 1
    assert "line 3" in out["refused"][0] and "email address" in out["refused"][0]
    assert "line 4" in out["refused"][1] and "source_url" in out["refused"][1]

    assert cli.main(["signals", "beta-corp.com"]) == 0
    listing = capsys.readouterr().out
    assert "funding" in listing and "crm://deals/88" in listing
    assert cli.main(["signals", "gamma-corp.com"]) == 1


def test_find_companies_shows_each_signal_with_its_source(agent):
    tools = build_tools(agent.settings, agent.conn, agent.gate)
    named(tools, "record_signal")(domain="acme-corp.com", kind="hiring", summary="Hiring support staff.",
                                  source_url="https://acme-corp.com/careers")
    [company] = json.loads(named(tools, "find_companies")(keywords=["shop"]))["companies"]
    assert company["signals"][0]["source_url"] == "https://acme-corp.com/careers"
