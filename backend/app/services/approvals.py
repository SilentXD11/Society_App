"""Dual-signatory approval for outgoing payments.

Rules (enforced here, inside one transaction holding a row lock):
- only active committee members flagged as signatories may approve;
- with maker-checker on, whoever raised the payment can't approve it;
- one approval per person (also enforced by the table's primary key);
- `approvals_required` distinct approvals move it to 'approved';
- only an approved payment can be marked paid.
"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Membership, PaymentApproval, PaymentRequest, RecurringBill
from app.services import audit
from app.services.bills import rewind


class ApprovalError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


async def _locked(session: AsyncSession, society_id: uuid.UUID, payment_id: uuid.UUID) -> PaymentRequest:
    p = (await session.execute(select(PaymentRequest).where(
        PaymentRequest.id == payment_id, PaymentRequest.society_id == society_id).with_for_update())).scalar_one_or_none()
    if p is None:
        raise ApprovalError("Payment not found.", 404)
    return p


async def approve(session: AsyncSession, *, society_id: uuid.UUID, payment_id: uuid.UUID,
                  user_id: uuid.UUID, method: str) -> PaymentRequest:
    s = get_settings()
    p = await _locked(session, society_id, payment_id)
    if p.status != "pending_approval":
        raise ApprovalError(f"This payment is already {p.status.replace('_', ' ')}.")
    signatory = await session.scalar(select(Membership.is_signatory).where(
        Membership.society_id == society_id, Membership.user_id == user_id,
        Membership.active, Membership.role == "committee"))
    if not signatory:
        raise ApprovalError("Only bank signatories can approve payments.", 403)
    if s.maker_checker and p.requested_by == user_id:
        raise ApprovalError("You raised this payment, so a different signatory must approve it.", 403)
    already = await session.get(PaymentApproval, (payment_id, user_id))
    if already:
        raise ApprovalError("You’ve already approved this payment.")

    session.add(PaymentApproval(payment_id=payment_id, user_id=user_id, method=method))
    await session.flush()
    count = await session.scalar(select(func.count()).select_from(PaymentApproval)
                                 .where(PaymentApproval.payment_id == payment_id))
    if count >= s.approvals_required:
        p.status = "approved"
    await audit.record(session, society_id=society_id, actor=user_id, action="payment.approve",
                       entity="payment_request", entity_id=p.id,
                       detail={"approvals": count, "of": s.approvals_required, "method": method,
                               "amount": p.amount, "payee": p.payee})
    return p


async def reject(session: AsyncSession, *, society_id: uuid.UUID, payment_id: uuid.UUID,
                 user_id: uuid.UUID, reason: str) -> PaymentRequest:
    p = await _locked(session, society_id, payment_id)
    if p.status not in ("pending_approval", "approved"):
        raise ApprovalError(f"A {p.status} payment can’t be rejected.")
    p.status, p.reject_reason = "rejected", reason
    if p.recurring_bill_id and p.bill_due_date:
        bill = await session.get(RecurringBill, p.recurring_bill_id, with_for_update=True)
        if bill:
            rewind(bill, p.bill_due_date)
    await audit.record(session, society_id=society_id, actor=user_id, action="payment.reject",
                       entity="payment_request", entity_id=p.id, detail={"reason": reason})
    return p


async def mark_paid(session: AsyncSession, *, society_id: uuid.UUID, payment_id: uuid.UUID,
                    user_id: uuid.UUID, paid_on, reference: str | None) -> PaymentRequest:
    p = await _locked(session, society_id, payment_id)
    if p.status != "approved":
        raise ApprovalError("Only a fully approved payment can be marked paid.")
    p.status, p.paid_on, p.reference, p.paid_by = "paid", paid_on, reference, user_id
    await audit.record(session, society_id=society_id, actor=user_id, action="payment.paid",
                       entity="payment_request", entity_id=p.id,
                       detail={"paid_on": paid_on, "reference": reference, "amount": p.amount})
    return p
