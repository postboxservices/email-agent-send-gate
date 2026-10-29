"""The `gate` command.

  gate serve internal|public     run one of the two HTTP services
  gate status                    dry run or live, and why
  gate selftest [--to ADDRESS]   check DKIM covers the unsubscribe headers (sends one message)
  gate review list|show|approve|reject
  gate suppress ADDRESS          never email this address (or @domain) again
"""

import argparse
import json
import sys

import db
from db import utcnow
from gate import providers, review, selftest
from gate.config import Settings
from gate.sending import live_sending_status
from gate.suppression import suppress


def _conn(settings: Settings):
    conn = db.connect(settings.gate_db_path)
    db.migrate(conn, "gate")
    return conn


def cmd_serve(args, settings: Settings) -> int:
    import uvicorn

    from gate.api import create_internal_app, create_public_app

    if args.service == "internal":
        app, port = create_internal_app(settings), args.port or 8000
    else:
        app, port = create_public_app(settings), args.port or 8001
    uvicorn.run(app, host=args.host, port=port, proxy_headers=True)
    return 0


def cmd_status(args, settings: Settings) -> int:
    conn = _conn(settings)
    live, why = live_sending_status(conn, settings)
    print(f"{'LIVE' if live else 'DRY RUN'}: {why}")
    print(f"review mode: {'on' if settings.review_mode else 'off'}")
    try:
        print(f"provider: {providers.describe(settings)} (fingerprint {providers.fingerprint(settings)})")
    except providers.ProviderError as e:
        print(f"provider: {e}")
    return 0


def cmd_selftest(args, settings: Settings) -> int:
    conn = _conn(settings)
    print(f"provider: {providers.describe(settings)}")
    try:
        report = selftest.run(
            conn, settings,
            provider=providers.get_provider(settings),
            fetcher=selftest.fetcher_from_settings(settings, conn, args.via),
            now=utcnow(), to=args.to, timeout=args.timeout,
        )
    except (selftest.SelftestError, providers.ProviderError) as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 2
    print(selftest.format_report(report))
    if args.mailtester:
        from gate.mailtester import Mailtester, format_outcome, run_mailtest

        print("Optional: Postbox Services mailtester (reported only, never decides the result)")
        outcome = run_mailtest(settings, providers.get_provider(settings), report.marker,
                               Mailtester(args.mailtester_url))
        print(format_outcome(outcome))
    return 0 if report.passed else 1


def cmd_review(args, settings: Settings) -> int:
    conn = _conn(settings)
    if args.action == "list":
        for row in review.pending(conn):
            print(f"{row['id']}  {row['created_at']}  {row['to_addr']}  {row['subject']}")
        return 0
    if not args.id:
        print("give the request id", file=sys.stderr)
        return 2
    try:
        if args.action == "show":
            row = conn.execute("SELECT * FROM send_requests WHERE id = ?", (args.id,)).fetchone()
            if row is None:
                raise KeyError(args.id)
            print(f"To: {row['to_name']} <{row['to_addr']}>\nSubject: {row['subject']}\n"
                  f"Why: {row['reason']}\nStatus: {row['status']}\n\n{row['body_text']}")
            return 0
        if args.action == "approve":
            out = review.approve(conn, settings, args.id, args.by, now=utcnow())
        else:
            out = review.reject(conn, args.id, args.by, args.note, now=utcnow())
    except KeyError:
        print(f"no request {args.id}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))
    return 0


def cmd_suppress(args, settings: Settings) -> int:
    conn = _conn(settings)
    new = suppress(conn, args.entry, args.reason, "cli", utcnow())
    print("added" if new else "already suppressed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gate", description="email-agent-send-gate")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run an HTTP service")
    p.add_argument("service", choices=["internal", "public"])
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve)

    sub.add_parser("status", help="show dry run or live, and why").set_defaults(func=cmd_status)

    p = sub.add_parser("selftest", help="send one message and check DKIM covers the unsubscribe headers")
    p.add_argument("--to", default="", help="test mailbox (default SELFTEST_TO)")
    p.add_argument("--timeout", type=float, help="seconds to wait for the message")
    p.add_argument("--mailtester", action="store_true",
                   help="also run the Postbox Services mailtester (optional, reported only)")
    p.add_argument("--mailtester-url", default="https://mcp.postboxservices.com/mcp")
    p.add_argument("--via", choices=["imap", "inbound"], default="",
                   help="how the message comes back (default SELFTEST_VIA, imap)")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("review", help="the approval queue")
    p.add_argument("action", choices=["list", "show", "approve", "reject"])
    p.add_argument("id", nargs="?")
    p.add_argument("--by", default="cli", help="reviewer name")
    p.add_argument("--note", default="", help="reason for rejecting")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("suppress", help="never email an address (or @domain) again")
    p.add_argument("entry")
    p.add_argument("--reason", default="manual")
    p.set_defaults(func=cmd_suppress)

    args = parser.parse_args(argv)
    return args.func(args, Settings.from_env())


if __name__ == "__main__":
    sys.exit(main())
