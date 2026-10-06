from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.config import get_settings
from app.models import Membership, Society
from app.schemas import MeOut, MembershipOut, OtpRequest, OtpVerify, RefreshIn, StepUpToken, StepUpVerify, TokenPair
from app.security import otp, tokens
from app.security.deps import CurrentUser, Session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/otp/request", status_code=status.HTTP_202_ACCEPTED)
async def request_login_code(body: OtpRequest, request: Request, session: Session):
    """Send a 6-digit login code by SMS or email. Always answers 202 so the
    response doesn't reveal whether the number or email is registered."""
    channel, dest = otp.normalise(body.destination)
    user = await otp.find_user(session, channel, dest)
    try:
        await otp.issue(session, destination=dest, purpose="login", user=user,
                        ip=request.client.host if request.client else None)
    except otp.OtpError as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e))
    return {"detail": "If that number or email is registered, a code is on its way."}


@router.post("/otp/verify", response_model=TokenPair)
async def verify_login_code(body: OtpVerify, request: Request, session: Session):
    try:
        user_id = await otp.verify(session, destination=body.destination, code=body.code, purpose="login")
    except otp.OtpError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e))
    from app.models import User
    user = await session.get(User, user_id)
    user.last_login_at = datetime.now(timezone.utc)
    refresh = await tokens.issue_refresh(session, user_id, request.headers.get("user-agent"))
    return TokenPair(access_token=tokens.make_access_token(user_id), refresh_token=refresh)


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshIn, request: Request, session: Session):
    out = await tokens.rotate_refresh(session, body.refresh_token, request.headers.get("user-agent"))
    if out is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session ended. Sign in again.")
    user_id, new_refresh = out
    return TokenPair(access_token=tokens.make_access_token(user_id), refresh_token=new_refresh)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshIn, session: Session):
    await tokens.revoke_refresh(session, body.refresh_token)


@router.post("/step-up/request", status_code=status.HTTP_202_ACCEPTED)
async def request_step_up(user: CurrentUser, request: Request, session: Session):
    """Send a code to the signed-in user's own phone (or email) before a sensitive action."""
    dest = user.phone or user.email
    try:
        await otp.issue(session, destination=dest, purpose="step_up", user=user,
                        ip=request.client.host if request.client else None)
    except otp.OtpError as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e))
    masked = dest[:3] + "•••" + dest[-2:] if user.phone else dest[0] + "•••" + dest[dest.index("@"):]
    return {"detail": f"Code sent to {masked}."}


@router.post("/step-up/verify", response_model=StepUpToken)
async def verify_step_up(body: StepUpVerify, user: CurrentUser, session: Session):
    try:
        uid = await otp.verify(session, destination=user.phone or user.email, code=body.code, purpose="step_up")
    except otp.OtpError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e))
    if uid != user.id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Code doesn’t match this account.")
    return StepUpToken(step_up_token=tokens.make_step_up_token(user.id, "otp"),
                       expires_in_seconds=get_settings().step_up_minutes * 60)


@router.get("/me", response_model=MeOut)
async def me(user: CurrentUser, session: Session):
    rows = (await session.execute(
        select(Membership, Society.name).join(Society, Society.id == Membership.society_id)
        .where(Membership.user_id == user.id, Membership.active))).all()
    return MeOut(id=user.id, full_name=user.full_name, phone=user.phone, email=user.email,
                 memberships=[MembershipOut(society_id=m.society_id, society_name=name, role=m.role,
                                            title=m.title, is_signatory=m.is_signatory) for m, name in rows])
