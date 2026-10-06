from tests.conftest import auth, login


async def test_unknown_number_gets_same_answer_and_no_message(client, outbox):
    r = await client.post("/auth/otp/request", json={"destination": "+919999999999"})
    assert r.status_code == 202
    assert outbox.outbox == []


async def test_login_with_otp_and_see_memberships(client, outbox, society):
    h = await login(client, outbox, society["people"]["sig1"]["phone"])
    r = await client.get("/auth/me", headers=auth(h))
    assert r.status_code == 200
    m = r.json()["memberships"]
    assert len(m) == 1 and m[0]["role"] == "committee" and m[0]["is_signatory"] is True


async def test_wrong_codes_lock_out_after_max_attempts(client, outbox, society):
    phone = society["people"]["clerk"]["phone"]
    await client.post("/auth/otp/request", json={"destination": phone})
    real = outbox.outbox[-1]["body"][:6]
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        r = await client.post("/auth/otp/verify", json={"destination": phone, "code": wrong})
        assert r.status_code == 401
    r = await client.post("/auth/otp/verify", json={"destination": phone, "code": real})
    assert r.status_code == 401 and "Too many" in r.json()["detail"]


async def test_send_rate_limit(client, outbox, society):
    phone = society["people"]["owner"]["phone"]
    codes = [(await client.post("/auth/otp/request", json={"destination": phone})).status_code for _ in range(4)]
    assert codes == [202, 202, 202, 429]


async def test_refresh_rotates_and_reuse_revokes_everything(client, outbox, society):
    h = await login(client, outbox, society["people"]["sig2"]["phone"])
    old = h["_refresh"]
    r = await client.post("/auth/refresh", json={"refresh_token": old})
    assert r.status_code == 200
    new = r.json()["refresh_token"]
    assert (await client.post("/auth/refresh", json={"refresh_token": old})).status_code == 401  # reuse detected
    assert (await client.post("/auth/refresh", json={"refresh_token": new})).status_code == 401  # family revoked


async def test_outsider_cannot_see_society(client, outbox, society):
    from tests.conftest import society as _  # noqa: F401
    h = await login(client, outbox, society["people"]["owner"]["phone"])
    r = await client.get(f"/societies/{society['id']}/payments", headers=auth(h))
    assert r.status_code == 403  # resident, not committee
    import uuid
    r = await client.get(f"/societies/{uuid.uuid4()}", headers=auth(h))
    assert r.status_code == 404
