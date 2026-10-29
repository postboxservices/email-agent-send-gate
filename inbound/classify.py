"""Sort an incoming message: bounce, automatic reply, opt-out, or a real reply.

The order matters and errs on the side of not emailing people:
1. delivery status reports (bounces)              -> bounce
2. Auto-Submitted and other automatic markers     -> auto_reply: never answered (RFC 3834)
3. opt-out wording in the new text or the subject -> opt_out: suppressed
4. optional Claude classifier for the rest        -> opt_out or reply
5. everything else                                -> reply

The rules can be wrong in one direction only: "I don't want to unsubscribe" is read as an
opt-out. Suppressing someone by mistake costs one lead; emailing someone who asked us to
stop costs much more.
"""

import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import EmailMessage, Message
from email.utils import parseaddr

# subject, new text -> one label from inbound.llm.LABELS, or None when unsure or unavailable
LabelFn = Callable[[str, str], str | None]

_AUTO_SUBJECT = re.compile(
    r"^\s*(automatic reply|auto[- ]?reply|autoreply|out of (the )?office|abwesenheit|"
    r"r[ée]ponse automatique|respuesta autom[áa]tica|on vacation|away from (the )?office)",
    re.IGNORECASE,
)
_OPT_OUT = re.compile(
    r"\b(unsubscribe|remove me|take me off|opt[ -]?out|"
    r"stop (emailing|contacting|sending|messaging|writing)|"
    r"(do not|don'?t) (email|contact|message|write)|not interested|no,? thanks?( you)?)\b",
    re.IGNORECASE,
)
_ONE_WORD_OPT_OUT = re.compile(r"^\s*(stop|remove|unsubscribe)\W*\s*$", re.IGNORECASE)
_QUOTE_START = re.compile(
    r"^(on .{0,300}(wrote|schrieb|a [ée]crit|escribi[óo])\s*:?|.*\bwrote:|"
    r"-+ ?original message ?-+|_{8,}|from: .+@.+)$",
    re.IGNORECASE,
)
_MAILER_DAEMON = re.compile(r"^(mailer-daemon|postmaster)@", re.IGNORECASE)
_STATUS = re.compile(r"\b([245])\.\d{1,3}\.\d{1,3}\b")


@dataclass
class Verdict:
    kind: str  # bounce | auto_reply | opt_out | reply
    reason: str
    text: str = ""  # the new text of the reply, quotes and our footer removed
    label: str = ""  # the Claude classifier's label, when it ran
    bounced: list[tuple[str, bool]] = field(default_factory=list)  # (address, hard)


def body_text(msg: Message) -> str:
    if not isinstance(msg, EmailMessage):
        return ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, ValueError):  # unknown charset or broken encoding
        text = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    if part.get_content_type() == "text/html":
        text = re.sub(r"(?is)<(style|script|blockquote)\b.*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return text


def new_text(text: str, unsubscribe_base_url: str = "") -> str:
    """The part the person wrote: stop at the quoted original, drop '>' lines and our footer."""
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if _QUOTE_START.match(stripped):
            break
        if stripped.startswith(">"):
            continue
        if unsubscribe_base_url and unsubscribe_base_url in stripped:
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()[:10000]


def _bounced_recipients(msg: Message) -> list[tuple[str, bool]]:
    found = []
    for part in msg.walk():
        if part.get_content_type() != "message/delivery-status":
            continue
        # The parser gives one sub-message per block: per-message fields first, then one
        # per recipient.
        for block in part.get_payload() or []:
            recipient = block.get("Final-Recipient") or block.get("Original-Recipient") or ""
            address = recipient.split(";", 1)[-1].strip().strip("<>")
            if "@" not in address:
                continue
            action = (block.get("Action") or "").strip().lower()
            status = _STATUS.search(block.get("Status") or "")
            if action == "failed" or (status and status.group(1) == "5"):
                found.append((address.lower(), bool(status and status.group(1) == "5")))
    return found


def _is_auto(msg: Message) -> str:
    auto = (msg.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return f"Auto-Submitted: {auto}"
    for name in ("X-Autoreply", "X-Autorespond", "X-Autoresponder"):
        if msg.get(name):
            return f"{name} header"
    if (msg.get("Precedence") or "").strip().lower() in ("bulk", "junk", "list", "auto_reply"):
        return f"Precedence: {msg.get('Precedence')}"
    if msg.get("List-Id") or msg.get("List-Unsubscribe"):
        return "sent by a mailing list"
    if _AUTO_SUBJECT.match(str(msg.get("Subject") or "")):
        return "automatic-reply subject"
    return ""


def classify(msg: Message, *, unsubscribe_base_url: str = "", label_fn: LabelFn | None = None) -> Verdict:
    content_type = msg.get_content_type()
    sender = parseaddr(str(msg.get("From") or ""))[1]
    if content_type == "multipart/report" or _MAILER_DAEMON.match(sender):
        return Verdict("bounce", "delivery status report", bounced=_bounced_recipients(msg))

    reason = _is_auto(msg)
    if reason:
        return Verdict("auto_reply", reason)

    subject = str(msg.get("Subject") or "")
    text = new_text(body_text(msg), unsubscribe_base_url)
    for line in [subject, *text.splitlines()]:
        if _OPT_OUT.search(line) or _ONE_WORD_OPT_OUT.match(line):
            return Verdict("opt_out", f"opt-out wording: {line.strip()[:80]!r}", text=text)

    if label_fn is not None:
        label = label_fn(subject, text)
        if label in ("opt_out", "wrong_person"):
            return Verdict("opt_out", f"classifier: {label}", text=text, label=label)
        if label:
            return Verdict("reply", f"classifier: {label}", text=text, label=label)
    return Verdict("reply", "a person replied", text=text)
