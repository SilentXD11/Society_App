from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import auth, login, step_up


async def _setup(client, outbox, society):
    p = society["people"]
    h = {k: await login(client, outbox, p[k]["phone"]) for k in ("sig1", "sig2", "clerk")}
    base = f"/societies/{society['id']}"
    return p, h, base


async def test_dual_approval_rules(client, outbox, society):
    p, h, base = await _setup(client, outbox, society)
    r = await client.post(f"{base}/payments", headers=auth(h["sig1"]), json={
        "payee": "Sample Security Agency", "purpose": "Guards for September", "category": "Security",
        "amount": "32000", "mode": "bank_transfer"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    # no step-up → refused
    r = await client.post(f"{base}/payments/{pid}/approve", headers=auth(h["sig2"]))
    assert r.status_code == 401 and r.headers.get("X-Step-Up-Required") == "true"

    # the person who raised it can't approve (maker-checker)
    r = await client.post(f"{base}/payments/{pid}/approve", headers=await step_up(client, outbox, h["sig1"]))
    assert r.status_code == 403

    # committee member who isn't a signatory can't approve
    r = await client.post(f"{base}/payments/{pid}/approve", headers=await step_up(client, outbox, h["clerk"]))
    assert r.status_code == 403

    # can't mark paid before approval
    r = await client.post(f"{base}/payments/{pid}/mark-paid", headers=auth(h["sig1"]), json={"paid_on": str(date.today())})
    assert r.status_code == 409

    su2 = await step_up(client, outbox, h["sig2"])
    r = await client.post(f"{base}/payments/{pid}/approve", headers=su2)
    assert r.status_code == 200 and r.json()["status"] == "pending_approval" and len(r.json()["approvals"]) == 1
    r = await client.post(f"{base}/payments/{pid}/approve", headers=su2)
    assert r.status_code == 409  # same person twice

    # make clerk a signatory and approve → second distinct signatory → approved
    from app.db import sessionmaker
    from sqlalchemy import update
    from app.models import Membership
    async with sessionmaker()() as s:
        await s.execute(update(Membership).where(Membership.user_id == p["clerk"]["id"]).values(is_signatory=True))
        await s.commit()
    r = await client.post(f"{base}/payments/{pid}/approve", headers=await step_up(client, outbox, h["clerk"]))
    assert r.status_code == 200 and r.json()["status"] == "approved"

    r = await client.post(f"{base}/payments/{pid}/mark-paid", headers=auth(h["sig1"]),
                          json={"paid_on": str(date.today()), "reference": "UTR123"})
    assert r.status_code == 200 and r.json()["status"] == "paid"

    log = (await client.get(f"{base}/audit", headers=auth(h["sig1"]))).json()
    assert [a["action"] for a in log[:4]] == ["payment.paid", "payment.approve", "payment.approve", "payment.raise"]


async def test_recurring_bill_raise_reject_rewinds_and_emi_finishes(client, outbox, society):
    p, h, base = await _setup(client, outbox, society)
    due = date.today() + timedelta(days=3)
    r = await client.post(f"{base}/bills", headers=auth(h["sig1"]), json={
        "name": "Common area electricity", "category": "Electricity", "payee": "Electricity board",
        "amount": "4200", "frequency": "monthly", "next_due": str(due), "lead_days": 7})
    assert r.status_code == 201, r.text
    bid = r.json()["id"]

    r = await client.post(f"{base}/bills/{bid}/raise-payment", headers=auth(h["sig1"]), json={})
    assert r.status_code == 201 and r.json()["bill_due_date"] == str(due)
    pid = r.json()["id"]
    bills = (await client.get(f"{base}/bills", headers=auth(h["sig1"]))).json()
    assert bills[0]["next_due"] != str(due)  # moved on

    r = await client.post(f"{base}/payments/{pid}/reject", headers=await step_up(client, outbox, h["sig2"]),
                          json={"reason": "Bill amount is wrong"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    bills = (await client.get(f"{base}/bills", headers=auth(h["sig1"]))).json()
    assert bills[0]["next_due"] == str(due) and bills[0]["paid_instalments"] == 0  # rewound

    # EMI with 1 instalment left, auto-debited → finishes
    r = await client.post(f"{base}/bills", headers=auth(h["sig1"]), json={
        "name": "Lift loan EMI", "category": "Loan / EMI", "payee": "Co-op Bank", "amount": "8750",
        "frequency": "monthly", "next_due": str(due), "autopay": True, "total_instalments": 36, "paid_instalments": 35})
    emi = r.json()["id"]
    assert (await client.post(f"{base}/bills/{emi}/raise-payment", headers=auth(h["sig1"]), json={})).status_code == 409
    r = await client.post(f"{base}/bills/{emi}/auto-debit", headers=auth(h["sig1"]), json={"debited_on": str(due)})
    assert r.status_code == 201 and r.json()["status"] == "paid"
    emi_row = [b for b in (await client.get(f"{base}/bills", headers=auth(h["sig1"]))).json() if b["id"] == emi][0]
    assert emi_row["next_due"] is None and emi_row["paid_instalments"] == 36


async def test_staff_pay_bill_and_payment_calendar(client, outbox, society):
    p, h, base = await _setup(client, outbox, society)
    for name, pay in [("Guard A", "16000"), ("Guard B", "16000")]:
        await client.post(f"{base}/staff", headers=auth(h["sig1"]), json={"role": "security", "full_name": name, "monthly_pay": pay})
    await client.post(f"{base}/staff", headers=auth(h["sig1"]), json={"role": "sweeper", "full_name": "Sweeper", "monthly_pay": "10000"})
    due = date.today() + timedelta(days=5)
    r = await client.post(f"{base}/bills", headers=auth(h["sig1"]), json={
        "name": "Guard wages", "category": "Staff salaries", "payee": "Guards", "amount_source": "security_pay",
        "frequency": "monthly", "next_due": str(due)})
    assert Decimal(r.json()["current_amount"]) == Decimal("32000")  # sum of active guards' pay
    await client.post(f"{base}/fds", headers=auth(h["sig1"]), json={
        "bank": "SBI", "fd_number": "12344417", "principal": "200000", "rate_pct": "7.1",
        "start_date": str(date.today() - timedelta(days=340)), "maturity_date": str(date.today() + timedelta(days=25))})

    cal = (await client.get(f"{base}/upcoming?days=40", headers=auth(h["clerk"]))).json()
    kinds = [i["kind"] for i in cal["items"]]
    assert "bill" in kinds and "fd_maturity" in kinds
    wages = [i for i in cal["items"] if i["kind"] == "bill"]
    assert [i["due"] for i in wages][0] == str(due)
    assert Decimal(cal["to_pay_total"]) == Decimal("32000") * len(wages)  # one or two pay days fall in 40 days

    owner = await login(client, outbox, p["owner"]["phone"])
    cal_res = (await client.get(f"{base}/upcoming?days=40", headers=auth(owner))).json()
    assert all(i["kind"] == "maintenance" for i in cal_res["items"])  # residents don't see society bills
