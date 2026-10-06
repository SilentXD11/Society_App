# Society App

A housing society management system. It covers maintenance billing, recurring bills and loan EMIs, payments that need two signatories, fixed deposits, staff, vendors, AMC contracts, documents and reminders.

| Folder | What it is |
|---|---|
| [`backend/`](backend/) | FastAPI + PostgreSQL API with phone/email OTP login, roles, dual-approval payments, recurring bills, FDs, a payment calendar and a daily reminder job. See [backend/README.md](backend/README.md). |
| [`frontend/`](frontend/) | `index.html`, the Society Register web app prototype. It covers the dashboard, upcoming payments, maintenance, payments, FDs, security, sweepers, vendors, AMC, documents, tenant documents, e-signatures, complaints, notices, reminders, committee handover and the audit log, with PDF/CSV export. |

## Quick start (backend)

```bash
cd backend
cp .env.example .env          # set SR_JWT_SECRET and SR_OTP_SECRET
docker compose up --build     # Postgres + migrations + API on http://localhost:8000/docs
docker compose run --rm api python -m app.cli create-society \
  --name "Your CHS" --admin-name "Your Name" --admin-phone 98XXXXXXXX --monthly-charge 2500
```

Login codes print to the API log while `SR_SMS_PROVIDER=console`.

## About the frontend

`frontend/index.html` was built as a Claude artifact. On claude.ai it saves data in the artifact's own storage. Opened anywhere else, it runs in a temporary mode where data disappears on reload. The next step is to replace that storage layer with calls to the backend API.

## Tests

```bash
cd backend
pip install -e ".[dev]"
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/sr_test pytest
```
