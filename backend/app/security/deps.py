"""FastAPI dependencies: who is calling, what they may do in a society,
and whether they have recently re-confirmed for a sensitive action."""
import uuid
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Path, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Membership, Society, User
from app.security.tokens import decode

bearer = HTTPBearer(auto_error=False)
Session = Annotated[AsyncSession, Depends(get_session)]


async def current_user(session: Session,
                       creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in first.")
    try:
        claims = decode(creds.credentials, "access")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session has expired. Sign in again.")
    user = await session.get(User, uuid.UUID(claims["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


@dataclass
class Member:
    user: User
    society: Society
    membership: Membership

    @property
    def is_committee(self) -> bool:
        return self.membership.role == "committee"


async def member(society_id: Annotated[uuid.UUID, Path()], user: CurrentUser, session: Session) -> Member:
    row = (await session.execute(
        select(Membership, Society).join(Society, Society.id == Membership.society_id)
        .where(Membership.society_id == society_id, Membership.user_id == user.id, Membership.active)
    )).first()
    if row is None:
        # 404 rather than 403 so outsiders can't probe which societies exist
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Society not found.")
    return Member(user=user, society=row.Society, membership=row.Membership)


AnyMember = Annotated[Member, Depends(member)]


async def committee(m: AnyMember) -> Member:
    if not m.is_committee:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only committee members can do this.")
    return m


CommitteeMember = Annotated[Member, Depends(committee)]


async def step_up(user: CurrentUser,
                  x_step_up: Annotated[str | None, Header(alias="X-Step-Up")] = None) -> str:
    """Require a fresh OTP/passkey confirmation (token from /auth/step-up/verify).
    Returns the method used ('otp' or 'passkey')."""
    if not x_step_up:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Confirm it’s you first.",
                            headers={"X-Step-Up-Required": "true"})
    try:
        claims = decode(x_step_up, "step_up")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your confirmation has expired. Confirm again.",
                            headers={"X-Step-Up-Required": "true"})
    if claims["sub"] != str(user.id):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Confirmation belongs to another account.")
    return claims.get("amr", "otp")


StepUp = Annotated[str, Depends(step_up)]
