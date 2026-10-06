# Society Register API

Backend for a housing society register. It handles:
- login with phone or email one-time codes;
- committee and resident roles;
- maintenance billing with part-payments;
- recurring bills and loan EMIs;
- outgoing payments that need two bank signatories;
- fixed deposits with renew and close;
- a payment calendar;
- a daily reminder job that messages the committee and residents.

Built with FastAPI, SQLAlchemy 2 (async) and PostgreSQL 16. It is tested against a real Postgres database: 17 tests, all passing.

## Where things are stored

| What | Where |
|---|---|
| People who can log in | `users`: name, phone (E.164) and/or email. **No passwords.** |
| What they may do in a society | `memberships`: role `committee` / `owner` / `tenant` / `viewer`, title, `is_signatory` |
| Login codes | `otp_codes`: only an HMAC of each code, with expiry and attempt count |
| Sessions | Access tokens are 15-minute JWTs and aren't stored. Refresh tokens are in `refresh_tokens` as SHA-256 hashes and are rotated on every use. Reusing an old one revokes all of that user's sessions. |
| Passkeys | `webauthn_credentials` (table ready; endpoints are the next step) |
| Society data | Flats, residents, maintenance, bills, payments, FDs, staff, vendors, AMC, documents, e-sign, complaints, notices, handovers: see `db/schema.sql` |
| Files | S3. Tables keep only the key, file name, size and SHA-256. |
| Messages sent | `notification_log`. Its `dedupe_key` is unique, so a reminder is never sent twice. |
| Who changed what | `audit_log`. It is append-only, and a database trigger rejects any UPDATE or DELETE. |

Every business table has `society_id`, so one deployment can serve many societies.

## Run it

```bash
cp .env.example .env            # set SR_JWT_SECRET and SR_OTP_SECRET
docker compose up --build       # Postgres + migration + API on :8000

# create the first society and its first committee member (a signatory)
docker compose run --rm api python -m app.cli create-society \
  --name "Shanti Kunj CHS" --admin-name "R. Shah" --admin-phone 9820000001 --monthly-charge 2500
```

Open http://localhost:8000/docs. With `SR_SMS_PROVIDER=console`, login codes are printed in the API's log.

Without Docker, use Python 3.11+ and a Postgres you can reach:

```bash
pip install -e ".[dev]"
export SR_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/society_register
alembic upgrade head
uvicorn app.main:app --reload
```

## How it works

### Login
1. `POST /auth/otp/request {destination}` always returns 202, so callers can't tell whether a number or email is registered.
2. `POST /auth/otp/verify {destination, code}` returns an access token and a refresh token.
3. `POST /auth/refresh` rotates the refresh token. `POST /auth/logout` revokes it.

Limits: 3 code requests per destination every 10 minutes, 5 guesses per code, and each code expires after 10 minutes.

### Sensitive actions need a fresh confirmation ("step-up")
These actions need a recent re-confirmation:
- approving or rejecting payments;
- renewing or closing FDs;
- adding or removing members.

Call `POST /auth/step-up/request` and then `/auth/step-up/verify {code}`. Pass the returned token in an `X-Step-Up` header. It lasts 5 minutes. If the header is missing, the API answers 401 with `X-Step-Up-Required: true`, so the frontend knows to prompt.

### Dual-approval payments
The rules live in `app/services/approvals.py` and run under a row lock:
- Only committee members with `is_signatory` can approve.
- With `SR_MAKER_CHECKER=true`, the person who raised a payment can't approve it.
- The same person can't approve twice. The table's primary key also enforces this.
- Two distinct approvals change the status to `approved`.
- Only an approved payment can be marked `paid`.

### Recurring bills and EMIs
- **Raise payment** (`POST /bills/{id}/raise-payment`) creates a payment request for the current due date and moves the bill to its next date. If that payment is rejected, the bill moves back.
- **Auto-debit** (`POST /bills/{id}/auto-debit`) is for standing instructions. It records the payment as already paid, with no approval step.
- Month-end dates stay anchored. A bill due on the 31st falls on 28 Feb, then 31 Mar.
- EMIs stop automatically after the last instalment.
- Staff-salary bills (`security_pay`, `sweeper_pay`, `all_staff_pay`) total the current pay of active staff.

### Payment calendar
`GET /societies/{id}/upcoming?days=90` lists everything due in the window:
- bills and EMIs;
- approved payments waiting to be paid;
- FD maturities;
- maintenance expected from residents.

It also returns totals to pay, totals coming in and the overdue total. Residents only see the maintenance items.

### Daily reminders
Schedule this once a day with cron, EventBridge or a Kubernetes CronJob:

```bash
python -m app.jobs.daily_reminders
```

| Who | When |
|---|---|
| Committee (one digest by email, or SMS if they have no email) | A bill or EMI reaches its reminder lead time, its due day, or becomes overdue. An FD reaches 30 days before maturity, 7 days before, the maturity day, or is past maturity. |
| Flat owners (SMS, or email if no phone) | Maintenance is due in 3 days, is 3 days overdue, or is 10 days overdue, while still unpaid |

Each stage is sent once. Running the job twice in a day sends nothing new. `POST /societies/{id}/reminders/run` runs it for one society straight away.

## Tests

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/sr_test pytest
```

The tests drop and recreate that database from `db/schema.sql`. They cover:
- OTP login, lockout, rate limiting, and refresh-token reuse detection;
- every dual-approval rule;
- a bill moving forward on raise and back on reject;
- an EMI finishing after its last instalment;
- staff-pay bills and the payment calendar;
- FD maturity estimates, renew and close;
- reminder stages and de-duplication;
- part-payments, and residents seeing only their own dues;
- the audit log refusing deletes.

## Status and next steps

**Built and tested:** auth, members and roles, flats and residents, maintenance, recurring bills, payments, FDs, vendors, staff, payment calendar, reminders, audit log.

**Tables exist, endpoints still to write:**
1. **Documents and tenant KYC.** Upload through S3 presigned URLs, with tenant uploads visible only to the uploader and the committee (`documents.visibility`).
2. **Passkeys.** Use `py_webauthn` for registration and assertion, and have a successful assertion issue a step-up token with `amr=passkey`.
3. **AMC contracts and visits.** Auto-book the next visit when one is completed, using the logic from the prototype.
4. **E-signatures.** Sign against `doc_sha256` and store the signature image in S3.
5. **Committee handover.** Approvals are enforced as one per side and one per person.
6. Attendance, complaints, notices and manual reminders.
7. **WhatsApp reminders.** Gupshup, Interakt or Twilio, through a new `Notifier`.
8. **Postgres row-level security** on `society_id` as defence in depth.

**Providers:** the MSG91 (SMS) and SES (email) notifiers follow those services' public APIs but haven't been run against live accounts. Test them with your own credentials first. MSG91 needs a DLT-registered template in India.
