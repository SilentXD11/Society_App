from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.db import sessionmaker
from app.models import NotificationLog, Society
from app.services.reminders import run_for_society
from tests.conftest import auth, login, step_up


async def test_fd_lifecycle(client, outbox, society):
    h = await login(client, outbox, society["people"]["sig1"]["phone"])
    base = f"/societies/{society['id']}"
    r = await client.post(f"{base}/fds", headers=auth(h), json={
        "bank": "Sample Bank", "fd_number": "XX-9876-4417", "principal": "200000", "rate_pct": "7.10",
        "start_date": "2025-10-20", "maturity_date": "2026-10-20", "auto_renew": False})
    assert r.status_code == 201, r.text
    fd = r.json()
    assert fd["fd_last4"] == "4417"
    assert Decimal("214000") < Decimal(fd["maturity_amount"]) < Decimal("215000")

    s = (await client.get(f"{base}/fds/summary", headers=auth(h))).json()
    assert s["active_count"] == 1 and s["total_principal"] == "200000.00"

    assert (await client.post(f"{base}/fds/{fd['id']}/renew", headers=auth(h),
                              json={"rate_pct": "7.25", "months": 12})).status_code == 401  # needs step-up
    r = await client.post(f"{base}/fds/{fd['id']}/renew", headers=await step_up(client, outbox, h),
                          json={"rate_pct": "7.25", "months": 12})
    assert r.status_code == 201
    new = r.json()
    assert new["start_date"] == "2026-10-20" and new["principal"] == fd["maturity_amount"]
    assert new["renewed_from_id"] == fd["id"]
    old = [f for f in (await client.get(f"{base}/fds", headers=auth(h))).json() if f["id"] == fd["id"]][0]
    assert old["status"] == "renewed"


async def test_daily_reminders_send_once_per_stage(client, outbox, society):
    p = society["people"]
    h = await login(client, outbox, p["sig1"]["phone"])
    base = f"/societies/{society['id']}"
    today = date(2026, 10, 7)

    await client.post(f"{base}/bills", headers=auth(h), json={
        "name": "Property tax", "category": "Property tax", "payee": "Municipal corporation",
        "amount": "26000", "frequency": "half_yearly", "next_due": "2026-10-15", "lead_days": 14})
    await client.post(f"{base}/fds", headers=auth(h), json={
        "bank": "Co-op Bank", "fd_number": "0921", "principal": "300000", "rate_pct": "7.4",
        "start_date": "2024-10-31", "maturity_date": "2026-10-31", "auto_renew": True})
    r = await client.post(f"{base}/flats", headers=auth(h), json={"wing": "A", "number": "101"})
    flat = r.json()["id"]
    await client.post(f"{base}/flats/{flat}/residents", headers=auth(h), json={
        "kind": "owner", "full_name": "Asha Rao", "phone": "9820012345"})
    await client.post(f"{base}/maintenance/generate", headers=auth(h), json={"period": "2026-10-01"})  # due 10 Oct

    outbox.outbox.clear()
    async with sessionmaker()() as s:
        soc = await s.get(Society, society["id"])
        res = await run_for_society(s, soc, today)
        await s.commit()
    assert res["new_items"] == 2  # tax (within 14-day lead) + FD (within 30 days)
    digests = [m for m in outbox.outbox if "reminders for" in m["body"]]
    assert len(digests) == 3  # three committee members
    assert "Property tax: ₹26,000 due 15 Oct" in digests[0]["body"]
    assert "auto-renews" in digests[0]["body"]
    sms = [m for m in outbox.outbox if m["to"] == "+919820012345"]
    assert len(sms) == 1 and "₹2,500" in sms[0]["body"] and "UPI testchs@upi" in sms[0]["body"]

    # same day again → nothing new
    outbox.outbox.clear()
    async with sessionmaker()() as s:
        soc = await s.get(Society, society["id"])
        res = await run_for_society(s, soc, today)
        await s.commit()
    assert res == {**res, "new_items": 0, "messages_sent": 0} and outbox.outbox == []

    # 13 Oct: maintenance 3 days overdue → second resident SMS; tax stays in same stage
    async with sessionmaker()() as s:
        soc = await s.get(Society, society["id"])
        res = await run_for_society(s, soc, date(2026, 10, 13))
        await s.commit()
    assert res["new_items"] == 0 and res["messages_sent"] == 1
    assert "still unpaid" in outbox.outbox[-1]["body"]

    async with sessionmaker()() as s:
        n = len((await s.execute(select(NotificationLog).where(NotificationLog.society_id == society["id"]))).scalars().all())
    assert n == 2 + 3 + 2  # 2 item markers, 3 digests, 2 resident SMS


async def test_resident_sees_only_their_dues_and_partial_payment(client, outbox, society):
    p = society["people"]
    h = await login(client, outbox, p["sig1"]["phone"])
    base = f"/societies/{society['id']}"
    f1 = (await client.post(f"{base}/flats", headers=auth(h), json={"wing": "B", "number": "201"})).json()["id"]
    f2 = (await client.post(f"{base}/flats", headers=auth(h), json={"wing": "B", "number": "202", "monthly_charge": "3200"})).json()["id"]
    await client.post(f"{base}/flats/{f1}/residents", headers=auth(h), json={
        "kind": "owner", "full_name": "Owner", "phone": p["owner"]["phone"]})
    bills = (await client.post(f"{base}/maintenance/generate", headers=auth(h), json={"period": "2026-11-15"})).json()
    again = (await client.post(f"{base}/maintenance/generate", headers=auth(h), json={"period": "2026-11-01"})).json()
    assert len(bills) == len(again) == 2  # idempotent
    b1 = [b for b in bills if b["flat_id"] == f1][0]
    assert [b for b in bills if b["flat_id"] == f2][0]["amount"] == "3200.00"

    r = await client.post(f"{base}/maintenance/{b1['id']}/receipts", headers=auth(h),
                          json={"amount": "1000", "paid_on": "2026-11-05", "mode": "upi"})
    assert r.json()["status"] == "unpaid" and r.json()["paid"] == "1000.00"
    r = await client.post(f"{base}/maintenance/{b1['id']}/receipts", headers=auth(h),
                          json={"amount": "1500", "paid_on": "2026-11-06", "mode": "cash"})
    assert r.json()["status"] == "paid"

    owner = await login(client, outbox, p["owner"]["phone"])
    mine = (await client.get(f"{base}/my-dues", headers=auth(owner))).json()
    assert [b["flat_id"] for b in mine] == [f1]


async def test_audit_log_cannot_be_altered(society):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    async with sessionmaker()() as s:
        await s.execute(text("INSERT INTO audit_log(action, entity) VALUES ('t', 't')"))
        await s.commit()
        try:
            await s.execute(text("DELETE FROM audit_log"))
            await s.commit()
            raise AssertionError("delete should fail")
        except DBAPIError as e:
            await s.rollback()
            assert "append-only" in str(e)
