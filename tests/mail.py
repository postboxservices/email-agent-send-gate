"""Builders for incoming test messages."""

from email.message import EmailMessage


def reply(text: str, *, subject="Re: Support cover", sender="Lee <lee@acme-corp.com>",
          in_reply_to: str = "", message_id="<r1@acme-corp.com>", headers=None, html=False) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "asha@outreach.example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    for name, value in (headers or {}).items():
        msg[name] = value
    if html:
        msg.set_content(text, subtype="html")
    else:
        msg.set_content(text)
    return msg.as_bytes()


def dsn(recipient: str, status: str, action: str = "failed", message_id="<dsn1@mx.example.net>") -> bytes:
    return (
        "From: Mail Delivery Subsystem <MAILER-DAEMON@mx.example.net>\r\n"
        "To: bounces@outreach.example.com\r\n"
        "Subject: Undelivered Mail Returned to Sender\r\n"
        f"Message-ID: {message_id}\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="B"\r\n'
        "\r\n"
        "--B\r\n"
        "Content-Type: text/plain\r\n\r\n"
        "Your message could not be delivered.\r\n"
        "--B\r\n"
        "Content-Type: message/delivery-status\r\n\r\n"
        "Reporting-MTA: dns; mx.example.net\r\n"
        "\r\n"
        f"Final-Recipient: rfc822; {recipient}\r\n"
        f"Action: {action}\r\n"
        f"Status: {status}\r\n"
        "\r\n"
        "--B--\r\n"
    ).encode()
