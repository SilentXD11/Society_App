from datetime import date

import pytest

from app.config import Settings, _with_driver
from app.services.schedule import add_months


def test_neon_url_is_adapted_for_each_driver():
    neon = "postgresql://user:pw@ep-cool-1234.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    a = _with_driver(neon, "asyncpg")
    assert a == "postgresql+asyncpg://user:pw@ep-cool-1234.ap-southeast-1.aws.neon.tech/neondb?ssl=require"
    s = _with_driver(a, "psycopg")
    assert s == "postgresql+psycopg://user:pw@ep-cool-1234.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
    assert _with_driver("postgres://u:p@h:5432/db", "asyncpg") == "postgresql+asyncpg://u:p@h:5432/db"
    with pytest.raises(ValueError):
        _with_driver("mysql://u:p@h/db", "asyncpg")


def test_prod_refuses_default_secrets():
    with pytest.raises(ValueError):
        Settings(env="prod", database_url="postgresql://u:p@h/db")
    ok = Settings(env="prod", database_url="postgresql://u:p@h/db", jwt_secret="x" * 40, otp_secret="y" * 40)
    assert ok.database_url.startswith("postgresql+asyncpg://")


async def test_setup_endpoint(client, outbox, monkeypatch):
    from app.config import get_settings
    body = {"society_name": "Setup CHS", "admin_name": "First Admin", "admin_phone": "9820099999",
            "monthly_charge": "2500"}
    assert (await client.post("/setup/society", json=body)).status_code == 404  # off by default

    monkeypatch.setattr(get_settings(), "setup_token", "s3cret-token-value")
    assert (await client.post("/setup/society", json=body, headers={"X-Setup-Token": "wrong"})).status_code == 401
    r = await client.post("/setup/society", json=body, headers={"X-Setup-Token": "s3cret-token-value"})
    assert r.status_code == 201, r.text

    from tests.conftest import auth, login
    h = await login(client, outbox, "+919820099999")
    me = (await client.get("/auth/me", headers=auth(h))).json()
    assert me["memberships"][0]["society_name"] == "Setup CHS" and me["memberships"][0]["is_signatory"]
