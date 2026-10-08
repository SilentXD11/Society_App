# Society App

A housing society management system. It covers maintenance billing, recurring bills and loan EMIs, payments that need two signatories, fixed deposits, staff, vendors, AMC contracts, documents and reminders.

| Folder | What it is |
|---|---|
| [`frontend/`](frontend/) | `index.html`, the Society Register web app. It covers the dashboard, upcoming payments, maintenance, payments, FDs, security, sweepers, vendors, AMC, documents, tenant documents, e-signatures, complaints, notices, reminders, committee handover and the audit log, with PDF/CSV export. |
| [`backend/`](backend/) | FastAPI + PostgreSQL API with phone/email OTP login, roles, dual-approval payments, recurring bills, FDs, a payment calendar and a daily reminder job. See [backend/README.md](backend/README.md). |

## Publish online for free

| Part | Free host | Address |
|---|---|---|
| Website | GitHub Pages | https://silentxd11.github.io/Society_App/ |
| API | Render (free web service) | `https://society-register-api.onrender.com` (or similar) |
| Database | Neon (free Postgres) | — |
| Daily reminders | GitHub Actions (scheduled) | — |

Render's own free database is deleted 30 days after it's created, so the database is on Neon instead. Neon's free plan has 1 GB of storage and pauses after 5 minutes of no use. It wakes on the next request.

### 1. Website on GitHub Pages (2 minutes)
1. Go to the repo's **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.
3. Go to **Actions → Deploy website → Run workflow**, or push any change to `frontend/`.
4. Open https://silentxd11.github.io/Society_App/.

On GitHub Pages the website saves records **in the browser of the device you use**. Other committee members won't see them. Use **Settings → Download backup** regularly. Shared data and logins need the API below, plus connecting the website to it (the next development step).

### 2. Database on Neon (3 minutes)
1. Sign up at https://neon.com. Signing in with GitHub works.
2. Create a project. Pick the region **AWS Asia Pacific (Singapore)**, which is closest to India.
3. Click **Connect**, turn **Connection pooling off**, and copy the connection string. It looks like `postgresql://…@ep-….neon.tech/neondb?sslmode=require…`.

### 3. API on Render (5 minutes)
1. Sign up at https://render.com with GitHub.
2. Choose **New → Blueprint** and pick this repo. Render reads [`render.yaml`](render.yaml).
3. When asked for `SR_DATABASE_URL`, paste the Neon connection string. The secrets are generated for you.
4. Deploy. The first build takes a few minutes. Database tables are created automatically on start-up.
5. Open `https://<your-service>.onrender.com/docs`.

The free service sleeps after 15 minutes without requests. The first request after that takes about a minute to wake it.

### 4. Create your society and first login
1. In Render, open the service's **Environment** tab and copy the value of `SR_SETUP_TOKEN`.
2. In `/docs`, open **POST /setup/society**, click **Try it out**, put the token in `X-Setup-Token`, and send:
   ```json
   {"society_name": "Your CHS", "admin_name": "Your Name", "admin_phone": "98XXXXXXXX", "monthly_charge": "2500"}
   ```
3. Log in with **POST /auth/otp/request** using that phone number. Until an SMS provider is set up, the 6-digit code appears in Render's **Logs** tab.
4. Delete `SR_SETUP_TOKEN` from Render's environment. That switches the setup endpoint off.

### 5. Daily reminders (optional, when you have an SMS or email provider)
In **Settings → Secrets and variables → Actions**:
1. Add the secret `SR_DATABASE_URL` (the Neon string).
2. Add the variable `SR_SMS_PROVIDER` = `msg91`.
3. Add the secret `SR_MSG91_AUTH_KEY`, and the variables `SR_MSG91_SENDER_ID` and `SR_MSG91_FLOW_ID`.

[`.github/workflows/reminders.yml`](.github/workflows/reminders.yml) then runs every morning at about 08:47 India time. It stays off until a real provider is set, so no reminder is marked as sent without reaching anyone.

## Run locally

```bash
cd backend
cp .env.example .env          # set SR_JWT_SECRET and SR_OTP_SECRET
docker compose up --build     # Postgres + migrations + API on http://localhost:8000/docs
```

Open `frontend/index.html` in a browser for the website.

## Tests

[`.github/workflows/tests.yml`](.github/workflows/tests.yml) runs the backend tests against Postgres 16 on every push. To run them locally:

```bash
cd backend
pip install -e ".[dev]"
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/sr_test pytest
```
