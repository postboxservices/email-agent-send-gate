"""The `agent` command.

  agent run daily | replies          one run of a trigger
  agent run task "INSTRUCTION"       one manual run
  agent watch                        the scheduler: replies as they come, the daily batch once a day
  agent runs                         past runs with token usage and an estimated cost
  agent demo                         a self-contained dry run (see agent/demo.py)
"""

import argparse
import logging
import sys

from agent.gate_client import GateClient
from agent.run import default_tools, estimated_cost, run, watch
from prospecting import store
from prospecting.config import AgentSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description="the prospecting agent")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="one run")
    p.add_argument("trigger", choices=["daily", "replies", "task"])
    p.add_argument("task", nargs="?", default="")
    sub.add_parser("watch", help="run on schedule and on new replies")
    sub.add_parser("runs", help="past runs and their cost")
    p = sub.add_parser("demo", help="a self-contained dry run that calls the Claude API")
    p.add_argument("--keep", action="store_true", help="keep the demo folder")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "demo":
        from agent.demo import main as demo

        return demo(keep=args.keep)

    settings = AgentSettings.from_env()
    conn = store.connect(settings.agent_db_path)
    if args.command == "runs":
        for r in conn.execute("SELECT * FROM runs ORDER BY id"):
            cost = estimated_cost(r)
            price = f"~${cost:.4f}" if cost is not None else "?"
            print(f"{r['id']:>4} {r['started_at'][:19]} {r['trigger']:8} {r['model']:18} "
                  f"turns={r['turns']:<3} in={r['input_tokens']} out={r['output_tokens']} "
                  f"cache_read={r['cache_read_tokens']} cost{price} {r['stop_reason']}")
        return 0

    gate = GateClient(settings.gate_url, settings.gate_api_token)
    tools = default_tools(settings, conn, gate)
    if args.command == "watch":
        watch(settings, conn, gate, tools)
        return 0
    out = run(settings, conn, tools, trigger=args.trigger, task=args.task)
    print(out["summary"])
    print(f"\n[run {out['run_id']}: {out['stop_reason']}, {out['turns']} turns, "
          f"{out['input_tokens']} in / {out['output_tokens']} out tokens]")
    return 0 if not out["stop_reason"].startswith("error") else 1


if __name__ == "__main__":
    sys.exit(main())
