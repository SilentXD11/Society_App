"""Access tokens (short-lived JWT), refresh tokens (opaque, stored hashed, rotated),
and step-up tokens (very short-lived JWT proving a fresh OTP/passkey check)."""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import RefreshToken

ALG = "HS256"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def make_access_token(user_id: uuid.UUID) -> str:
    s = get_settings()
    return jwt.encode({"sub": str(user_id), "typ": "access", "iat": _now(),
                       "exp": _now() + timedelta(minutes=s.access_token_minutes)}, s.jwt_secret, ALG)


def make_step_up_token(user_id: uuid.UUID, method: str) -> str:
    s = get_settings()
    return jwt.encode({"sub": str(user_id), "typ": "step_up", "amr": method, "iat": _now(),
                       "exp": _now() + timedelta(minutes=s.step_up_minutes)}, s.jwt_secret, ALG)


def decode(token: str, typ: str) -> dict:
    claims = jwt.decode(token, get_settings().jwt_secret, algorithms=[ALG])
    if claims.get("typ") != typ:
        raise jwt.InvalidTokenError("wrong token type")
    return claims


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_refresh(session: AsyncSession, user_id: uuid.UUID, user_agent: str | None) -> str:
    token = secrets.token_urlsafe(48)
    session.add(RefreshToken(user_id=user_id, token_hash=_hash(token), user_agent=user_agent,
                             expires_at=_now() + timedelta(days=get_settings().refresh_token_days)))
    await session.flush()
    return token


async def rotate_refresh(session: AsyncSession, token: str, user_agent: str | None) -> tuple[uuid.UUID, str] | None:
    row = (await session.execute(select(RefreshToken).where(RefreshToken.token_hash == _hash(token))
                                 .with_for_update())).scalar_one_or_none()
    if row is None or row.expires_at <= _now():
        return None
    if row.revoked_at is not None:
        # A revoked token being reused suggests theft: revoke every session for this user.
        for r in (await session.execute(select(RefreshToken).where(
                RefreshToken.user_id == row.user_id, RefreshToken.revoked_at.is_(None)))).scalars():
            r.revoked_at = _now()
        await session.commit()  # must persist even though the request then fails with 401
        return None
    row.revoked_at = _now()
    return row.user_id, await issue_refresh(session, row.user_id, user_agent)


async def revoke_refresh(session: AsyncSession, token: str) -> None:
    row = (await session.execute(select(RefreshToken).where(RefreshToken.token_hash == _hash(token)))).scalar_one_or_none()
    if row and row.revoked_at is None:
        row.revoked_at = _now()
