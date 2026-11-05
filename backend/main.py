"""
Society Grocery API (Auth0 edition)
-----------------------------------
- People sign in with Auth0 (email/password, Google, etc. — whatever you switch on in Auth0).
- Auth0 proves WHO someone is. This server decides WHETHER they're allowed in:
  only verified emails on the society register can shop; admins are listed in ADMIN_EMAILS.
- Every sign-in is recorded, so the admin dashboard shows who logged in, when, and from where.
  People who sign in but aren't on the register appear as "access requests" you can approve.
- Prices and totals are always calculated here, never trusted from the app.
- New orders are pushed instantly to the admin dashboard and to Telegram.
"""
import asyncio
import html
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Optional

import httpx
import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("society")


# ---------------------------------------------------------------- config
ON_VERCEL = bool(os.getenv("VERCEL"))          # Vercel sets this automatically
MISSING: list[str] = []                        # settings that still need filling in


def need(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        MISSING.append(name)
    return v


# Only signs the dashboard's 60-second live-feed ticket. Optional: if not set, a random one is made at start-up
# (fine — the live feed only runs on a single laptop server; on Vercel it's switched off).
JWT_SECRET = os.getenv("JWT_SECRET", "").strip() or secrets.token_urlsafe(48)
if len(JWT_SECRET) < 32:
    MISSING.append("JWT_SECRET (must be 32+ random characters, or leave it out)")
AUTH0_DOMAIN = need("AUTH0_DOMAIN").removeprefix("https://").rstrip("/")
AUTH0_CLIENT_ID = need("AUTH0_CLIENT_ID")
AUTH0_AUDIENCE = need("AUTH0_AUDIENCE")
ADMIN_EMAILS = {e.strip().lower() for e in need("ADMIN_EMAILS").split(",") if e.strip()}
if MISSING:
    log.error("Server settings missing: %s — the app will show a setup message until they're added",
              ", ".join(MISSING))
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
# true when the server sits behind a tunnel/proxy (ngrok, Vercel) that sets X-Forwarded-For
TRUST_PROXY = ON_VERCEL or os.getenv("TRUST_PROXY", "false").lower() == "true"
# Vercel runs many short-lived copies of the server, so a live push feed can't reach every admin:
# the dashboard polls instead, and alerts are sent before the request finishes.
LIVE_STREAM = not ON_VERCEL
PAGES_DIR = Path(__file__).resolve().parent.parent   # where index.html and admin.html live

# Database: Postgres when DATABASE_URL is set (Vercel + Neon), otherwise a local SQLite file.
DATABASE_URL = (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
USE_PG = DATABASE_URL.startswith("postgresql://")
DB_PATH = os.getenv("DB_PATH", str(Path(__file__).parent / "society-auth0.db"))
if not USE_PG and ON_VERCEL:
    # Vercel's disk is read-only except /tmp, which is wiped often — fine for a first look, not for real use.
    DB_PATH = "/tmp/society.db"
    log.warning("No DATABASE_URL set: using a temporary database. Add Postgres (Neon) in Vercel → Storage.")

CATALOG = json.loads((Path(__file__).parent / "catalog.json").read_text(encoding="utf-8"))

ORDER_FLOW = ["placed", "packed", "out", "delivered"]
SLOT_LABEL = {"asap": "Express (~30 min)", "eve": "Today 6–8 pm", "tmrw": "Tomorrow 7–9 am"}


# ---------------------------------------------------------------- database
_NAMED = re.compile(r"(?<!:):([a-z_]+)")


def _pg_sql(sql: str) -> str:
    """The SQL below is written once for SQLite; this adapts it for Postgres."""
    return _NAMED.sub(r"%(\1)s", sql.replace("?", "%s")).replace("INTEGER", "BIGINT")


class _PgConn:
    def __init__(self, con):
        self.con = con

    def execute(self, sql, params=None):
        return self.con.execute(_pg_sql(sql), params)

    def executescript(self, script):
        for stmt in script.split(";"):
            if stmt.strip():
                self.con.execute(_pg_sql(stmt))


@contextmanager
def tx():
    if USE_PG:
        import psycopg                      # only needed on Vercel/Postgres
        from psycopg.rows import dict_row
        con = psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None, connect_timeout=10)
        try:
            yield _PgConn(con)
            con.commit()
        finally:
            con.close()
        return
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with tx() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS residents(
            email TEXT PRIMARY KEY, name TEXT NOT NULL, flat TEXT NOT NULL, phone TEXT,
            active INTEGER NOT NULL DEFAULT 1, created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS users(
            sub TEXT PRIMARY KEY, email TEXT, email_verified INTEGER, auth_name TEXT, picture TEXT,
            first_seen INTEGER, last_seen INTEGER, last_ip TEXT, info_checked INTEGER);
        CREATE TABLE IF NOT EXISTS logins(
            ts DOUBLE PRECISION, sub TEXT, email TEXT, name TEXT, flat TEXT, ip TEXT, device TEXT, outcome TEXT);
        CREATE TABLE IF NOT EXISTS orders(
            id TEXT PRIMARY KEY, email TEXT NOT NULL, name TEXT, flat TEXT, phone TEXT,
            items TEXT NOT NULL, subtotal INTEGER, fee INTEGER, total INTEGER,
            slot TEXT, pay_method TEXT, pay_status TEXT, utr TEXT UNIQUE,
            status TEXT, created INTEGER, updated INTEGER);
        CREATE TABLE IF NOT EXISTS audit(ts INTEGER, actor TEXT, action TEXT, detail TEXT);
        CREATE INDEX IF NOT EXISTS idx_orders_email ON orders(email);
        CREATE INDEX IF NOT EXISTS idx_logins_ts ON logins(ts)
        """)
        for e in ADMIN_EMAILS:
            c.execute("INSERT INTO residents VALUES(?,?,?,?,1,?) ON CONFLICT DO NOTHING",
                      (e, "Admin", "Society Office", None, int(time.time())))


def audit(c, actor, action, detail=""):
    c.execute("INSERT INTO audit VALUES(?,?,?,?)", (int(time.time()), actor, action, detail))


DB_ERROR = ""
try:
    init_db()
except Exception as e:  # show a clear message instead of crashing on start
    DB_ERROR = f"{type(e).__name__}: {e}"[:300]
    log.exception("Database setup failed")


# ---------------------------------------------------------------- helpers
_hits: dict[str, list[float]] = {}


def rate_limit(key: str, limit: int, window: int):
    """Simple in-memory limiter. Use Redis instead if you run more than one server."""
    now = time.time()
    recent = [t for t in _hits.get(key, []) if now - t < window]
    if len(recent) >= limit:
        raise HTTPException(429, "Too many attempts. Please wait a few minutes and try again.")
    recent.append(now)
    _hits[key] = recent


def norm_phone(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    d = re.sub(r"\D", "", raw)
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    if not re.fullmatch(r"[6-9]\d{9}", d):
        raise HTTPException(400, "Enter a valid 10-digit mobile number (or leave it blank)")
    return d


def client_ip(request: Request) -> str:
    # Behind ngrok/Render the real visitor's address is in X-Forwarded-For. Only trusted when TRUST_PROXY is on,
    # because anyone can send that header straight to the server otherwise.
    if TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if fwd:
            return fwd[:64]
    return request.client.host if request.client else "unknown"


def device_of(ua: str) -> str:
    ua = ua or ""
    os_ = next((n for k, n in [("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                               ("Windows", "Windows"), ("Mac OS", "Mac"), ("Linux", "Linux")] if k in ua), "Other")
    br = next((n for k, n in [("Edg/", "Edge"), ("OPR/", "Opera"), ("Chrome/", "Chrome"),
                              ("Firefox/", "Firefox"), ("Safari/", "Safari")] if k in ua), "Browser")
    return f"{br} on {os_}"


# ---------- Auth0 token verification
_jwks = jwt.PyJWKClient(f"https://{AUTH0_DOMAIN}/.well-known/jwks.json", cache_keys=True, lifespan=3600)


def verify_auth0_token(token: str) -> dict:
    """Checks the token was signed by YOUR Auth0 tenant, for THIS API, and hasn't expired."""
    try:
        key = _jwks.get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=["RS256"], audience=AUTH0_AUDIENCE,
                          issuer=f"https://{AUTH0_DOMAIN}/", leeway=30)
    except jwt.PyJWKClientError:
        raise HTTPException(401, "Couldn't check your login right now. Please try again.")
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired. Please log in again.")


def fetch_userinfo(token: str) -> dict:
    """Asks Auth0 for the person's email + whether it's verified (cached so it's ~once a day per person)."""
    try:
        r = httpx.get(f"https://{AUTH0_DOMAIN}/userinfo", headers={"Authorization": f"Bearer {token}"}, timeout=10)
    except httpx.HTTPError:
        raise HTTPException(503, "Couldn't reach the login service. Please try again.")
    if r.status_code == 429:
        raise HTTPException(429, "Too many requests. Please wait a moment.")
    if r.status_code != 200:
        raise HTTPException(401, "Session expired. Please log in again.")
    return r.json()


def identity(request: Request) -> dict:
    """Who is this? (Auth0-verified). Does NOT check if they're allowed in — see current_user."""
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        raise HTTPException(401, "Please log in")
    token = h[7:]
    claims = verify_auth0_token(token)
    sub, now, ip = claims["sub"], int(time.time()), client_ip(request)
    with tx() as c:
        u = c.execute("SELECT * FROM users WHERE sub=?", (sub,)).fetchone()
        if not u or not u["email"] or now - (u["info_checked"] or 0) > 86400 or not u["email_verified"]:
            info = fetch_userinfo(token)
            email = (info.get("email") or "").strip().lower()
            c.execute("""INSERT INTO users(sub,email,email_verified,auth_name,picture,first_seen,last_seen,last_ip,info_checked)
                         VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(sub) DO UPDATE SET email=excluded.email,
                         email_verified=excluded.email_verified, auth_name=excluded.auth_name, picture=excluded.picture,
                         last_seen=excluded.last_seen, last_ip=excluded.last_ip, info_checked=excluded.info_checked""",
                      (sub, email, int(bool(info.get("email_verified"))), (info.get("name") or "")[:80],
                       (info.get("picture") or "")[:500], now, now, ip, now))
        elif now - (u["last_seen"] or 0) > 60:
            c.execute("UPDATE users SET last_seen=?, last_ip=? WHERE sub=?", (now, ip, sub))
        u = c.execute("SELECT * FROM users WHERE sub=?", (sub,)).fetchone()
    return {"sub": sub, "email": u["email"], "email_verified": bool(u["email_verified"]),
            "auth_name": u["auth_name"], "picture": u["picture"]}


def resident_for(c, ident: dict):
    if not ident["email"] or not ident["email_verified"]:
        return None
    return c.execute("SELECT * FROM residents WHERE email=? AND active=1", (ident["email"],)).fetchone()


def current_user(ident: dict = Depends(identity)) -> dict:
    """Signed in AND on the society register (re-checked on every request, so blocking is instant)."""
    if not ident["email_verified"]:
        raise HTTPException(403, "unverified: Please verify your email (check your inbox), then log in again.")
    with tx() as c:
        r = resident_for(c, ident)
        blocked = not r and c.execute("SELECT 1 FROM residents WHERE email=? AND active=0", (ident["email"],)).fetchone()
    if blocked:
        raise HTTPException(403, "blocked: Your access has been removed. Please contact the society office.")
    if not r:
        raise HTTPException(403, "not_registered: Your account is waiting for approval from the society office.")
    return {"sub": ident["sub"], "email": r["email"], "name": r["name"], "flat": r["flat"], "phone": r["phone"],
            "picture": ident["picture"], "role": "admin" if r["email"] in ADMIN_EMAILS else "resident"}


def admin_user(u: dict = Depends(current_user)) -> dict:
    if u["role"] != "admin":
        raise HTTPException(403, "Admins only")
    return u


def order_out(r: sqlite3.Row, admin: bool = False) -> dict:
    o = {k: r[k] for k in r.keys()}
    o["items"] = json.loads(o["items"])
    if not admin:
        o.pop("email", None)
        o.pop("phone", None)
    return o


def make_ticket(email: str) -> str:
    now = int(time.time())
    return jwt.encode({"sub": email, "pur": "stream", "iat": now, "exp": now + 60}, JWT_SECRET, algorithm="HS256")


# ---------------------------------------------------------------- notifications
_admin_streams: set[asyncio.Queue] = set()
_bg_tasks: set[asyncio.Task] = set()


def broadcast(event: str, data: dict):
    for q in list(_admin_streams):
        try:
            q.put_nowait({"event": event, "data": data})
        except asyncio.QueueFull:
            pass


async def telegram(text: str):
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            r = await cl.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                              json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
                                    "disable_web_page_preview": True})
            if r.status_code != 200:
                log.warning("Telegram error %s: %s", r.status_code, r.text[:200])
    except Exception as e:  # never let a notification failure break an order
        log.warning("Telegram failed: %s", e)


async def fire(coro):
    """Send a notification without slowing the request down — except on Vercel, where work left
    running after the reply can be frozen, so there we wait for it (Telegram has a 10 s timeout)."""
    if ON_VERCEL:
        await coro
        return
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


def order_message(o: dict) -> str:
    e = html.escape
    lines = "\n".join(f"• {i['qty']} × {e(i['name'])} ({e(i['unit'])}) — {e(i['vendor'])} ₹{i['total']}"
                      for i in o["items"])
    if o["pay_method"] == "upi":
        pay = f"UPI · UTR <code>{e(o['utr'])}</code> — verify in bank app" if o["utr"] else "UPI · awaiting payment"
    else:
        pay = "Cash on delivery"
    contact = e(o["phone"]) if o.get("phone") else e(o["email"])
    return (f"🛒 <b>New order {e(o['id'])}</b>\n"
            f"🏠 {e(o['flat'])} · {e(o['name'])} · {contact}\n"
            f"🕒 {SLOT_LABEL.get(o['slot'], o['slot'])}\n\n{lines}\n\n"
            f"<b>Total ₹{o['total']}</b> (delivery ₹{o['fee']})\n💳 {pay}")


# ---------------------------------------------------------------- app
app = FastAPI(title="Society Grocery API",
              docs_url="/docs" if DEV_MODE else None, redoc_url=None,
              openapi_url="/openapi.json" if DEV_MODE else None)

app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                   allow_methods=["GET", "POST", "PATCH"],
                   allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def setup_check(request: Request, call_next):
    """Until required settings exist, every API call explains what's missing (pages still load)."""
    p = request.url.path
    if (MISSING or DB_ERROR) and p not in ("/", "/health", "/society-app.html") and p.lstrip("/") not in PAGES:
        msg = (f"Server setup incomplete — add these settings: {', '.join(MISSING)}" if MISSING
               else f"Database problem: {DB_ERROR}")
        return JSONResponse({"detail": msg}, status_code=503)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if int(request.headers.get("content-length") or 0) > 64_000:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# ---------- the pages themselves (so a phone needs just one address)
PAGES = {"index.html": "text/html", "admin.html": "text/html",
         "manifest.webmanifest": "application/manifest+json",
         "icon-192.png": "image/png", "icon-512.png": "image/png"}


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(PAGES_DIR / "index.html", media_type="text/html")


@app.get("/society-app.html", include_in_schema=False)
def old_address():   # the app used to live here
    return RedirectResponse("/")


@app.get("/health")
def health():
    return {"ok": not (MISSING or DB_ERROR), "missing": MISSING, "database": "postgres" if USE_PG else "sqlite",
            "db_error": DB_ERROR or None, "host": "vercel" if ON_VERCEL else "local"}


@app.get("/config")
def public_config():
    """Public Auth0 settings the pages need to show the login screen (these are not secrets)."""
    return {"domain": AUTH0_DOMAIN, "client_id": AUTH0_CLIENT_ID, "audience": AUTH0_AUDIENCE, "live": LIVE_STREAM}


# ---------- sign-in record
@app.post("/auth/session")
async def record_login(request: Request, ident: dict = Depends(identity)):
    """Called by the pages right after someone signs in. Records the login and says if they're allowed in."""
    ip = client_ip(request)
    rate_limit(f"login:{ident['sub']}", 20, 600)
    with tx() as c:
        r = resident_for(c, ident)
        if r:
            outcome = "ok"
        elif not ident["email_verified"]:
            outcome = "unverified"
        elif c.execute("SELECT 1 FROM residents WHERE email=? AND active=0", (ident["email"],)).fetchone():
            outcome = "blocked"
        else:
            outcome = "not_registered"
        first_request = outcome == "not_registered" and not c.execute(
            "SELECT 1 FROM logins WHERE sub=? AND outcome='not_registered'", (ident["sub"],)).fetchone()
        row = {"ts": round(time.time(), 3), "sub": ident["sub"], "email": ident["email"],
               "name": r["name"] if r else ident["auth_name"], "flat": r["flat"] if r else None,
               "ip": ip, "device": device_of(request.headers.get("user-agent", "")), "outcome": outcome}
        c.execute("INSERT INTO logins VALUES(:ts,:sub,:email,:name,:flat,:ip,:device,:outcome)", row)
    broadcast("login", {k: v for k, v in row.items() if k != "sub"})
    if first_request:
        await fire(telegram(f"🔔 <b>New access request</b>\n{html.escape(row['name'] or '')} · {html.escape(row['email'] or '')}\n"
                      f"Approve them in the dashboard → Residents."))
    if outcome == "unverified":
        raise HTTPException(403, "unverified: Please verify your email (check your inbox), then log in again.")
    return current_user(ident)   # for "blocked" / "not_registered" this raises the matching message


@app.get("/me")
def me(u: dict = Depends(current_user)):
    return u


@app.get("/catalog")
def catalog(u: dict = Depends(current_user)):
    return CATALOG


# ---------- orders (resident)
class Item(BaseModel):
    product_id: str = Field(max_length=40, pattern=r"^[a-z0-9_]+$")
    vendor_id: str = Field(max_length=10, pattern=r"^v\d+$")
    qty: int = Field(ge=1, le=20)


class OrderIn(BaseModel):
    items: list[Item] = Field(min_length=1, max_length=60)
    slot: Literal["asap", "eve", "tmrw"]
    pay_method: Literal["upi", "cod"]
    utr: Optional[str] = Field(default=None, pattern=r"^\d{12}$")


class UtrIn(BaseModel):
    utr: str = Field(pattern=r"^\d{12}$")


def utr_taken(c, utr: str) -> bool:
    return c.execute("SELECT 1 FROM orders WHERE utr=?", (utr,)).fetchone() is not None


@app.post("/orders")
async def create_order(body: OrderIn, u: dict = Depends(current_user)):
    rate_limit(f"order:{u['email']}", 10, 3600)
    lines, seen = [], set()
    for it in body.items:
        key = f"{it.product_id}|{it.vendor_id}"
        if key in seen:
            raise HTTPException(400, "Duplicate item in cart")
        seen.add(key)
        price = CATALOG["prices"].get(key)          # price comes from the server, never the app
        if price is None:
            raise HTTPException(400, "An item in your cart is no longer available. Please refresh.")
        p, v = CATALOG["products"][it.product_id], CATALOG["vendors"][it.vendor_id]
        lines.append({"product_id": it.product_id, "vendor_id": it.vendor_id, "name": p["name"],
                      "unit": p["unit"], "vendor": v["name"], "qty": it.qty, "price": price,
                      "total": price * it.qty})
    subtotal = sum(l["total"] for l in lines)
    d = CATALOG["delivery"]
    fee = 0 if subtotal >= d["free_above"] else d["fee"]
    utr = body.utr if body.pay_method == "upi" else None
    pay_status = "cod" if body.pay_method == "cod" else ("verifying" if utr else "awaiting")
    now = int(time.time())
    oid = "ORD" + secrets.token_hex(4).upper()
    with tx() as c:
        if utr and utr_taken(c, utr):
            raise HTTPException(409, "This UPI transaction ID has already been used")
        c.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (oid, u["email"], u["name"], u["flat"], u["phone"], json.dumps(lines), subtotal, fee,
                   subtotal + fee, body.slot, body.pay_method, pay_status, utr, "placed", now, now))
        audit(c, u["email"], "order_create", oid)
        row = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    o = order_out(row, admin=True)
    broadcast("order_new", o)
    await fire(telegram(order_message(o)))
    return order_out(row)


@app.get("/orders/mine")
def my_orders(u: dict = Depends(current_user)):
    with tx() as c:
        rows = c.execute("SELECT * FROM orders WHERE email=? ORDER BY created DESC LIMIT 50",
                         (u["email"],)).fetchall()
    return [order_out(r) for r in rows]


@app.post("/orders/{oid}/utr")
async def submit_utr(oid: str, body: UtrIn, u: dict = Depends(current_user)):
    with tx() as c:
        r = c.execute("SELECT * FROM orders WHERE id=? AND email=?", (oid, u["email"])).fetchone()
        if not r or r["pay_method"] != "upi" or r["pay_status"] not in ("awaiting", "failed"):
            raise HTTPException(400, "Payment details can't be added to this order")
        if utr_taken(c, body.utr):
            raise HTTPException(409, "This UPI transaction ID has already been used")
        c.execute("UPDATE orders SET utr=?, pay_status='verifying', updated=? WHERE id=?",
                  (body.utr, int(time.time()), oid))
        audit(c, u["email"], "utr_submit", oid)
        row = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    o = order_out(row, admin=True)
    broadcast("order_update", o)
    await fire(telegram(f"💳 Payment submitted for <b>{oid}</b> ({html.escape(o['flat'])}) — UTR <code>{body.utr}</code>, ₹{o['total']}. Please verify."))
    return order_out(row)


# ---------- admin
class OrderPatch(BaseModel):
    status: Optional[Literal["placed", "packed", "out", "delivered", "cancelled"]] = None
    pay_status: Optional[Literal["paid", "failed"]] = None


class ResidentIn(BaseModel):
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = Field(min_length=1, max_length=60)
    flat: str = Field(min_length=1, max_length=40)
    phone: Optional[str] = Field(default=None, max_length=20)


class ResidentPatch(BaseModel):
    active: bool


@app.get("/admin/orders")
def admin_orders(status: Optional[str] = None, a: dict = Depends(admin_user)):
    q, args = "SELECT * FROM orders", []
    if status:
        q += " WHERE status=?"
        args.append(status)
    with tx() as c:
        rows = c.execute(q + " ORDER BY created DESC LIMIT 300", args).fetchall()
    return [order_out(r, admin=True) for r in rows]


@app.patch("/admin/orders/{oid}")
def admin_update_order(oid: str, body: OrderPatch, a: dict = Depends(admin_user)):
    with tx() as c:
        r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not r:
            raise HTTPException(404, "Order not found")
        sets, args = [], []
        if body.status:
            cur = r["status"]
            if cur in ("delivered", "cancelled"):
                raise HTTPException(400, f"Order is already {cur}")
            if body.status != "cancelled":
                if ORDER_FLOW.index(body.status) <= ORDER_FLOW.index(cur):
                    raise HTTPException(400, "Orders can only move forward")
                paid_now = body.pay_status == "paid" or r["pay_status"] == "paid"
                if r["pay_method"] == "upi" and not paid_now:
                    raise HTTPException(400, "Confirm the UPI payment has arrived before processing this order")
            sets.append("status=?"); args.append(body.status)
        if body.pay_status:
            if r["pay_method"] != "upi":
                raise HTTPException(400, "Not a UPI order")
            sets.append("pay_status=?"); args.append(body.pay_status)
        if not sets:
            raise HTTPException(400, "Nothing to update")
        sets.append("updated=?"); args.append(int(time.time()))
        c.execute(f"UPDATE orders SET {', '.join(sets)} WHERE id=?", (*args, oid))
        audit(c, a["email"], "order_update", f"{oid} {body.model_dump(exclude_none=True)}")
        row = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    o = order_out(row, admin=True)
    broadcast("order_update", o)
    return o


@app.get("/admin/residents")
def list_residents(a: dict = Depends(admin_user)):
    with tx() as c:
        rows = c.execute("""SELECT r.*, MAX(u.last_seen) AS last_seen FROM residents r
                            LEFT JOIN users u ON u.email = r.email GROUP BY r.email ORDER BY r.flat""").fetchall()
    return [dict(r) for r in rows]


@app.post("/admin/residents")
def add_resident(body: ResidentIn, a: dict = Depends(admin_user)):
    email = body.email.strip().lower()
    phone = norm_phone(body.phone)
    with tx() as c:
        c.execute("INSERT INTO residents VALUES(?,?,?,?,1,?) ON CONFLICT(email) DO UPDATE SET "
                  "name=excluded.name, flat=excluded.flat, phone=excluded.phone, active=1",
                  (email, body.name.strip(), body.flat.strip(), phone, int(time.time())))
        audit(c, a["email"], "resident_upsert", email)
    return {"ok": True}


@app.patch("/admin/residents/{email}")
def update_resident(email: str, body: ResidentPatch, a: dict = Depends(admin_user)):
    email = email.strip().lower()
    if email in ADMIN_EMAILS and not body.active:
        raise HTTPException(400, "Admins can't be blocked here — remove them from ADMIN_EMAILS instead")
    with tx() as c:
        c.execute("UPDATE residents SET active=? WHERE email=?", (int(body.active), email))
        audit(c, a["email"], "resident_active", f"{email}={body.active}")
    return {"ok": True}


@app.get("/admin/access-requests")
def access_requests(a: dict = Depends(admin_user)):
    """People who signed in with Auth0 but aren't on the register yet."""
    with tx() as c:
        rows = c.execute("""SELECT u.email, u.auth_name, u.email_verified, u.first_seen, u.last_seen FROM users u
                            WHERE u.email IS NOT NULL AND u.email <> ''
                              AND u.email NOT IN (SELECT email FROM residents)
                            ORDER BY u.last_seen DESC LIMIT 100""").fetchall()
    return [dict(r) for r in rows]


@app.get("/admin/logins")
def login_history(limit: int = Query(200, ge=1, le=1000), a: dict = Depends(admin_user)):
    with tx() as c:
        rows = c.execute("SELECT * FROM logins ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return [{k: r[k] for k in r.keys() if k != "sub"} for r in rows]


@app.post("/admin/stream-ticket")
def stream_ticket(a: dict = Depends(admin_user)):
    # Browsers can't send headers on a live stream, so the dashboard gets a 60-second single-purpose ticket.
    return {"ticket": make_ticket(a["email"])}


@app.get("/admin/stream")
async def admin_stream(request: Request, ticket: str = Query(...)):
    try:
        claims = jwt.decode(ticket, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Ticket expired")
    if claims.get("pur") != "stream" or claims.get("sub") not in ADMIN_EMAILS:
        raise HTTPException(403, "Admins only")
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _admin_streams.add(q)

    async def events():
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'])}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            _admin_streams.discard(q)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# Registered last so it never shadows an API route like /health or /me.
@app.get("/{name}", include_in_schema=False)
def page(name: str):
    if name not in PAGES or not (PAGES_DIR / name).is_file():   # fixed list → no path tricks
        raise HTTPException(404, "Not found")
    return FileResponse(PAGES_DIR / name, media_type=PAGES[name])
