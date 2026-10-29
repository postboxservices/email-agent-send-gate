"""The `prospect` command (runs on the agent side; it cannot send anything).

  prospect import-csv FILE [--source-url URL] [--lawful-basis BASIS]
  prospect import-signals FILE           signals from your own data (domain,kind,summary,source_url)
  prospect signals DOMAIN                what is on record about a company, and where it came from
  prospect verify [--limit N]            verify unverified contacts with Bouncer
  prospect list [--ready]                contacts; --ready = verified and not yet contacted
  prospect provenance EMAIL              where we got this address, and on what basis
  prospect forget EMAIL                  erase a contact and ask the gate never to email them
"""

import argparse
import csv
import json
import sys

from agent.gate_client import GateClient, GateError
from db import utcnow
from prospecting import store
from prospecting.config import AgentSettings
from prospecting.rules import add_many
from prospecting.sources.csv_import import read_csv
from prospecting.verify import BouncerVerifier, VerifierError, verify_pending


def _gate(settings: AgentSettings) -> GateClient:
    return GateClient(settings.gate_url, settings.gate_api_token)


def cmd_import_csv(args, settings: AgentSettings, conn) -> int:
    candidates = read_csv(args.file, source_url=args.source_url, lawful_basis=args.lawful_basis)
    out = add_many(conn, settings, candidates, _gate(settings).is_suppressed, utcnow())
    print(json.dumps(out, indent=2))
    return 0


def cmd_import_signals(args, settings: AgentSettings, conn) -> int:
    """Each row: domain, kind, summary, source_url (your CRM record, the article, ...)."""
    added, refused = 0, []
    with open(args.file, newline="", encoding="utf-8-sig") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            domain, kind = row.get("domain", "").lower(), row.get("kind", "").lower()
            problems = store.signal_problems(kind, row.get("summary", ""), row.get("source_url", ""))
            if not domain:
                problems.append("domain is empty")
            if problems:
                refused.append(f"line {n}: {'; '.join(problems)}")
                continue
            company_id = store.upsert_company(conn, domain=domain, name=row.get("company_name", ""),
                                              source_type="own_data", source_url=row["source_url"],
                                              now=utcnow())
            store.add_signal(conn, company_id, store.Signal(kind, row.get("value", ""), row["summary"],
                                                            row["source_url"]), utcnow())
            added += 1
    print(json.dumps({"added": added, "refused": refused}, indent=2))
    return 0 if not refused else 1


def cmd_signals(args, settings: AgentSettings, conn) -> int:
    rows = store.signals_for(conn, args.domain)
    if not rows:
        print(f"nothing on record for {args.domain}")
        return 1
    for r in rows:
        print(f"{r['checked_at'][:10]} {r['kind']:12} {r['summary']}\n{'':24}source: {r['source_url']}")
    return 0


def cmd_verify(args, settings: AgentSettings, conn) -> int:
    try:
        counts = verify_pending(conn, BouncerVerifier(settings.bouncer_api_key), utcnow(), args.limit)
    except VerifierError as e:
        print(f"verify: {e}", file=sys.stderr)
        return 2
    print(json.dumps(counts))
    return 0


def cmd_list(args, settings: AgentSettings, conn) -> int:
    where = "WHERE c.verification_status = 'valid' AND c.status = 'new'" if args.ready else ""
    for r in conn.execute(
        "SELECT c.email, c.title, c.verification_status, c.status, co.domain FROM contacts c "
        f"JOIN companies co ON co.id = c.company_id {where} ORDER BY c.id"
    ):
        print(f"{r['email']:40} {r['verification_status']:10} {r['status']:14} {r['title']}")
    return 0


def cmd_provenance(args, settings: AgentSettings, conn) -> int:
    found = store.provenance(conn, args.email)
    if found is None:
        print(f"{args.email} is not in the contact database", file=sys.stderr)
        return 1
    print(json.dumps(found, indent=2))
    return 0


def cmd_forget(args, settings: AgentSettings, conn) -> int:
    # Suppress first: if the gate cannot be reached, nothing is deleted and we can retry.
    _gate(settings).suppress(args.email, "erasure_request")
    conn.execute("DELETE FROM contacts WHERE email = ?", (args.email.lower(),))
    print(f"{args.email}: erased here and suppressed at the gate")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prospect", description="the agent's lead database")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import-csv", help="import your own contacts")
    p.add_argument("file")
    p.add_argument("--source-url", default="", help="where the list came from (for rows without one)")
    p.add_argument("--lawful-basis", default="", help="lawful basis (for rows without one)")
    p.set_defaults(func=cmd_import_csv)

    p = sub.add_parser("import-signals", help="signals from your own data (CSV)")
    p.add_argument("file")
    p.set_defaults(func=cmd_import_signals)

    p = sub.add_parser("signals", help="what is on record about a company")
    p.add_argument("domain")
    p.set_defaults(func=cmd_signals)

    p = sub.add_parser("verify", help="verify unverified contacts")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("list", help="list contacts")
    p.add_argument("--ready", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("provenance", help="where an address came from")
    p.add_argument("email")
    p.set_defaults(func=cmd_provenance)

    p = sub.add_parser("forget", help="erase a contact and suppress them at the gate")
    p.add_argument("email")
    p.set_defaults(func=cmd_forget)

    args = parser.parse_args(argv)
    settings = AgentSettings.from_env()
    conn = store.connect(settings.agent_db_path)
    try:
        return args.func(args, settings, conn)
    except GateError as e:
        print(f"gate: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
