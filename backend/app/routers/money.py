"""Maintenance, recurring bills, payments (dual approval) and the payment calendar."""
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.models import (Flat, MaintenanceBill, MaintenanceReceipt, PaymentApproval, PaymentRequest, RecurringBill,
                        Resident, Vendor)
from app.schemas import (AutoDebitIn, BillIn, BillOut, GenerateBillsIn, MaintenanceBillOut, MarkPaidIn, PaymentIn,
                         PaymentOut, RaisePaymentIn, ReceiptIn, RejectIn, UpcomingItem, UpcomingOut)
from app.security.deps import AnyMember, CommitteeMember, Session, StepUp
from app.services import approvals, audit
from app.services.bills import advance, bill_amount
from app.services.reminders import upcoming
from app.services.schedule import maintenance_due_date, month_start, today_in

router = APIRouter(prefix="/societies/{society_id}", tags=["money"])


def _err(e: approvals.ApprovalError):
    return HTTPException(e.status, str(e))


# ---------------------------------------------------------------- maintenance
async def _bill_out(session, bills: list[MaintenanceBill]) -> list[MaintenanceBillOut]:
    if not bills:
        return []
    paid = dict((await session.execute(
        select(MaintenanceReceipt.bill_id, func.sum(MaintenanceReceipt.amount))
        .where(MaintenanceReceipt.bill_id.in_([b.id for b in bills])).group_by(MaintenanceReceipt.bill_id))).all())
    return [MaintenanceBillOut.model_validate(b).model_copy(update={"paid": paid.get(b.id, Decimal(0))}) for b in bills]


@router.post("/maintenance/generate", response_model=list[MaintenanceBillOut])
async def generate_bills(body: GenerateBillsIn, m: CommitteeMember, session: Session):
    """Create the month's bill for every occupied flat that doesn't have one yet. Safe to run twice."""
    period = month_start(body.period)
    due = maintenance_due_date(period, m.society.due_day)
    flats = (await session.execute(select(Flat).where(Flat.society_id == m.society.id,
                                                      Flat.occupancy != "vacant"))).scalars().all()
    created = 0
    for f in flats:
        amount = f.monthly_charge if f.monthly_charge is not None else m.society.monthly_charge
        if not amount:
            continue
        res = await session.execute(insert(MaintenanceBill).values(
            society_id=m.society.id, flat_id=f.id, period=period, amount=amount, due_date=due)
            .on_conflict_do_nothing(index_elements=["flat_id", "period"]))
        created += res.rowcount
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="maintenance.generate",
                       entity="maintenance_bill", detail={"period": period, "created": created})
    bills = (await session.execute(select(MaintenanceBill).where(MaintenanceBill.society_id == m.society.id,
                                                                 MaintenanceBill.period == period))).scalars().all()
    return await _bill_out(session, list(bills))


@router.get("/maintenance", response_model=list[MaintenanceBillOut])
async def list_maintenance(m: CommitteeMember, session: Session, period: date | None = None,
                           unpaid_only: bool = False):
    q = select(MaintenanceBill).where(MaintenanceBill.society_id == m.society.id)
    if period:
        q = q.where(MaintenanceBill.period == month_start(period))
    if unpaid_only:
        q = q.where(MaintenanceBill.status == "unpaid")
    return await _bill_out(session, list((await session.execute(q.order_by(MaintenanceBill.due_date))).scalars()))


@router.post("/maintenance/{bill_id}/receipts", response_model=MaintenanceBillOut)
async def record_receipt(bill_id: uuid.UUID, body: ReceiptIn, m: CommitteeMember, session: Session):
    """Record money received. Part-payments are allowed; the bill becomes paid when fully covered."""
    b = (await session.execute(select(MaintenanceBill).where(MaintenanceBill.id == bill_id,
                                                             MaintenanceBill.society_id == m.society.id)
                               .with_for_update())).scalar_one_or_none()
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bill not found.")
    if b.status != "unpaid":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This bill is already {b.status}.")
    session.add(MaintenanceReceipt(bill_id=b.id, recorded_by=m.user.id, **body.model_dump()))
    await session.flush()
    paid = await session.scalar(select(func.sum(MaintenanceReceipt.amount)).where(MaintenanceReceipt.bill_id == b.id))
    if paid >= b.amount:
        b.status = "paid"
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="maintenance.receipt",
                       entity="maintenance_bill", entity_id=b.id, detail=body.model_dump())
    return (await _bill_out(session, [b]))[0]


@router.get("/my-dues", response_model=list[MaintenanceBillOut])
async def my_dues(m: AnyMember, session: Session):
    """For a resident: bills for the flats they're a current resident of."""
    flat_ids = select(Resident.flat_id).where(Resident.user_id == m.user.id, Resident.moved_out.is_(None),
                                              Resident.society_id == m.society.id)
    bills = (await session.execute(select(MaintenanceBill).where(MaintenanceBill.flat_id.in_(flat_ids))
                                   .order_by(MaintenanceBill.period.desc()))).scalars().all()
    return await _bill_out(session, list(bills))


# ---------------------------------------------------------------- recurring bills
async def _billout(session, b: RecurringBill) -> BillOut:
    return BillOut.model_validate(b).model_copy(update={"current_amount": await bill_amount(session, b)})


async def _get_bill(session, m, bill_id) -> RecurringBill:
    b = (await session.execute(select(RecurringBill).where(RecurringBill.id == bill_id,
                                                           RecurringBill.society_id == m.society.id)
                               .with_for_update())).scalar_one_or_none()
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bill not found.")
    return b


@router.get("/bills", response_model=list[BillOut])
async def list_bills(m: CommitteeMember, session: Session):
    bills = (await session.execute(select(RecurringBill).where(RecurringBill.society_id == m.society.id)
                                   .order_by(RecurringBill.next_due.nulls_last()))).scalars().all()
    return [await _billout(session, b) for b in bills]


@router.post("/bills", response_model=BillOut, status_code=status.HTTP_201_CREATED)
async def add_bill(body: BillIn, m: CommitteeMember, session: Session):
    b = RecurringBill(society_id=m.society.id, anchor_day=body.next_due.day, **body.model_dump())
    session.add(b)
    await session.flush()
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="bill.create",
                       entity="recurring_bill", entity_id=b.id, detail=body.model_dump())
    return await _billout(session, b)


@router.put("/bills/{bill_id}", response_model=BillOut)
async def update_bill(bill_id: uuid.UUID, body: BillIn, m: CommitteeMember, session: Session):
    b = await _get_bill(session, m, bill_id)
    for k, v in body.model_dump().items():
        setattr(b, k, v)
    b.anchor_day = body.next_due.day
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="bill.update",
                       entity="recurring_bill", entity_id=b.id, detail=body.model_dump())
    return await _billout(session, b)


@router.post("/bills/{bill_id}/raise-payment", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
async def raise_bill_payment(bill_id: uuid.UUID, body: RaisePaymentIn, m: CommitteeMember, session: Session):
    """Send this bill's current occurrence for signatory approval and move the bill to its next due date.
    If the payment is later rejected, the bill moves back."""
    b = await _get_bill(session, m, bill_id)
    if b.next_due is None or not b.active:
        raise HTTPException(status.HTTP_409_CONFLICT, "This bill has nothing due.")
    if b.autopay:
        raise HTTPException(status.HTTP_409_CONFLICT, "This bill is auto-debited. Record the debit instead.")
    amount = body.amount or await bill_amount(session, b)
    payee = b.payee or (await session.get(Vendor, b.vendor_id)).name
    due = advance(b)
    p = PaymentRequest(society_id=m.society.id, recurring_bill_id=b.id, bill_due_date=due, vendor_id=b.vendor_id,
                       payee=payee, purpose=f"{b.name} — due {due:%d %b %Y}", category=b.category, amount=amount,
                       mode=body.mode, cheque_no=body.cheque_no, requested_by=m.user.id)
    session.add(p)
    await session.flush()
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="payment.raise",
                       entity="payment_request", entity_id=p.id, detail={"bill": b.name, "due": due, "amount": amount})
    return PaymentOut.model_validate(p)


@router.post("/bills/{bill_id}/auto-debit", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
async def record_auto_debit(bill_id: uuid.UUID, body: AutoDebitIn, m: CommitteeMember, session: Session):
    """For standing instructions (e.g. loan EMIs): the bank has already paid, so no approval is needed."""
    b = await _get_bill(session, m, bill_id)
    if not b.autopay:
        raise HTTPException(status.HTTP_409_CONFLICT, "This bill isn’t set up as an auto-debit.")
    if b.next_due is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This bill has nothing due.")
    amount = body.amount or await bill_amount(session, b)
    payee = b.payee or (await session.get(Vendor, b.vendor_id)).name
    due = advance(b)
    p = PaymentRequest(society_id=m.society.id, recurring_bill_id=b.id, bill_due_date=due, vendor_id=b.vendor_id,
                       payee=payee, purpose=f"{b.name} — due {due:%d %b %Y} (auto-debit)", category=b.category,
                       amount=amount, mode="auto_debit", requested_by=m.user.id, status="paid",
                       paid_on=body.debited_on, reference=body.reference, paid_by=m.user.id)
    session.add(p)
    await session.flush()
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="payment.auto_debit",
                       entity="payment_request", entity_id=p.id, detail={"bill": b.name, "due": due, "amount": amount})
    return PaymentOut.model_validate(p)


@router.get("/upcoming", response_model=UpcomingOut)
async def payment_calendar(m: AnyMember, session: Session, days: int = Query(90, ge=1, le=366)):
    """Everything to pay and to receive in the next `days` days, oldest first (overdue included)."""
    today = today_in(m.society.timezone)
    items = await upcoming(session, m.society, today, days)
    if not m.is_committee:  # residents see only society-level maintenance expectations
        items = [i for i in items if i.kind == "maintenance"]
    out = [UpcomingItem(due=i.due, kind=i.kind, title=i.title, amount=i.amount, direction=i.direction,
                        ref_id=i.ref_id, note=i.note, autopay=i.autopay, days_until=(i.due - today).days) for i in items]
    return UpcomingOut(today=today, days=days,
                       to_pay_total=sum((i.amount for i in out if i.direction == "out"), Decimal(0)),
                       coming_in_total=sum((i.amount for i in out if i.direction == "in"), Decimal(0)),
                       overdue_total=sum((i.amount for i in out if i.direction == "out" and i.days_until < 0), Decimal(0)),
                       items=out)


# ---------------------------------------------------------------- payments
async def _payment_out(session, p: PaymentRequest) -> PaymentOut:
    aps = (await session.execute(select(PaymentApproval).where(PaymentApproval.payment_id == p.id)
                                 .order_by(PaymentApproval.approved_at))).scalars().all()
    return PaymentOut.model_validate(p).model_copy(update={"approvals": aps})


@router.get("/payments", response_model=list[PaymentOut])
async def list_payments(m: CommitteeMember, session: Session, status_: str | None = Query(None, alias="status")):
    q = select(PaymentRequest).where(PaymentRequest.society_id == m.society.id)
    if status_:
        q = q.where(PaymentRequest.status == status_)
    ps = (await session.execute(q.order_by(PaymentRequest.requested_at.desc()).limit(500))).scalars().all()
    return [await _payment_out(session, p) for p in ps]


@router.post("/payments", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
async def raise_payment(body: PaymentIn, m: CommitteeMember, session: Session):
    payee = body.payee
    if body.vendor_id:
        v = await session.get(Vendor, body.vendor_id)
        if v is None or v.society_id != m.society.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Vendor not found.")
        payee = payee or v.name
    p = PaymentRequest(society_id=m.society.id, requested_by=m.user.id, **{**body.model_dump(), "payee": payee})
    session.add(p)
    await session.flush()
    await audit.record(session, society_id=m.society.id, actor=m.user.id, action="payment.raise",
                       entity="payment_request", entity_id=p.id, detail=body.model_dump())
    return await _payment_out(session, p)


@router.post("/payments/{payment_id}/approve", response_model=PaymentOut)
async def approve_payment(payment_id: uuid.UUID, m: CommitteeMember, session: Session, method: StepUp):
    try:
        p = await approvals.approve(session, society_id=m.society.id, payment_id=payment_id,
                                    user_id=m.user.id, method=method)
    except approvals.ApprovalError as e:
        raise _err(e)
    return await _payment_out(session, p)


@router.post("/payments/{payment_id}/reject", response_model=PaymentOut)
async def reject_payment(payment_id: uuid.UUID, body: RejectIn, m: CommitteeMember, session: Session, _: StepUp):
    try:
        p = await approvals.reject(session, society_id=m.society.id, payment_id=payment_id,
                                   user_id=m.user.id, reason=body.reason)
    except approvals.ApprovalError as e:
        raise _err(e)
    return await _payment_out(session, p)


@router.post("/payments/{payment_id}/mark-paid", response_model=PaymentOut)
async def mark_payment_paid(payment_id: uuid.UUID, body: MarkPaidIn, m: CommitteeMember, session: Session):
    try:
        p = await approvals.mark_paid(session, society_id=m.society.id, payment_id=payment_id,
                                      user_id=m.user.id, paid_on=body.paid_on, reference=body.reference)
    except approvals.ApprovalError as e:
        raise _err(e)
    return await _payment_out(session, p)
