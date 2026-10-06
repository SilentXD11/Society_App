"""One-time codes for login and step-up confirmation.

- Codes are 6 digits from `secrets`, stored only as HMAC-SHA256 with a server pepper.
- Each code expires after SR_OTP_TTL_MINUTES and allows SR_OTP_MAX_ATTEMPTS guesses.
- Sending is rate-limited per destination.
- Requesting a login code never reveals whether the phone/email is registered.
"""
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import OtpCode, User
from app.services.notifier import get_notifier


class OtpError(Exception):
    pass


def _hash(code: str, destination: str) -> str:
    key = get_settings().otp_secret.encode()
    return hmac.new(key, f"{destination}:{code}".encode(), hashlib.sha256).hexdigest()


def normalise(destination: str) -> tuple[str, str]:
    """Returns (channel, normalised destination)."""
    d = destination.strip()
    if "@" in d:
        return "email", d.lower()
    digits = "".join(ch for ch in d if ch.isdigit())
    if d.startswith("+"):
        return "sms", "+" + digits
    if len(digits) == 10:  # assume India for bare 10-digit numbers
        return "sms", "+91" + digits
    return "sms", "+" + digits


async def find_user(session: AsyncSession, channel: str, destination: str) -> User | None:
    col = User.email if channel == "email" else User.phone
    return (await session.execute(select(User).where(func.lower(col) == destination.lower()))).scalar_one_or_none()


async def issue(session: AsyncSession, *, destination: str, purpose: str, user: User | None,
                ip: str | None = None, society_name: str = "Society Register") -> None:
    s = get_settings()
    channel, dest = normalise(destination)
    now = datetime.now(timezone.utc)
    recent = await session.scalar(
        select(func.count()).select_from(OtpCode).where(
            OtpCode.destination == dest,
            OtpCode.created_at > now - timedelta(minutes=s.otp_send_window_minutes)))
    if recent >= s.otp_max_sends_per_window:
        raise OtpError("Too many codes requested. Wait a few minutes and try again.")

    code = f"{secrets.randbelow(10**6):06d}"
    session.add(OtpCode(user_id=user.id if user else None, destination=dest, channel=channel,
                        purpose=purpose, code_hash=_hash(code, dest), request_ip=ip,
                        expires_at=now + timedelta(minutes=s.otp_ttl_minutes)))
    await session.flush()
    if user is None:
        return  # unknown destination: record the attempt, send nothing, reveal nothing

    text = (f"{code} is your {society_name} code. It expires in {s.otp_ttl_minutes} minutes. "
            "Never share it with anyone.")
    n = get_notifier()
    if channel == "sms":
        await n.send_sms(dest, text)
    else:
        await n.send_email(dest, f"Your {society_name} code", text)


async def verify(session: AsyncSession, *, destination: str, code: str, purpose: str) -> uuid.UUID:
    """Returns the user id on success; raises OtpError otherwise."""
    s = get_settings()
    _, dest = normalise(destination)
    now = datetime.now(timezone.utc)
    otp = (await session.execute(
        select(OtpCode).where(OtpCode.destination == dest, OtpCode.purpose == purpose,
                              OtpCode.consumed_at.is_(None), OtpCode.expires_at > now)
        .order_by(OtpCode.created_at.desc()).limit(1).with_for_update())).scalar_one_or_none()
    if otp is None or otp.user_id is None:
        raise OtpError("That code is wrong or has expired. Request a new one.")
    if otp.attempts >= s.otp_max_attempts:
        raise OtpError("Too many wrong attempts. Request a new code.")
    if not hmac.compare_digest(otp.code_hash, _hash(code.strip(), dest)):
        await session.execute(update(OtpCode).where(OtpCode.id == otp.id).values(attempts=OtpCode.attempts + 1))
        await session.commit()  # keep the failed attempt even though we raise
        raise OtpError("That code is wrong or has expired. Request a new one.")
    otp.consumed_at = now
    return otp.user_id
