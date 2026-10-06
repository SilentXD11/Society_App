from datetime import date
from decimal import Decimal

from app.services.schedule import add_months, bucket, fd_maturity_amount, maintenance_due_date, occurrences


def test_month_end_dates_keep_their_anchor():
    d = date(2026, 1, 31)
    feb = add_months(d, 1, anchor_day=31)
    assert feb == date(2026, 2, 28)
    assert add_months(feb, 1, anchor_day=31) == date(2026, 3, 31)  # doesn't drift to the 28th


def test_quarterly_occurrences_stop_at_window_and_instalments():
    ds = occurrences(date(2026, 10, 5), "monthly", 5, until=date(2027, 3, 1), remaining=2)
    assert ds == [date(2026, 10, 5), date(2026, 11, 5)]
    q = occurrences(date(2026, 12, 25), "quarterly", 25, until=date(2027, 12, 31))
    assert q == [date(2026, 12, 25), date(2027, 3, 25), date(2027, 6, 25), date(2027, 9, 25), date(2027, 12, 25)]
    assert occurrences(date(2026, 10, 1), "one_time", 1, until=date(2030, 1, 1)) == [date(2026, 10, 1)]


def test_fd_maturity_quarterly_compounding():
    amt = fd_maturity_amount(Decimal("100000"), Decimal("7"), date(2025, 1, 1), date(2026, 1, 1))
    assert Decimal("107185") < amt < Decimal("107187")  # 1.0175^4
    assert fd_maturity_amount(Decimal("100000"), Decimal("7"), date(2025, 1, 1), date(2026, 1, 1), "monthly") == Decimal("100000.00")


def test_stage_buckets_and_due_dates():
    assert bucket(25, (30, 7, 0)) == 30
    assert bucket(5, (30, 7, 0)) == 7
    assert bucket(0, (30, 7, 0)) == 0
    assert bucket(40, (30, 7, 0)) is None
    assert maintenance_due_date(date(2026, 2, 1), 30) == date(2026, 2, 28)
