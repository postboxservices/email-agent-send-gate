"""Turn an approved draft into the exact message the provider sends."""

from email import policy
from email.message import EmailMessage
from email.utils import formataddr, formatdate

from gate.config import Settings
from gate.headers import unsubscribe_headers

# CRLF line endings, and long header lines kept whole (RFC 5322 allows 998 characters).
# With the default 78, Python RFC 2047-encodes a long List-Unsubscribe URL, which breaks it.
POLICY = policy.SMTP.clone(max_line_length=998)


def footer(settings: Settings, unsubscribe_link: str) -> str:
    return (
        "-- \n"
        f"{settings.sender_name}, {settings.company_name}\n"
        f"{settings.postal_address}\n"
        f"Unsubscribe with one click: {unsubscribe_link}\n"
    )


def build_message(
    settings: Settings,
    *,
    send_id: str,
    to_addr: str,
    to_name: str,
    subject: str,
    body_text: str,
) -> EmailMessage:
    unsub = unsubscribe_headers(settings.unsubscribe_base_url, settings.unsubscribe_secret, send_id)
    msg = EmailMessage(policy=POLICY)
    msg["From"] = formataddr((settings.sender_name, settings.from_address))
    msg["To"] = formataddr((to_name, to_addr))
    if settings.reply_to:
        msg["Reply-To"] = settings.reply_to
    msg["Subject"] = subject
    msg["Date"] = formatdate(usegmt=True)
    # Replies quote this id in In-Reply-To, which is how inbound/ matches them to a send.
    msg["Message-ID"] = f"<{send_id}@{settings.sending_domain}>"
    for name, value in unsub.items():
        msg[name] = value
    link = unsub["List-Unsubscribe"].strip("<>")
    msg.set_content(body_text.rstrip() + "\n\n" + footer(settings, link))
    return msg
