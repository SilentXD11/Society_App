"""Tests run against a real PostgreSQL database.

Set TEST_DATABASE_URL (asyncpg URL to a server where you may create databases), e.g.
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/sr_test
The database is dropped and recreated from db/schema.sql at the start of the run.
"""
import os
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://postgres@localhost:5433/sr_test")
os.environ["SR_DATABASE_URL"] = TEST_URL
os.environ["SR_ENV"] = "test"

from app.config import get_settings  # noqa: E402
from app.db import sessionmaker  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Membership, Society, User  # noqa: E402
from app.services.notifier import ConsoleNotifier, set_notifier  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def _plain(url: str) -> str:
    return url.replace("+asyncpg", "")


@pytest.fixture(scope="session", autouse=True)
async def database():
    get_settings.cache_clear()
    base, name = _plain(TEST_URL).rsplit("/", 1)
    admin = await asyncpg.connect(base + "/postgres")
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    conn = await asyncpg.connect(_plain(TEST_URL))
    await conn.execute(SCHEMA.read_text())
    await conn.close()
    yield


@pytest.fixture
def outbox() -> ConsoleNotifier:
    n = ConsoleNotifier()
    set_notifier(n)
    return n


@pytest.fixture
async def client(outbox):
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as c:
        yield c


def _phone() -> str:
    return "+9198" + str(uuid.uuid4().int)[:8]


@pytest.fixture
async def society():
    """A fresh society with: two signatories (sig1, sig2), a committee member who is
    not a signatory (clerk), and a resident (owner). Returns ids and phones."""
    async with sessionmaker()() as s:
        soc = Society(name=f"Test CHS {uuid.uuid4().hex[:6]}", monthly_charge=Decimal("2500"), due_day=10,
                      timezone="Asia/Kolkata", pay_info="UPI testchs@upi")
        s.add(soc)
        await s.flush()
        people = {}
        for key, role, sig in [("sig1", "committee", True), ("sig2", "committee", True),
                               ("clerk", "committee", False), ("owner", "owner", False)]:
            u = User(full_name=key.title(), phone=_phone())
            s.add(u)
            await s.flush()
            s.add(Membership(society_id=soc.id, user_id=u.id, role=role, is_signatory=sig))
            people[key] = {"id": u.id, "phone": u.phone}
        await s.commit()
        return {"id": soc.id, "people": people}


async def login(client: AsyncClient, outbox: ConsoleNotifier, phone: str) -> dict:
    r = await client.post("/auth/otp/request", json={"destination": phone})
    assert r.status_code == 202, r.text
    code = outbox.outbox[-1]["body"][:6]
    r = await client.post("/auth/otp/verify", json={"destination": phone, "code": code})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}", "_refresh": r.json()["refresh_token"]}


async def step_up(client: AsyncClient, outbox: ConsoleNotifier, headers: dict) -> dict:
    h = {k: v for k, v in headers.items() if not k.startswith("_")}
    r = await client.post("/auth/step-up/request", headers=h)
    assert r.status_code == 202, r.text
    code = outbox.outbox[-1]["body"][:6]
    r = await client.post("/auth/step-up/verify", json={"code": code}, headers=h)
    assert r.status_code == 200, r.text
    return {**h, "X-Step-Up": r.json()["step_up_token"]}


def auth(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if not k.startswith("_")}
