"""Recurring bills: working out the amount, and moving a bill on to its next due date."""
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RecurringBill, Staff
from app.services.schedule import next_occurrence, occurrences, remaining_instalments

STAFF_ROLE = {"security_pay": ["security"], "sweeper_pay": ["sweeper"], "all_staff_pay": ["security", "sweeper", "other"]}


async def bill_amount(session: AsyncSession, bill: RecurringBill) -> Decimal:
    if bill.amount_source == "fixed":
        return bill.amount or Decimal(0)
    total = await session.scalar(
        select(func.coalesce(func.sum(Staff.monthly_pay), 0)).where(
            Staff.society_id == bill.society_id, Staff.left_on.is_(None),
            Staff.role.in_(STAFF_ROLE[bill.amount_source])))
    return Decimal(total)


def advance(bill: RecurringBill) -> date:
    """Mark the current occurrence as settled. Returns the date that was settled."""
    settled = bill.next_due
    assert settled is not None, "bill has no pending occurrence"
    bill.paid_instalments += 1
    nxt = next_occurrence(settled, bill.frequency, bill.anchor_day)
    left = remaining_instalments(bill.total_instalments, bill.paid_instalments)
    bill.next_due = None if (nxt is None or left == 0) else nxt
    return settled


def rewind(bill: RecurringBill, settled: date) -> bool:
    """Undo `advance` when a payment for `settled` is rejected — only if the bill
    hasn't moved on again since then."""
    expected = next_occurrence(settled, bill.frequency, bill.anchor_day)
    if bill.next_due == expected or (bill.next_due is None and bill.paid_instalments > 0):
        bill.next_due = settled
        bill.paid_instalments = max(0, bill.paid_instalments - 1)
        return True
    return False


def upcoming_dates(bill: RecurringBill, until: date) -> list[date]:
    if not bill.active:
        return []
    return occurrences(bill.next_due, bill.frequency, bill.anchor_day, until,
                       remaining_instalments(bill.total_instalments, bill.paid_instalments))
