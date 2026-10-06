"""What's coming up, and who to tell.

`upcoming()`  — the payment calendar shown in the app (bills, EMIs, FD maturities,
                approved payments to make, maintenance expected from residents).
`run_for_society()` — the daily job: sends each reminder once per stage, using
                notification_log.dedupe_key so re-running the job never repeats a message.

Who gets what:
- Committee: one digest (email, else SMS) on any day when at least one item
  newly reaches a reminder stage. Stages: bill/EMI at its lead time, on the due
  day and when overdue; FD at 30 days, 7 days, on maturity and after.
- Residents (current owners): maintenance SMS 3 days before the due date,
  3 days after it and 10 days after it, while unpaid.
"""
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (FixedDeposit, Flat, MaintenanceBill, Membership, NotificationLog, PaymentRequest,
                        RecurringBill, Resident, Society, User, Vendor)
from app.services.bills import bill_amount, upcoming_dates
from app.services.notifier import get_notifier
from app.services.schedule import add_days, add_months, bucket, days_until, maintenance_due_date, month_start, today_in


def fmt_money(amount: Decimal, currency: str = "INR") -> str:
    """₹1,23,456 for INR (Indian grouping); "GBP 1,234" otherwise."""
    n = int(round(amount))
    sign, digits = ("-" if n < 0 else ""), str(abs(n))
    if currency != "INR":
        return f"{sign}{currency} {abs(n):,}"
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        digits = ",".join([head, *parts, tail]) if head else ",".join([*parts, tail])
    return f"{sign}₹{digits}"


@dataclass
class Item:
    due: date
    kind: str            # bill | emi | fd_maturity | approved_payment | maintenance
    title: str
    amount: Decimal
    direction: str       # out | in
    ref_id: uuid.UUID | None = None
    note: str = ""
    autopay: bool = False
    extra: dict = field(default_factory=dict)


async def upcoming(session: AsyncSession, society: Society, today: date, days: int = 90) -> list[Item]:
    until = add_days(today, days)
    items: list[Item] = []
    vendors = {v.id: v.name for v in (await session.execute(
        select(Vendor).where(Vendor.society_id == society.id))).scalars()}

    for b in (await session.execute(select(RecurringBill).where(
            RecurringBill.society_id == society.id, RecurringBill.active))).scalars():
        amt = await bill_amount(session, b)
        for d in upcoming_dates(b, until):
            items.append(Item(d, "emi" if b.total_instalments else "bill", b.name, amt, "out", b.id,
                              b.payee or vendors.get(b.vendor_id, ""), b.autopay))

    for p in (await session.execute(select(PaymentRequest).where(
            PaymentRequest.society_id == society.id, PaymentRequest.status == "approved"))).scalars():
        items.append(Item(p.bill_due_date or today, "approved_payment", p.payee, p.amount, "out", p.id, p.purpose))

    for f in (await session.execute(select(FixedDeposit).where(
            FixedDeposit.society_id == society.id, FixedDeposit.status == "active",
            FixedDeposit.maturity_date <= until))).scalars():
        items.append(Item(f.maturity_date, "fd_maturity", f"{f.bank} FD" + (f" •••{f.fd_last4}" if f.fd_last4 else ""),
                          f.maturity_amount or f.principal, "in", f.id,
                          "auto-renews" if f.auto_renew else "decide: renew or close"))

    # Maintenance expected each month in the window (unpaid bills, or projected if not yet created)
    flats = list((await session.execute(select(Flat).where(Flat.society_id == society.id))).scalars())
    period = month_start(today)
    while period <= until:
        due = maintenance_due_date(period, society.due_day)
        if due >= today:
            bills = list((await session.execute(select(MaintenanceBill).where(
                MaintenanceBill.society_id == society.id, MaintenanceBill.period == period))).scalars())
            if bills:
                amt = sum((b.amount for b in bills if b.status == "unpaid"), Decimal(0))
            else:
                amt = sum((f.monthly_charge if f.monthly_charge is not None else society.monthly_charge
                           for f in flats if f.occupancy != "vacant"), Decimal(0))
            if amt and due <= until:
                items.append(Item(due, "maintenance", f"{period:%B %Y} maintenance", amt, "in",
                                  note="bills created" if bills else "projected"))
        period = add_months(period, 1)

    return sorted(items, key=lambda i: (i.due, i.direction != "out"))


# ---------------------------------------------------------------- the daily job

STAGES_BILL = lambda lead: (lead, 0, -1)          # lead time, due day, overdue
STAGES_FD = (30, 7, 0, -1)


def _stage(days: int, stages: tuple[int, ...]) -> int | None:
    if days < 0:
        return -1
    return bucket(days, tuple(s for s in stages if s >= 0))


async def _claim(session: AsyncSession, *, key: str, society_id, user_id, channel: str, dest: str,
                 template: str, body: str) -> int | None:
    """Insert a log row; returns its id only if this key has never been used."""
    stmt = insert(NotificationLog).values(society_id=society_id, user_id=user_id, channel=channel,
                                          destination=dest, template=template, body=body, dedupe_key=key,
                                          status="queued").on_conflict_do_nothing(index_elements=["dedupe_key"]) \
        .returning(NotificationLog.id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _deliver(session: AsyncSession, log_id: int, channel: str, dest: str, subject: str, body: str) -> None:
    from datetime import datetime, timezone
    n = get_notifier()
    res = await (n.send_email(dest, subject, body) if channel == "email" else n.send_sms(dest, body))
    row = await session.get(NotificationLog, log_id)
    row.status = "sent" if res.ok else "failed"
    row.provider_message_id, row.error = res.provider_message_id, res.error
    row.sent_at = datetime.now(timezone.utc) if res.ok else None


async def run_for_society(session: AsyncSession, society: Society, today: date | None = None) -> dict:
    today = today or today_in(society.timezone)
    cur = society.currency
    fresh: list[str] = []

    # --- committee items that newly reached a stage today
    for b in (await session.execute(select(RecurringBill).where(
            RecurringBill.society_id == society.id, RecurringBill.active,
            RecurringBill.next_due.is_not(None)))).scalars():
        d = days_until(b.next_due, today)
        st = _stage(d, STAGES_BILL(b.lead_days))
        if st is None:
            continue
        key = f"item:bill:{b.id}:{b.next_due}:{st}"
        amt = await bill_amount(session, b)
        when = "overdue" if d < 0 else "due today" if d == 0 else f"due {b.next_due:%d %b}"
        line = f"{b.name}: {fmt_money(amt, cur)} {when}" + (" (auto-debit — keep balance ready)" if b.autopay else "")
        if await _claim(session, key=key, society_id=society.id, user_id=None, channel="marker",
                        dest="committee", template="bill", body=line):
            fresh.append(line)

    for f in (await session.execute(select(FixedDeposit).where(
            FixedDeposit.society_id == society.id, FixedDeposit.status == "active"))).scalars():
        d = days_until(f.maturity_date, today)
        st = _stage(d, STAGES_FD)
        if st is None:
            continue
        key = f"item:fd:{f.id}:{f.maturity_date}:{st}"
        action = "auto-renews — confirm the new rate" if f.auto_renew else "decide whether to renew or close"
        when = "has matured" if d < 0 else "matures today" if d == 0 else f"matures {f.maturity_date:%d %b} ({d} days)"
        line = (f"FD {f.bank}{' •••' + f.fd_last4 if f.fd_last4 else ''} "
                f"{fmt_money(f.principal, cur)} → {fmt_money(f.maturity_amount or f.principal, cur)} {when}; {action}")
        if await _claim(session, key=key, society_id=society.id, user_id=None, channel="marker",
                        dest="committee", template="fd", body=line):
            fresh.append(line)

    sent = 0
    if fresh:
        committee = (await session.execute(
            select(User).join(Membership, Membership.user_id == User.id).where(
                Membership.society_id == society.id, Membership.active, Membership.role == "committee"))).scalars().all()
        body = f"{society.name} — reminders for {today:%a %d %b}\n\n" + "\n".join(f"• {x}" for x in fresh)
        for u in committee:
            channel, dest = ("email", u.email) if u.email else ("sms", u.phone)
            log_id = await _claim(session, key=f"digest:{society.id}:{u.id}:{today}", society_id=society.id,
                                  user_id=u.id, channel=channel, dest=dest, template="committee_digest", body=body)
            if log_id:
                await _deliver(session, log_id, channel, dest, f"{society.name}: {len(fresh)} reminder(s)", body)
                sent += 1

    # --- residents: maintenance reminders
    rows = (await session.execute(
        select(MaintenanceBill, Flat).join(Flat, Flat.id == MaintenanceBill.flat_id).where(
            MaintenanceBill.society_id == society.id, MaintenanceBill.status == "unpaid"))).all()
    for bill, flat in rows:
        d = days_until(bill.due_date, today)
        stage = "pre" if d == 3 else "over3" if -10 < d <= -3 else "over10" if d <= -10 else None
        if stage is None:
            continue
        owners = (await session.execute(select(Resident).where(
            Resident.flat_id == flat.id, Resident.kind == "owner", Resident.moved_out.is_(None)))).scalars().all()
        for r in owners:
            if not (r.phone or r.email):
                continue
            channel, dest = ("sms", r.phone) if r.phone else ("email", r.email)
            text = (f"Dear {r.full_name}, maintenance of {fmt_money(bill.amount, cur)} for flat {flat.label} "
                    f"({bill.period:%B %Y}) " + ("is due on " + f"{bill.due_date:%d %b}." if stage == "pre"
                                                   else f"was due on {bill.due_date:%d %b} and is still unpaid.")
                    + (f" Pay: {society.pay_info}." if society.pay_info else "")
                    + f" Ignore if already paid. — {society.name}")
            log_id = await _claim(session, key=f"maint:{bill.id}:{r.id}:{stage}", society_id=society.id,
                                  user_id=r.user_id, channel=channel, dest=dest, template=f"maintenance_{stage}", body=text)
            if log_id:
                await _deliver(session, log_id, channel, dest, f"{society.name}: maintenance reminder", text)
                sent += 1

    return {"society_id": str(society.id), "date": str(today), "new_items": len(fresh), "messages_sent": sent}
