"""Outgoing SMS / email.

`console` providers print instead of sending (dev and tests).
The MSG91 and SES providers are written against those services' public
APIs but have not been run against live accounts — test them with your
own credentials before relying on them.
"""
import logging
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.config import get_settings

log = logging.getLogger("notifier")


@dataclass
class SendResult:
    ok: bool
    provider_message_id: str | None = None
    error: str | None = None


class Notifier(Protocol):
    async def send_sms(self, to: str, body: str) -> SendResult: ...
    async def send_email(self, to: str, subject: str, body: str) -> SendResult: ...


@dataclass
class ConsoleNotifier:
    """Prints messages; also keeps them so tests can read the OTP."""

    outbox: list[dict] = field(default_factory=list)

    async def send_sms(self, to: str, body: str) -> SendResult:
        self.outbox.append({"channel": "sms", "to": to, "body": body})
        print(f"[console SMS] to {to}: {body}", flush=True)
        return SendResult(ok=True, provider_message_id=f"console-{len(self.outbox)}")

    async def send_email(self, to: str, subject: str, body: str) -> SendResult:
        self.outbox.append({"channel": "email", "to": to, "subject": subject, "body": body})
        print(f"[console email] to {to}: {subject}\n{body}", flush=True)
        return SendResult(ok=True, provider_message_id=f"console-{len(self.outbox)}")


class Msg91Notifier:
    """SMS through MSG91's Flow API (India). Needs a DLT-approved template whose
    single variable is the message body (##body##)."""

    url = "https://control.msg91.com/api/v5/flow/"

    def __init__(self, fallback: Notifier):
        s = get_settings()
        self.auth_key, self.flow_id, self.sender = s.msg91_auth_key, s.msg91_flow_id, s.msg91_sender_id
        self.fallback = fallback

    async def send_sms(self, to: str, body: str) -> SendResult:
        payload = {"template_id": self.flow_id, "sender": self.sender, "short_url": "0",
                   "recipients": [{"mobiles": to.lstrip("+"), "body": body}]}
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(self.url, json=payload, headers={"authkey": self.auth_key})
            data = r.json() if r.content else {}
            if r.status_code == 200 and data.get("type") == "success":
                return SendResult(ok=True, provider_message_id=str(data.get("message")))
            return SendResult(ok=False, error=f"MSG91 {r.status_code}: {data}")
        except httpx.HTTPError as e:
            return SendResult(ok=False, error=f"MSG91 request failed: {e}")

    async def send_email(self, to: str, subject: str, body: str) -> SendResult:
        return await self.fallback.send_email(to, subject, body)


class SesNotifier:
    """Email through Amazon SES (requires boto3 and a verified sender)."""

    def __init__(self, sms: Notifier):
        import boto3  # optional dependency

        s = get_settings()
        self.client = boto3.client("ses", region_name=s.aws_region)
        self.sender = s.ses_from_address
        self.sms = sms

    async def send_sms(self, to: str, body: str) -> SendResult:
        return await self.sms.send_sms(to, body)

    async def send_email(self, to: str, subject: str, body: str) -> SendResult:
        import anyio

        def _send():
            return self.client.send_email(
                Source=self.sender, Destination={"ToAddresses": [to]},
                Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}})
        try:
            r = await anyio.to_thread.run_sync(_send)
            return SendResult(ok=True, provider_message_id=r.get("MessageId"))
        except Exception as e:  # botocore raises many types
            return SendResult(ok=False, error=f"SES failed: {e}")


_notifier: Notifier | None = None


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        s = get_settings()
        n: Notifier = ConsoleNotifier()
        if s.sms_provider == "msg91":
            n = Msg91Notifier(fallback=n)
        if s.email_provider == "ses":
            n = SesNotifier(sms=n)
        _notifier = n
    return _notifier


def set_notifier(n: Notifier) -> None:
    """For tests."""
    global _notifier
    _notifier = n
