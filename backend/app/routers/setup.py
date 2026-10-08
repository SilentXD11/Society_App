"""First-time setup over HTTP, for hosts with no shell (e.g. Render's free tier).

POST /setup/society with header `X-Setup-Token: <SR_SETUP_TOKEN>` creates a society and
its first committee member (a bank signatory), who can then log in and add everyone else.
The endpoint is off unless SR_SETUP_TOKEN is set. Clear the variable once you're done.
"""
import hmac
from decimal import Decimal

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from app.config import get_settings
from app.models import Membership, Society
from app.routers.society import get_or_create_user
from app.security.deps import Session
from app.services import audit

router = APIRouter(prefix="/setup", tags=["setup"])


class SetupIn(BaseModel):
    society_name: str = Field(min_length=2)
    address: str | None = None
    monthly_charge: Decimal = Field(default=Decimal(0), ge=0)
    due_day: int = Field(default=10, ge=1, le=28)
    timezone: str = "Asia/Kolkata"
    currency: str = Field(default="INR", min_length=3, max_length=3)
    admin_name: str = Field(min_length=1)
    admin_phone: str | None = None
    admin_email: str | None = None
    admin_title: str = "Secretary"

    @model_validator(mode="after")
    def _contact(self):
        if not (self.admin_phone or self.admin_email):
            raise ValueError("Give admin_phone or admin_email so the first member can log in.")
        return self


@router.post("/society", status_code=status.HTTP_201_CREATED)
async def create_society(body: SetupIn, session: Session, x_setup_token: str = Header(default="")):
    expected = get_settings().setup_token
    if not expected:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not hmac.compare_digest(x_setup_token.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong setup token.")
    s = Society(name=body.society_name, address=body.address, monthly_charge=body.monthly_charge,
                due_day=body.due_day, timezone=body.timezone, currency=body.currency.upper())
    session.add(s)
    await session.flush()
    u = await get_or_create_user(session, body.admin_name, body.admin_phone, body.admin_email)
    session.add(Membership(society_id=s.id, user_id=u.id, role="committee", title=body.admin_title,
                           is_signatory=True))
    await audit.record(session, society_id=s.id, actor=None, action="society.create", entity="society",
                       entity_id=s.id, detail={"admin": u.id, "via": "setup endpoint"})
    return {"society_id": str(s.id), "admin_user_id": str(u.id),
            "next": "Log in with POST /auth/otp/request using the admin phone or email. "
                    "Then remove SR_SETUP_TOKEN from the server's environment."}
