"""Generic SMTP submission (port 587 STARTTLS, 465 TLS, or a plain local relay)."""

import smtplib
import ssl
from email.message import EmailMessage

from gate.providers import ProviderError


class SmtpProvider:
    name = "smtp"

    def __init__(self, host: str, port: int, username: str, password: str, security: str,
                 timeout: float = 30):
        if security not in ("starttls", "tls", "none"):
            raise ProviderError("SMTP_SECURITY must be starttls, tls or none")
        if not host:
            raise ProviderError("SMTP_HOST is empty")
        self.host, self.port, self.timeout = host, port, timeout
        self.username, self.password, self.security = username, password, security

    def send(self, msg: EmailMessage) -> str:
        context = ssl.create_default_context()
        try:
            if self.security == "tls":
                smtp = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=context)
            else:
                smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            with smtp:
                if self.security == "starttls":
                    smtp.starttls(context=context)
                if self.username:
                    smtp.login(self.username, self.password)
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            raise ProviderError(f"SMTP send failed: {e}") from e
        return msg["Message-ID"]
