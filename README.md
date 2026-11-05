# Society Grocery App — setup guide

```
society-app/
├── start-windows.bat    ← double-click to run everything (Windows)
├── start-mac.command    ← double-click to run everything (Mac)
├── start-phone.bat      ← run it with a secure link your phone can open (Windows)
├── manifest.webmanifest, icon-*.png  ← lets phones install it as an app icon
├── society-app.html     ← resident app (what members use)
├── admin.html           ← your dashboard: orders, who logged in, residents
└── backend/
    ├── main.py          ← the secure server
    ├── setup_env.py     ← first-time setup (the start script runs it)
    ├── catalog.json     ← products + every shop's price (server's copy)
    ├── requirements.txt
    └── .env.example     ← settings template
```

## How login works (Auth0)

```
Person taps "Log in"  ──►  Auth0 login page (email + password, Google, …)
                                   │  Auth0 proves who they are
                                   ▼
                     Your server checks: signed by YOUR Auth0? email verified? on the society register?
                         │                                     │
                    yes  ▼                                no   ▼
               Shop, order, track            "Waiting for approval" screen + shows up in your
                                              dashboard → Residents → "Waiting for access"
```

Every sign-in — allowed or not — is recorded. **Dashboard → Logins** shows who, when, which flat, result, device and IP address, updating live.
Auth0's own dashboard also keeps a full log under **Monitoring → Logs**.

## 1. Set up Auth0 (free, ~10 minutes, one time)

1. Sign up at **auth0.com**.
2. **Applications → Applications → Create Application**. Name it `Society App`, choose **Single Page Web Applications**, click **Create**.
3. Open its **Settings** tab. Copy **Domain** and **Client ID** (the start script will ask for them). Scroll down and fill in:
   - **Allowed Callback URLs**: `http://localhost:5500/society-app.html, http://localhost:5500/admin.html`
   - **Allowed Logout URLs**: `http://localhost:5500/society-app.html, http://localhost:5500/admin.html`
   - **Allowed Web Origins**: `http://localhost:5500`

   Click **Save Changes** at the bottom.
4. **Applications → APIs → Create API**. Name `Society API`, Identifier `https://society-api` (this is the "API Identifier" the start script asks for). Leave signing as **RS256**. Click **Create**.
   Then in that API's **Settings**, turn on **Allow Offline Access** and save (keeps people logged in).
5. Choose how people can sign in under **Authentication**: *Database* (email + password, on by default) and/or *Social → Google*.
   Auth0 sends a verification email to new email/password sign-ups; the app only lets people in once their email is verified.

Always open the pages as **http://localhost:5500/…** (not 127.0.0.1) — Auth0 only returns to the exact addresses listed above.

## 2. Run it on your computer

Install Python from python.org (on Windows tick *"Add python.exe to PATH"*), then double-click
**start-windows.bat** (Windows) or **start-mac.command** (Mac).

The first time, it asks for your **Auth0 Domain, Client ID, API Identifier** and **your email** — that email becomes the admin.
It then starts the server and opens the dashboard. Click **Log in** and sign in to Auth0 with that email.
Keep the windows it opens running while you use the app.

To make someone a resident: they open **http://localhost:5500/society-app.html** and sign up → they appear under
**Residents → Waiting for access** → type their flat (and mobile, so you can call them about orders) → **Approve**.
Or add them in advance with **Add a resident** using the email they'll sign in with.

Don't open admin.html by double-clicking it — it needs the server. (Double-clicking society-app.html still works, as a demo with no login.)

**Manual way:** `cd backend`, create a venv, `pip install -r requirements.txt`, copy `.env.example` to `.env` and fill it in,
`uvicorn main:app --port 8000`; in a second terminal from the `society-app` folder run `python -m http.server 5500`.

## 3. Use it on your phone

Your laptop runs the app; **ngrok** gives it a free, permanent `https://…ngrok-free.app` address your phone can open
from anywhere (Wi-Fi or mobile data). Auth0 only works over https, which is why a plain Wi-Fi address won't do.

**One-time setup (~10 min)**
1. Sign up free at https://dashboard.ngrok.com/signup
2. Install it: open **PowerShell** and run `winget install ngrok.ngrok`
3. Connect your account: copy the command from https://dashboard.ngrok.com/get-started/your-authtoken
   (`ngrok config add-authtoken 2abc…`) and run it in PowerShell.
4. Claim your free domain at https://dashboard.ngrok.com/domains — e.g. `calm-otter-123.ngrok-free.app`
5. In Auth0 → Applications → Society App → Settings, **add** (comma after the existing entries):
   - Allowed Callback URLs: `https://YOUR-DOMAIN/society-app.html, https://YOUR-DOMAIN/admin.html`
   - Allowed Logout URLs: the same two
   - Allowed Web Origins: `https://YOUR-DOMAIN`

   Save Changes.

**Every time:** double-click **start-phone.bat** (the first time it asks for your ngrok domain). Then on your phone open
`https://YOUR-DOMAIN/society-app.html`. ngrok shows a one-time "You are about to visit" page — tap **Visit Site**.
The admin dashboard is at `https://YOUR-DOMAIN/admin.html` and works on the phone too.

**Make it an app icon:** Android Chrome → ⋮ → **Add to Home screen** / **Install app**. iPhone Safari → Share → **Add to Home Screen**.
It then opens full-screen like a normal app.

The link only works while the laptop is on and the two windows stay open. Anyone with the link can reach the login
page, but only people you approve can shop. For residents to use it any time, it needs proper hosting (see checklist below).

## 4. Get order alerts on your phone (Telegram, free)

1. In Telegram, open **@BotFather** → `/newbot` → pick a name → copy the **token** into `TELEGRAM_BOT_TOKEN` in `backend/.env`.
2. Alerts to **you only**: send any message to your new bot.
   Alerts to **your whole team**: create a group, add the bot and your team, send a message in the group.
3. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and copy the `"chat":{"id": ...}` number
   (group IDs start with `-`) into `TELEGRAM_CHAT_ID`.
4. Restart. You'll get a message for every new order, every UPI payment to check, and every new person asking for access.

## 5. Before real residents use it — checklist

- [ ] Host the server with HTTPS (Render, Railway, AWS Lightsail, or a VPS with Nginx + Let's Encrypt). Never plain `http://` for real users
- [ ] Add your real https page addresses to the three Auth0 URL fields (step 1.3), set `ALLOWED_ORIGINS` in `.env`, and put the server address in `API_BASE` in both HTML files
- [ ] `DEV_MODE=false` (hides the `/docs` test page)
- [ ] In Auth0, set up your own Google keys if you use Google login (Auth0's built-in dev keys are for testing only), and consider turning on **Security → Attack Protection** (brute-force and breached-password protection)
- [ ] Keep `.env` and `*.db` out of GitHub (already in `.gitignore`); back up `backend/society-auth0.db` daily
- [ ] If you run more than one server copy, move the rate limiter to Redis
- [ ] When you move from manual UPI checks to a payment gateway (Razorpay/Cashfree), mark orders paid **only** from the gateway's signed webhook on the server

## What's protecting the app

| Threat | Protection |
|---|---|
| Strangers using the app | Auth0 sign-in **plus** your approval — only verified emails on the society register can shop |
| Fake or stolen-looking logins | Server checks every token is signed by *your* Auth0 tenant, for *this* API, and not expired (tested: attacker-signed, wrong audience, wrong issuer, expired and unsigned tokens are all rejected) |
| Passwords | Never touch your server — Auth0 stores and checks them |
| Changing prices in the app | Server ignores app prices and recalculates from `catalog.json` |
| Fake "I paid" | Resident must give the 12-digit UTR; each UTR can be used once; a UPI order can't be packed until you confirm the money arrived |
| Resident opening admin screens | Admin rights come from `ADMIN_EMAILS` on the server, checked on every request |
| Ex-resident with an old login | **Block** in the Residents tab locks them out on their very next action |
| Other websites calling your server | CORS allows only your own app's address |
| Bad / oversized input | Every field validated (quantities 1–20, max 60 items, 64 KB request limit) |
| Who did what | Every sign-in (Logins tab), order and status change is recorded |
| Live feed token leaking in URLs | The dashboard uses a 60-second single-purpose ticket, not your login token |

## Updating products or prices

Prices currently live in two places: the app's `PRODUCTS`/`VENDORS` list (for display) and `backend/catalog.json` (what's actually charged).
The next step is to have the app load `GET /catalog` from the server so you only edit prices in one place.
