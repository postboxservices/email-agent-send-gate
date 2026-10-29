"""Settings for the agent side: prospecting and the agent loop.

These live in env/agent.env. The agent never gets the email provider's credentials, the
review password or the unsubscribe secret: it reaches the gate only through GATE_URL with
GATE_API_TOKEN.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from gate.config import load


@dataclass(frozen=True)
class AgentSettings:
    agent_db_path: str = "agent.db"
    gate_url: str = "http://gate:8000"
    gate_api_token: str = ""

    # Lead sources and checks. Each is optional; empty switches it off.
    apollo_api_key: str = ""
    bouncer_api_key: str = ""

    # Database rules.
    max_contacts_per_company: int = 3
    lawful_bases: tuple[str, ...] = ("legitimate_interest", "consent", "existing_customer")
    # The basis recorded for contacts found through a data provider. Left empty on purpose:
    # you decide (and document) it before the agent may add anyone.
    default_lawful_basis: str = ""

    # The agent loop. ANTHROPIC_API_KEY is read by the Anthropic SDK itself.
    agent_model: str = "claude-sonnet-5-5"
    agent_effort: str = "medium"  # low | medium | high | xhigh | max
    agent_fallbacks: bool = True  # server-side refusal fallback (Claude API only)
    agent_max_iterations: int = 40
    agent_brief_path: str = "brief.md"  # your target market and signal, in plain words
    # Optional web research with Claude's web search and web fetch tools, about companies
    # only. Billed per use on top of tokens; capped per run.
    agent_web_research: bool = False
    agent_web_searches: int = 5
    agent_web_fetches: int = 10
    daily_new_companies: int = 5
    daily_run_hour_utc: int = 9
    watch_interval_seconds: int = 300

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "AgentSettings":
        return load(cls, env)
