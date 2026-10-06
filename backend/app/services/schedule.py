"""Pure date and money rules for recurring bills, maintenance and FDs.
No database access here, so it is easy to unit-test."""
import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

STEP_MONTHS = {"monthly": 1, "quarterly": 3, "half_yearly": 6, "yearly": 12, "one_time": 0}


def add_months(d: date, months: int, anchor_day: int | None = None) -> date:
    """Add months, keeping the anchor day where the month allows it.
    31 Jan + 1 → 28/29 Feb, and with anchor 31 the next step returns to 31 Mar."""
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    day = min(anchor_day or d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def next_occurrence(current: date, frequency: str, anchor_day: int | None) -> date | None:
    step = STEP_MONTHS[frequency]
    return None if step == 0 else add_months(current, step, anchor_day)


def occurrences(next_due: date | None, frequency: str, anchor_day: int | None, until: date,
                remaining: int | None = None, limit: int = 60) -> list[date]:
    """Due dates from next_due up to and including `until`."""
    out: list[date] = []
    d = next_due
    while d is not None and d <= until and len(out) < limit:
        if remaining is not None and len(out) >= remaining:
            break
        out.append(d)
        d = next_occurrence(d, frequency, anchor_day)
    return out


def remaining_instalments(total: int | None, paid: int) -> int | None:
    return None if total is None else max(0, total - paid)


def month_start(d: date) -> date:
    return d.replace(day=1)


def maintenance_due_date(period: date, due_day: int) -> date:
    return period.replace(day=min(due_day, calendar.monthrange(period.year, period.month)[1]))


def fd_maturity_amount(principal: Decimal, rate_pct: Decimal, start: date, maturity: date,
                       payout: str = "cumulative") -> Decimal:
    """Indian bank convention: cumulative FDs compound quarterly.
    For payout FDs the interest is paid out, so the maturity amount is the principal."""
    if payout != "cumulative":
        return principal.quantize(Decimal("0.01"))
    years = Decimal((maturity - start).days) / Decimal("365")
    factor = (Decimal(1) + rate_pct / Decimal(400)) ** (Decimal(4) * years)
    return (principal * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def days_until(d: date, today: date) -> int:
    return (d - today).days


def bucket(days: int, thresholds: tuple[int, ...]) -> int | None:
    """Smallest threshold the item has reached, e.g. (30, 7, 0) with 5 days left → 7."""
    reached = [t for t in thresholds if days <= t]
    return min(reached) if reached else None


def today_in(tz: str) -> date:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(tz)).date()


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)
