"""Fixed deposits, vendors, staff, reminders run, audit log."""
import uuid
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import AuditLog, FixedDeposit, Staff, Vendor
from app.schemas import (AuditOut, FdCloseIn, FdIn, FdOut, FdRenewIn, FdSummary, StaffIn, StaffOut, VendorIn,
                         VendorOut)
from app.security.deps import CommitteeMember, Session, StepUp
from app.services import audit
from app.services.reminders import run_for_society
from app.services.schedule import add_days, add_months, fd_maturity_amount, today_in

router = APIRouter(prefix="/societies/{society_id}", tags=["records"])


# ---------------------------------------------------------------- fixed deposits
def _fd_out(f: FixedDeposit, today) -> FdOut:
    return FdOut.model_validate(f).model_copy(
        update={"days_to_maturity": (f.maturity_date - today).days if f.status == "active" else None})


async def _get_fd(session, m, fd_id) -> FixedDeposit:
    f = (await session.execute(select(FixedDeposit).where(FixedDeposit.id == fd_id,
                                                          FixedDeposit.society_id == m.society.id)
                               .with_for_update())).scalar_one_or_none()
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Fixed deposit not found.")
    return f


@router.get("/fds", response_model=list[FdOut])
async def list_fds(m: CommitteeMember, session: Session, status_: str | None = Query(None, alias="status")):
    q = select(FixedDeposit).where(FixedDeposit.society_id == m.society.id)
    if status_:
        q = q.where(FixedDeposit.status == status_)
    today = today_in(m.society.timezone)
    return [_fd_out(f, today) for f in (await session.execute(q.order_by(FixedDeposit.maturity_date))).scalars()]


@router.get("/fds/summary", response_model=FdSummary)
async def fd_summary(m: CommitteeMember, session: Session):
    today = today_in(m.society.timezone)
    act = (await session.execute(select(FixedDeposit).where(FixedDeposit.society_id == m.society.id,
                                                            FixedDeposit.status == "active"))).scalars().all()
    return FdSummary(
        active_count=len(act),
        total_principal=sum((f.principal for f in act), Decimal(0)),
        total_maturity_value=sum((f.maturity_amount or f.principal for f in act), Decimal(0)),
        yearly_interest_estimate=sum((f.principal * (f.rate_pct or 0) / 100 for f in act), Decimal(0)).quantize(Decimal("0.01")),
        maturing_30_days=sum(1 for f in act if f.maturity_date <= add_days(today, 30)),
        maturing_90_days=sum(1 for f in act if f.maturity_date <= add_days(today, 90)),
        next_maturity=min((f.maturity_date for f in act), default=None))


@router.post("/fds", response_model=FdOut, status_code=status.HTTP_201_CREATED)
async def add_fd(body: FdIn, m: CommitteeMember, session: Session):
    data = body.model_dump()
    data["fd_last4"] = data.pop("fd_number")
    if data["maturity_amount"] is None and body.rate_pct is not None:
        data["maturity_amount"] = fd_maturity_amount(body.principal, body.rate_pct, body.start_date,
                                                     body.maturity_date, body.payout)
    f = FixedDeposit(society_id=m.society.id, **data)
    session.add(f)
    await session.flush()
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="fd.create", entity="fixed_deposit",
                       entity_id=f.id, detail={**data})
    return _fd_out(f, today_in(m.society.timezone))


@router.post("/fds/{fd_id}/renew", response_model=FdOut, status_code=status.HTTP_201_CREATED)
async def renew_fd(fd_id: uuid.UUID, body: FdRenewIn, m: CommitteeMember, session: Session, _: StepUp):
    """Close this FD as 'renewed' and open the next one from its maturity date."""
    old = await _get_fd(session, m, fd_id)
    if old.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This FD is already {old.status}.")
    payout = body.payout or old.payout
    principal = body.principal or (old.maturity_amount if old.payout == "cumulative" and old.maturity_amount else old.principal)
    start = old.maturity_date
    maturity = add_months(start, body.months)
    new = FixedDeposit(society_id=m.society.id, bank=old.bank, branch=old.branch, fd_last4=old.fd_last4,
                       principal=principal, rate_pct=body.rate_pct, payout=payout, start_date=start,
                       maturity_date=maturity, auto_renew=old.auto_renew if body.auto_renew is None else body.auto_renew,
                       maturity_amount=fd_maturity_amount(principal, body.rate_pct, start, maturity, payout),
                       holder=old.holder, nominee=old.nominee, renewed_from_id=old.id)
    session.add(new)
    old.status, old.closed_on, old.closed_amount = "renewed", start, old.maturity_amount
    await session.flush()
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="fd.renew", entity="fixed_deposit",
                       entity_id=new.id, detail={"from": old.id, "principal": principal, "rate": body.rate_pct,
                                                 "maturity": maturity})
    return _fd_out(new, today_in(m.society.timezone))


@router.post("/fds/{fd_id}/close", response_model=FdOut)
async def close_fd(fd_id: uuid.UUID, body: FdCloseIn, m: CommitteeMember, session: Session, _: StepUp):
    f = await _get_fd(session, m, fd_id)
    if f.status not in ("active", "matured"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"This FD is already {f.status}.")
    f.status, f.closed_on, f.closed_amount = "closed", body.closed_on, body.amount_credited
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="fd.close", entity="fixed_deposit",
                       entity_id=f.id, detail=body.model_dump())
    return _fd_out(f, today_in(m.society.timezone))


# ---------------------------------------------------------------- vendors & staff
@router.get("/vendors", response_model=list[VendorOut])
async def list_vendors(m: CommitteeMember, session: Session):
    return (await session.execute(select(Vendor).where(Vendor.society_id == m.society.id)
                                  .order_by(Vendor.name))).scalars().all()


@router.post("/vendors", response_model=VendorOut, status_code=status.HTTP_201_CREATED)
async def add_vendor(body: VendorIn, m: CommitteeMember, session: Session):
    v = Vendor(society_id=m.society.id, **body.model_dump())
    session.add(v)
    try:
        await session.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A vendor called {body.name} already exists.")
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="vendor.create", entity="vendor",
                       entity_id=v.id, detail=body.model_dump())
    return v


@router.get("/staff", response_model=list[StaffOut])
async def list_staff(m: CommitteeMember, session: Session, role: str | None = None):
    q = select(Staff).where(Staff.society_id == m.society.id)
    if role:
        q = q.where(Staff.role == role)
    return (await session.execute(q.order_by(Staff.full_name))).scalars().all()


@router.post("/staff", response_model=StaffOut, status_code=status.HTTP_201_CREATED)
async def add_staff(body: StaffIn, m: CommitteeMember, session: Session):
    s = Staff(society_id=m.society.id, **body.model_dump())
    session.add(s)
    await session.flush()
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="staff.create", entity="staff",
                       entity_id=s.id, detail=body.model_dump())
    return s


# ---------------------------------------------------------------- reminders & audit
@router.post("/reminders/run")
async def run_reminders_now(m: CommitteeMember, session: Session):
    """Run today's reminder check for this society now (the daily job does this automatically).
    Messages already sent for the same stage are never repeated."""
    return await run_for_society(session, m.society)


@router.get("/audit", response_model=list[AuditOut])
async def audit_log(m: CommitteeMember, session: Session, limit: int = Query(200, le=1000)):
    return (await session.execute(select(AuditLog).where(AuditLog.society_id == m.society.id)
                                  .order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit))).scalars().all()
