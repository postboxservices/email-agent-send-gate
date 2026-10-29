"""Amazon SES (API v2), sending the raw message so every header we built goes out as is.

Credentials come from the usual AWS sources (environment, profile or instance role) of the
internal gate service only. Install with `pip install .[ses]`.
"""

from email.message import EmailMessage

from gate.providers import ProviderError


class SesProvider:
    name = "ses"

    def __init__(self, region: str, configuration_set: str = "", client=None):
        if client is None:
            if not region:
                raise ProviderError("SES_REGION is empty")
            import boto3

            client = boto3.client("sesv2", region_name=region)
        self.client = client
        self.configuration_set = configuration_set

    def send(self, msg: EmailMessage) -> str:
        request = {"Content": {"Raw": {"Data": msg.as_bytes()}}}
        if self.configuration_set:
            request["ConfigurationSetName"] = self.configuration_set
        try:
            response = self.client.send_email(**request)
        except Exception as e:  # botocore's errors; botocore is optional, so no import here
            raise ProviderError(f"SES send failed: {e}") from e
        # SES replaces Message-ID with <MessageId@region.amazonses.com>; inbound/ matches both.
        return response["MessageId"]
