"""First-time setup: asks for your Auth0 details + admin email and saves them in backend/.env.
Runs every time the start script runs, but only asks for settings that are still missing."""
import pathlib
import re
import secrets
import sys

env = pathlib.Path(__file__).parent / ".env"
values: dict[str, str] = {}
if env.exists():
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()


def ask(key, prompt, check, fix=lambda s: s.strip()):
    if values.get(key):
        return
    while True:
        v = fix(input(prompt))
        if check(v):
            values[key] = v
            return
        print("   That doesn't look right, please try again.")


missing = [k for k in ("AUTH0_DOMAIN", "AUTH0_CLIENT_ID", "AUTH0_AUDIENCE", "ADMIN_EMAILS") if not values.get(k)]
if missing:
    print("\n=== Setup: Auth0 login ===")
    print("Open your Auth0 dashboard (README.md → 'Set up Auth0' has the exact clicks).\n")

ask("AUTH0_DOMAIN", "Auth0 Domain (Applications → your app → Settings, e.g. dev-abc123.us.auth0.com): ",
    lambda v: re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", v) is not None,
    lambda s: s.strip().lower().removeprefix("https://").rstrip("/"))
ask("AUTH0_CLIENT_ID", "Auth0 Client ID (same Settings page): ",
    lambda v: re.fullmatch(r"[A-Za-z0-9_-]{16,}", v) is not None)
ask("AUTH0_AUDIENCE", "API Identifier (Applications → APIs, e.g. https://society-api): ",
    lambda v: len(v) >= 3 and " " not in v)
ask("ADMIN_EMAILS", "YOUR email address (the one you'll log in with — this becomes the admin): ",
    lambda v: all(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e.strip()) for e in v.split(",")),
    lambda s: s.strip().lower())

if "--phone" in sys.argv and not values.get("PHONE_DOMAIN"):
    print("\n=== Setup: phone link (ngrok) ===")
    print("Your free domain is at https://dashboard.ngrok.com/domains (looks like calm-otter-123.ngrok-free.app)\n")
ask("PHONE_DOMAIN", "Your ngrok domain: ",
    lambda v: re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", v) is not None,
    lambda s: s.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]) if "--phone" in sys.argv else None

if len(values.get("JWT_SECRET", "")) < 32:
    values["JWT_SECRET"] = secrets.token_urlsafe(48)
values["ALLOWED_ORIGINS"] = values.get("ALLOWED_ORIGINS") or "http://localhost:5500"
for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    values.setdefault(k, "")
values.setdefault("DEV_MODE", "true")
values.pop("ADMIN_PHONES", None)   # old SMS-login setting, no longer used

order = ["AUTH0_DOMAIN", "AUTH0_CLIENT_ID", "AUTH0_AUDIENCE", "ADMIN_EMAILS", "JWT_SECRET", "ALLOWED_ORIGINS",
         "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DEV_MODE", "PHONE_DOMAIN"]
keys = [k for k in order if k in values] + [k for k in values if k not in order]
env.write_text("".join(f"{k}={values[k]}\n" for k in keys), encoding="utf-8")
if missing:
    print(f"\nSaved. Admin login: {values['ADMIN_EMAILS']}\n")
