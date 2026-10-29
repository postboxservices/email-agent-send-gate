"""Email providers. Each one takes a finished message and hands it to a real mail system.

Only the internal gate service is given provider credentials. The fingerprint names the
provider without its secrets; `gate selftest` results are stored against it, so changing
provider (or region, host or sending domain) needs a new passing selftest before live
sending resumes.
"""

import hashlib
from email.message import EmailMessage
from typing import Protocol

from gate.config import Settings


class Provider(Protocol):
    name: str

    def send(self, msg: EmailMessage) -> str:
        """Send the message and return the provider's message id."""
        ...


class ProviderError(RuntimeError):
    pass


def describe(settings: Settings) -> str:
    if settings.provider == "smtp":
        return (
            f"smtp host={settings.smtp_host} port={settings.smtp_port} "
            f"user={settings.smtp_username} security={settings.smtp_security} "
            f"domain={settings.sending_domain}"
        )
    if settings.provider == "ses":
        return (
            f"ses region={settings.ses_region} "
            f"configuration_set={settings.ses_configuration_set} domain={settings.sending_domain}"
        )
    raise ProviderError(f"unknown PROVIDER {settings.provider!r} (use smtp or ses)")


def fingerprint(settings: Settings) -> str:
    return hashlib.sha256(describe(settings).encode()).hexdigest()[:16]


def get_provider(settings: Settings) -> Provider:
    if settings.provider == "smtp":
        from gate.providers.smtp import SmtpProvider

        return SmtpProvider(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            security=settings.smtp_security,
        )
    if settings.provider == "ses":
        from gate.providers.ses import SesProvider

        return SesProvider(region=settings.ses_region, configuration_set=settings.ses_configuration_set)
    raise ProviderError(f"unknown PROVIDER {settings.provider!r} (use smtp or ses)")
