"""Import your own contacts from a CSV file.

Columns (header row required): email, company_domain, first_name, last_name, title,
company_name, source_url, found_at, lawful_basis, lawful_basis_note.

`email` and `company_domain` are required. `source_url` (where you got the address: your CRM
record, the event sign-up sheet, ...) and `lawful_basis` may come from the file or from the
command line for the whole file. A row with neither is refused: a list nobody can account
for does not go in.
"""

import csv
from collections.abc import Iterator
from pathlib import Path

from prospecting.store import ContactCandidate


def read_csv(path: str | Path, *, source_url: str = "", lawful_basis: str = "",
             lawful_basis_note: str = "") -> Iterator[ContactCandidate]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            yield ContactCandidate(
                email=row.get("email", ""),
                company_domain=row.get("company_domain", ""),
                company_name=row.get("company_name", ""),
                first_name=row.get("first_name", ""),
                last_name=row.get("last_name", ""),
                title=row.get("title", ""),
                source_type="own_data",
                source_url=row.get("source_url") or source_url,
                source_note=f"csv:{Path(path).name}",
                found_at=row.get("found_at", ""),
                lawful_basis=row.get("lawful_basis") or lawful_basis,
                lawful_basis_note=row.get("lawful_basis_note") or lawful_basis_note,
            )
