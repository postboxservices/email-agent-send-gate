"""Dry run: write the exact message to a local .eml file instead of sending it."""

from email.message import EmailMessage
from pathlib import Path


def write_outbox(outbox_dir: str, send_id: str, msg: EmailMessage) -> str:
    folder = Path(outbox_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{send_id}.eml"
    path.write_bytes(msg.as_bytes())
    return str(path)
