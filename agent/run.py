"""Run the agent: the Anthropic SDK tool runner over the five tools in agent/tools.py.

What starts a run:
  daily    once a day (DAILY_RUN_HOUR_UTC): new companies, signals, first emails, replies
  replies  when new replies are waiting at the gate
  task     a manual instruction, e.g. `agent run task "check the signal for acme.com"`

`agent watch` is the scheduler: it checks for replies every WATCH_INTERVAL_SECONDS and starts
the daily batch once a day. Every run is logged in the runs table with its token usage.
"""

import logging
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import anthropic

from agent.gate_client import GateClient
from agent.prompts import SYSTEM, TRIGGERS, first_message
from agent.tools import build_tools
from db import iso, utcnow
from prospecting.config import AgentSettings
from prospecting.plugins import load_signal_checks
from prospecting.sources.apollo import ApolloClient
from prospecting.verify import BouncerVerifier

log = logging.getLogger(__name__)

# List prices per million tokens on 29 Oct 2026 (input, output, cache read), for the cost
# estimate in `agent runs`. Check https://www.anthropic.com/pricing before relying on them.
# Input and output prices are Anthropic's published list prices. Cache reads: $0.20 is the
# published price for Opus 5.5 and Sonnet 5.5; Haiku 5.5's $0.01 is Anthropic's general rule
# (cache reads cost about 0.1x the input price), not a separately published figure. Haiku 5.5
# also costs more ($0.50 / $2.50) for prompts over 100K tokens, which this estimate ignores.
# Cache writes are charged at 1.25x the input price (5-minute cache, the default here).
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


def default_tools(settings: AgentSettings, conn: sqlite3.Connection, gate: GateClient) -> list:
    return build_tools(
        settings, conn, gate,
        signal_fns=load_signal_checks(),
        apollo=ApolloClient(settings.apollo_api_key) if settings.apollo_api_key else None,
        verifier=BouncerVerifier(settings.bouncer_api_key) if settings.bouncer_api_key else None,
    )


def web_research_tools(settings: AgentSettings) -> list[dict]:
    """Claude's server-side web search and fetch, capped per run. Off unless AGENT_WEB_RESEARCH.

    Web fetch only opens pages already in the conversation (for example search results).
    Nothing here can add a contact: addresses seen on the web cannot become recipients.
    """
    if not settings.agent_web_research:
        return []
    return [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": settings.agent_web_searches},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": settings.agent_web_fetches},
    ]


def task_for(trigger: str, settings: AgentSettings, task: str = "") -> str:
    if trigger == "task":
        if not task.strip():
            raise ValueError("a manual run needs a task")
        return task
    return TRIGGERS[trigger].format(n=settings.daily_new_companies)


def run(
    settings: AgentSettings,
    conn: sqlite3.Connection,
    tools: list,
    *,
    trigger: str,
    task: str = "",
    client: anthropic.Anthropic | None = None,
    now: Callable[[], datetime] = utcnow,
) -> dict:
    brief = Path(settings.agent_brief_path).read_text(encoding="utf-8")
    task_text = task_for(trigger, settings, task)
    run_id = conn.execute(
        "INSERT INTO runs (started_at, trigger, task, model) VALUES (?, ?, ?, ?)",
        (iso(now()), trigger, task_text, settings.agent_model),
    ).lastrowid

    params = dict(
        model=settings.agent_model,
        max_tokens=16000,
        system=SYSTEM,
        tools=list(tools) + web_research_tools(settings),
        messages=[{"role": "user", "content": first_message(brief, task_text)}],
        max_iterations=settings.agent_max_iterations,
        output_config={"effort": settings.agent_effort},
        cache_control={"type": "ephemeral"},
    )
    if settings.agent_fallbacks:
        # If the model declines a request, the API retries it on a fallback model it picks.
        params |= {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

    client = client or anthropic.Anthropic()
    totals = {"turns": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
              "cache_write_tokens": 0}
    last = None
    try:
        # A long server-tool turn (web search) can stop with "pause_turn"; the SDK's runner
        # (anthropic >= 2.3) sends it back to be continued, so the loop needs nothing extra.
        for message in client.beta.messages.tool_runner(**params):
            last = message
            usage = message.usage
            totals["turns"] += 1
            totals["input_tokens"] += usage.input_tokens or 0
            totals["output_tokens"] += usage.output_tokens or 0
            totals["cache_read_tokens"] += usage.cache_read_input_tokens or 0
            totals["cache_write_tokens"] += usage.cache_creation_input_tokens or 0
        stop_reason = last.stop_reason if last else "no response"
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        stop_reason = f"error: {type(e).__name__}"
        log.error("agent run %s failed: %s", run_id, e)

    summary = ""
    if last is not None:
        summary = "\n".join(b.text for b in last.content if b.type == "text").strip()
    conn.execute(
        "UPDATE runs SET finished_at = ?, turns = ?, input_tokens = ?, output_tokens = ?, "
        "cache_read_tokens = ?, cache_write_tokens = ?, stop_reason = ?, summary = ? WHERE id = ?",
        (iso(now()), totals["turns"], totals["input_tokens"], totals["output_tokens"],
         totals["cache_read_tokens"], totals["cache_write_tokens"], stop_reason, summary, run_id),
    )
    return {"run_id": run_id, "stop_reason": stop_reason, "summary": summary, **totals}


def estimated_cost(row: sqlite3.Row) -> float | None:
    prices = PRICES.get(row["model"])
    if prices is None:
        return None
    price_in, price_out, price_cache_read = prices
    return (row["input_tokens"] * price_in + row["output_tokens"] * price_out
            + row["cache_read_tokens"] * price_cache_read
            + row["cache_write_tokens"] * price_in * 1.25) / 1_000_000


# --- the scheduler ----------------------------------------------------------------------

def daily_due(now: datetime, last_daily: str | None, hour: int) -> bool:
    return now.hour >= hour and last_daily != now.date().isoformat()


def watch(settings: AgentSettings, conn: sqlite3.Connection, gate: GateClient, tools: list, *,
          client: anthropic.Anthropic | None = None, now: Callable[[], datetime] = utcnow,
          sleep: Callable[[float], None] = time.sleep, rounds: int | None = None) -> None:
    done = 0
    while rounds is None or done < rounds:
        done += 1
        state = dict(conn.execute("SELECT key, value FROM agent_state").fetchall())
        try:
            if daily_due(now(), state.get("last_daily"), settings.daily_run_hour_utc):
                conn.execute("INSERT OR REPLACE INTO agent_state (key, value) VALUES ('last_daily', ?)",
                             (now().date().isoformat(),))
                log.info("daily run: %s", run(settings, conn, tools, trigger="daily", client=client,
                                               now=now)["stop_reason"])
            else:
                # Only replies newer than both the agent's cursor and the last batch a run was
                # started for. If a run fails or never reads them, the same replies do not start
                # a new paid run every round; the next daily run reads them.
                after = max(int(state.get("reply_cursor", 0)), int(state.get("replies_run_upto", 0)))
                waiting = gate.replies(after=after, limit=200)["replies"]
                if waiting:
                    conn.execute("INSERT OR REPLACE INTO agent_state (key, value) "
                                 "VALUES ('replies_run_upto', ?)", (str(waiting[-1]["id"]),))
                    log.info("replies run: %s", run(settings, conn, tools, trigger="replies",
                                                     client=client, now=now)["stop_reason"])
        except Exception:  # keep watching; the error is in the log and the runs table
            log.exception("watch round failed")
        sleep(settings.watch_interval_seconds)
