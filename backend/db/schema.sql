-- =====================================================================
-- Society Register — PostgreSQL schema (PostgreSQL 14+)
--
-- Multi-society: every business table carries society_id, so one
-- deployment can serve many housing societies.
-- Money is NUMERIC(14,2). Times are TIMESTAMPTZ (store UTC).
-- Files live in S3; tables store only the object key.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS citext;     -- case-insensitive email

-- ---------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------
CREATE TYPE member_role       AS ENUM ('committee', 'owner', 'tenant', 'viewer');
CREATE TYPE otp_channel       AS ENUM ('sms', 'email');
CREATE TYPE otp_purpose       AS ENUM ('login', 'step_up');
CREATE TYPE occupancy_type    AS ENUM ('owner', 'tenant', 'vacant');
CREATE TYPE resident_kind     AS ENUM ('owner', 'tenant');
CREATE TYPE bill_status       AS ENUM ('unpaid', 'paid', 'waived');
CREATE TYPE pay_mode          AS ENUM ('upi', 'bank_transfer', 'cash', 'cheque', 'card', 'auto_debit');
CREATE TYPE frequency_type    AS ENUM ('monthly', 'quarterly', 'half_yearly', 'yearly', 'one_time');
CREATE TYPE amount_source     AS ENUM ('fixed', 'security_pay', 'sweeper_pay', 'all_staff_pay');
CREATE TYPE payment_status    AS ENUM ('pending_approval', 'approved', 'rejected', 'paid');
CREATE TYPE approval_method   AS ENUM ('passkey', 'otp');
CREATE TYPE fd_payout         AS ENUM ('cumulative', 'monthly', 'quarterly');
CREATE TYPE fd_status         AS ENUM ('active', 'matured', 'renewed', 'closed');
CREATE TYPE staff_role        AS ENUM ('security', 'sweeper', 'other');
CREATE TYPE attendance_mark   AS ENUM ('present', 'absent', 'leave');
CREATE TYPE visit_status      AS ENUM ('scheduled', 'completed', 'missed');
CREATE TYPE doc_kind          AS ENUM ('general', 'tenant_kyc', 'amc_report', 'invoice', 'fd_receipt', 'esign_source');
CREATE TYPE doc_visibility    AS ENUM ('committee', 'residents', 'uploader_and_committee');
CREATE TYPE review_status     AS ENUM ('pending', 'verified', 'rejected');
CREATE TYPE complaint_status  AS ENUM ('open', 'in_progress', 'resolved');
CREATE TYPE handover_status   AS ENUM ('draft', 'pending_approval', 'approved');
CREATE TYPE handover_side     AS ENUM ('outgoing', 'incoming');
CREATE TYPE notify_status     AS ENUM ('queued', 'sent', 'failed', 'skipped');

-- ---------------------------------------------------------------------
-- Societies, people, login
-- ---------------------------------------------------------------------
CREATE TABLE societies (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             TEXT NOT NULL,
    reg_no           TEXT,
    address          TEXT,
    currency         CHAR(3)       NOT NULL DEFAULT 'INR',
    timezone         TEXT          NOT NULL DEFAULT 'Asia/Kolkata',
    due_day          SMALLINT      NOT NULL DEFAULT 10 CHECK (due_day BETWEEN 1 AND 28),
    monthly_charge   NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (monthly_charge >= 0),
    opening_balance  NUMERIC(14,2) NOT NULL DEFAULT 0,
    pay_info         TEXT,                       -- UPI ID / bank details shown in reminders
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- A person who can log in. Login is by phone or email OTP; no passwords.
CREATE TABLE users (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name      TEXT NOT NULL,
    phone          TEXT UNIQUE,                  -- E.164, e.g. +919820000001
    email          CITEXT UNIQUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at  TIMESTAMPTZ,
    CHECK (phone IS NOT NULL OR email IS NOT NULL),
    CHECK (phone IS NULL OR phone ~ '^\+[1-9][0-9]{7,14}$')
);

-- What a user may do in a society. One row per (society, user).
CREATE TABLE memberships (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id    UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role          member_role NOT NULL,
    title         TEXT,                          -- Chairman, Secretary, Treasurer…
    is_signatory  BOOLEAN NOT NULL DEFAULT false, -- may approve payments
    active        BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (society_id, user_id),
    CHECK (NOT is_signatory OR role = 'committee')
);

-- One-time codes. Only a hash of the code is stored.
CREATE TABLE otp_codes (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID REFERENCES users(id) ON DELETE CASCADE,
    destination  TEXT NOT NULL,                  -- phone or email the code went to
    channel      otp_channel NOT NULL,
    purpose      otp_purpose NOT NULL,
    code_hash    TEXT NOT NULL,                  -- HMAC-SHA256(code, server secret)
    attempts     SMALLINT NOT NULL DEFAULT 0,
    expires_at   TIMESTAMPTZ NOT NULL,
    consumed_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_ip   INET
);
CREATE INDEX otp_codes_dest_recent ON otp_codes (destination, created_at DESC);

-- Long-lived refresh tokens (access tokens are short-lived JWTs, not stored).
CREATE TABLE refresh_tokens (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT NOT NULL UNIQUE,           -- SHA-256 of the opaque token
    user_agent   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    revoked_at   TIMESTAMPTZ
);
CREATE INDEX refresh_tokens_user ON refresh_tokens (user_id) WHERE revoked_at IS NULL;

-- Passkeys (WebAuthn) for step-up on sensitive actions.
CREATE TABLE webauthn_credentials (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    credential_id  BYTEA NOT NULL UNIQUE,
    public_key     BYTEA NOT NULL,
    sign_count     BIGINT NOT NULL DEFAULT 0,
    transports     TEXT[],
    nickname       TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at   TIMESTAMPTZ
);

-- ---------------------------------------------------------------------
-- Flats and residents
-- ---------------------------------------------------------------------
CREATE TABLE flats (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id         UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    wing               TEXT NOT NULL DEFAULT '',
    number             TEXT NOT NULL,
    area_sqft          NUMERIC(8,2),
    parking            TEXT,
    occupancy          occupancy_type NOT NULL DEFAULT 'owner',
    monthly_charge     NUMERIC(14,2) CHECK (monthly_charge >= 0),  -- NULL = society default
    notes              TEXT,
    UNIQUE (society_id, wing, number)
);

-- Owners and tenants of a flat, current and past. user_id links a login.
CREATE TABLE residents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id  UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    flat_id     UUID NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    kind        resident_kind NOT NULL,
    full_name   TEXT NOT NULL,
    phone       TEXT,
    email       CITEXT,
    moved_in    DATE,
    moved_out   DATE,
    CHECK (moved_out IS NULL OR moved_in IS NULL OR moved_out >= moved_in)
);
CREATE INDEX residents_flat_current ON residents (flat_id) WHERE moved_out IS NULL;
CREATE INDEX residents_user ON residents (user_id);

-- ---------------------------------------------------------------------
-- Maintenance (money coming in)
-- ---------------------------------------------------------------------
CREATE TABLE maintenance_bills (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id  UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    flat_id     UUID NOT NULL REFERENCES flats(id) ON DELETE CASCADE,
    period      DATE NOT NULL CHECK (date_trunc('month', period) = period),  -- first of month
    amount      NUMERIC(14,2) NOT NULL CHECK (amount >= 0),
    due_date    DATE NOT NULL,
    status      bill_status NOT NULL DEFAULT 'unpaid',
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (flat_id, period)
);
CREATE INDEX maintenance_unpaid ON maintenance_bills (society_id, due_date) WHERE status = 'unpaid';

CREATE TABLE maintenance_receipts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bill_id      UUID NOT NULL REFERENCES maintenance_bills(id) ON DELETE CASCADE,
    amount       NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    paid_on      DATE NOT NULL,
    mode         pay_mode NOT NULL,
    reference    TEXT,
    recorded_by  UUID REFERENCES users(id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Vendors, recurring bills, outgoing payments with dual approval
-- ---------------------------------------------------------------------
CREATE TABLE vendors (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id  UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    service     TEXT,
    contact     TEXT,
    phone       TEXT,
    email       CITEXT,
    gstin       TEXT CHECK (gstin IS NULL OR gstin ~ '^[0-9]{2}[A-Z0-9]{13}$'),
    address     TEXT,
    active      BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (society_id, name)
);

-- Bills the society pays on a schedule: electricity, salaries, EMIs…
CREATE TABLE recurring_bills (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id          UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    category            TEXT NOT NULL,
    vendor_id           UUID REFERENCES vendors(id) ON DELETE SET NULL,
    payee               TEXT,
    amount_source       amount_source NOT NULL DEFAULT 'fixed',
    amount              NUMERIC(14,2) CHECK (amount >= 0),
    frequency           frequency_type NOT NULL DEFAULT 'monthly',
    next_due            DATE,                    -- NULL once finished
    anchor_day          SMALLINT CHECK (anchor_day BETWEEN 1 AND 31),  -- keeps the 31st on the 31st
    lead_days           SMALLINT NOT NULL DEFAULT 7 CHECK (lead_days BETWEEN 0 AND 90),
    autopay             BOOLEAN NOT NULL DEFAULT false,
    lender              TEXT,                    -- loans
    total_instalments   INTEGER CHECK (total_instalments > 0),
    paid_instalments    INTEGER NOT NULL DEFAULT 0 CHECK (paid_instalments >= 0),
    active              BOOLEAN NOT NULL DEFAULT true,
    notes               TEXT,
    CHECK (amount_source <> 'fixed' OR amount IS NOT NULL),
    CHECK (vendor_id IS NOT NULL OR payee IS NOT NULL),
    CHECK (total_instalments IS NULL OR paid_instalments <= total_instalments)
);
CREATE INDEX recurring_bills_due ON recurring_bills (society_id, next_due) WHERE active;

CREATE TABLE payment_requests (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id         UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    recurring_bill_id  UUID REFERENCES recurring_bills(id) ON DELETE SET NULL,
    bill_due_date      DATE,                     -- which occurrence of the bill this pays
    vendor_id          UUID REFERENCES vendors(id) ON DELETE SET NULL,
    payee              TEXT NOT NULL,
    purpose            TEXT NOT NULL,
    category           TEXT NOT NULL,
    amount             NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    mode               pay_mode NOT NULL,
    cheque_no          TEXT,
    invoice_doc_id     UUID,                     -- FK added below (documents)
    status             payment_status NOT NULL DEFAULT 'pending_approval',
    requested_by       UUID NOT NULL REFERENCES users(id),
    requested_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    reject_reason      TEXT,
    paid_on            DATE,
    reference          TEXT,
    paid_by            UUID REFERENCES users(id),
    CHECK (status <> 'paid' OR paid_on IS NOT NULL),
    CHECK (status <> 'rejected' OR reject_reason IS NOT NULL)
);
CREATE INDEX payment_requests_status ON payment_requests (society_id, status);

-- One row per signatory approval. Two distinct signatories => approved.
CREATE TABLE payment_approvals (
    payment_id   UUID NOT NULL REFERENCES payment_requests(id) ON DELETE CASCADE,
    user_id      UUID NOT NULL REFERENCES users(id),
    method       approval_method NOT NULL,
    approved_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (payment_id, user_id)            -- same person can't approve twice
);

-- ---------------------------------------------------------------------
-- Fixed deposits
-- ---------------------------------------------------------------------
CREATE TABLE fixed_deposits (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id       UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    bank             TEXT NOT NULL,
    branch           TEXT,
    fd_last4         CHAR(4) CHECK (fd_last4 ~ '^[0-9]{4}$'),   -- never the full number
    principal        NUMERIC(14,2) NOT NULL CHECK (principal > 0),
    rate_pct         NUMERIC(5,2) CHECK (rate_pct >= 0 AND rate_pct < 50),
    payout           fd_payout NOT NULL DEFAULT 'cumulative',
    start_date       DATE NOT NULL,
    maturity_date    DATE NOT NULL,
    maturity_amount  NUMERIC(14,2) CHECK (maturity_amount > 0),
    auto_renew       BOOLEAN NOT NULL DEFAULT false,
    holder           TEXT,
    nominee          TEXT,
    status           fd_status NOT NULL DEFAULT 'active',
    renewed_from_id  UUID REFERENCES fixed_deposits(id),
    closed_on        DATE,
    closed_amount    NUMERIC(14,2),
    receipt_doc_id   UUID,
    notes            TEXT,
    CHECK (maturity_date > start_date),
    CHECK (status NOT IN ('closed') OR closed_on IS NOT NULL)
);
CREATE INDEX fixed_deposits_maturity ON fixed_deposits (society_id, maturity_date) WHERE status = 'active';

-- ---------------------------------------------------------------------
-- Staff
-- ---------------------------------------------------------------------
CREATE TABLE staff (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id    UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    role          staff_role NOT NULL,
    full_name     TEXT NOT NULL,
    phone         TEXT,
    post          TEXT,                          -- Main gate / Wing A
    shift         TEXT,
    agency_id     UUID REFERENCES vendors(id) ON DELETE SET NULL,
    monthly_pay   NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (monthly_pay >= 0),
    joined_on     DATE,
    left_on       DATE,
    id_proof_note TEXT
);
CREATE INDEX staff_active ON staff (society_id, role) WHERE left_on IS NULL;

CREATE TABLE attendance (
    staff_id   UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    day        DATE NOT NULL,
    mark       attendance_mark NOT NULL,
    marked_by  UUID REFERENCES users(id),
    marked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (staff_id, day)
);

-- ---------------------------------------------------------------------
-- Documents (files in S3)
-- ---------------------------------------------------------------------
CREATE TABLE documents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id    UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    kind          doc_kind NOT NULL DEFAULT 'general',
    title         TEXT NOT NULL,
    category      TEXT,
    flat_id       UUID REFERENCES flats(id) ON DELETE SET NULL,
    dated         DATE,
    expires_on    DATE,
    s3_key        TEXT NOT NULL UNIQUE,
    file_name     TEXT NOT NULL,
    content_type  TEXT NOT NULL,
    size_bytes    BIGINT NOT NULL CHECK (size_bytes > 0),
    sha256        CHAR(64) NOT NULL,
    visibility    doc_visibility NOT NULL DEFAULT 'committee',
    review        review_status,                 -- tenant KYC only
    reviewed_by   UUID REFERENCES users(id),
    uploaded_by   UUID NOT NULL REFERENCES users(id),
    uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (kind <> 'tenant_kyc' OR (flat_id IS NOT NULL AND review IS NOT NULL))
);
CREATE INDEX documents_expiry ON documents (society_id, expires_on) WHERE expires_on IS NOT NULL;
ALTER TABLE payment_requests ADD FOREIGN KEY (invoice_doc_id) REFERENCES documents(id) ON DELETE SET NULL;
ALTER TABLE fixed_deposits  ADD FOREIGN KEY (receipt_doc_id) REFERENCES documents(id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------
-- AMC contracts and engineer visits
-- ---------------------------------------------------------------------
CREATE TABLE amc_contracts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id       UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    vendor_id        UUID NOT NULL REFERENCES vendors(id),
    equipment        TEXT NOT NULL,
    contract_no      TEXT,
    value            NUMERIC(14,2),
    start_date       DATE NOT NULL,
    end_date         DATE NOT NULL,
    visits_per_year  SMALLINT CHECK (visits_per_year BETWEEN 1 AND 52),
    contract_doc_id  UUID REFERENCES documents(id) ON DELETE SET NULL,
    CHECK (end_date > start_date)
);

CREATE TABLE amc_visits (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id     UUID NOT NULL REFERENCES amc_contracts(id) ON DELETE CASCADE,
    scheduled_at    TIMESTAMPTZ NOT NULL,
    status          visit_status NOT NULL DEFAULT 'scheduled',
    engineer_name   TEXT,
    engineer_phone  TEXT,
    work_done       TEXT,
    report_doc_id   UUID REFERENCES documents(id) ON DELETE SET NULL,
    auto_scheduled  BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX amc_visits_upcoming ON amc_visits (scheduled_at) WHERE status = 'scheduled';

-- ---------------------------------------------------------------------
-- E-signatures
-- ---------------------------------------------------------------------
CREATE TABLE esign_requests (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id     UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    title          TEXT NOT NULL,
    flat_id        UUID REFERENCES flats(id) ON DELETE SET NULL,
    source_doc_id  UUID NOT NULL REFERENCES documents(id),
    doc_sha256     CHAR(64) NOT NULL,            -- hash of exactly what is being signed
    created_by     UUID NOT NULL REFERENCES users(id),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE esign_parties (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id         UUID NOT NULL REFERENCES esign_requests(id) ON DELETE CASCADE,
    role               TEXT NOT NULL,            -- Owner / Tenant / Witness
    full_name          TEXT NOT NULL,
    resident_id        UUID REFERENCES residents(id),
    signed_at          TIMESTAMPTZ,
    signer_user_id     UUID REFERENCES users(id),
    signature_s3_key   TEXT,
    signer_ip          INET,
    signer_user_agent  TEXT,
    in_person          BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (request_id, role),
    CHECK ((signed_at IS NULL) = (signature_s3_key IS NULL))
);

-- ---------------------------------------------------------------------
-- Complaints, notices, manual reminders
-- ---------------------------------------------------------------------
CREATE TABLE complaints (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id   UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    flat_id      UUID REFERENCES flats(id) ON DELETE SET NULL,
    raised_by    UUID REFERENCES users(id),
    category     TEXT NOT NULL,
    title        TEXT NOT NULL,
    detail       TEXT,
    status       complaint_status NOT NULL DEFAULT 'open',
    raised_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ
);

CREATE TABLE notices (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id  UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    pinned      BOOLEAN NOT NULL DEFAULT false,
    issued_by   UUID REFERENCES users(id),
    issued_on   DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE reminders (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id  UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    due_on      DATE NOT NULL,
    lead_days   SMALLINT NOT NULL DEFAULT 7,
    notes       TEXT,
    done_at     TIMESTAMPTZ,
    created_by  UUID REFERENCES users(id)
);

-- ---------------------------------------------------------------------
-- Committee handover
-- ---------------------------------------------------------------------
CREATE TABLE handovers (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    society_id    UUID NOT NULL REFERENCES societies(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    handover_on   DATE NOT NULL,
    outgoing      TEXT,
    incoming      TEXT,
    cash_in_hand  NUMERIC(14,2),
    bank_balance  NUMERIC(14,2),
    checklist     JSONB NOT NULL DEFAULT '{}'::jsonb,
    snapshot      JSONB,                         -- fund / FD / dues figures frozen on submit
    status        handover_status NOT NULL DEFAULT 'draft',
    notes         TEXT,
    created_by    UUID NOT NULL REFERENCES users(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE handover_approvals (
    handover_id  UUID NOT NULL REFERENCES handovers(id) ON DELETE CASCADE,
    side         handover_side NOT NULL,
    user_id      UUID NOT NULL REFERENCES users(id),
    method       approval_method NOT NULL,
    approved_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (handover_id, side),
    UNIQUE (handover_id, user_id)                -- one person can't sign both sides
);

-- ---------------------------------------------------------------------
-- Notifications sent (SMS / email / WhatsApp) — dedupe_key stops repeats
-- ---------------------------------------------------------------------
CREATE TABLE notification_log (
    id                   BIGSERIAL PRIMARY KEY,
    society_id           UUID REFERENCES societies(id) ON DELETE CASCADE,
    user_id              UUID REFERENCES users(id) ON DELETE SET NULL,
    channel              TEXT NOT NULL,          -- sms / email / whatsapp
    destination          TEXT NOT NULL,
    template             TEXT NOT NULL,
    body                 TEXT NOT NULL,
    dedupe_key           TEXT NOT NULL UNIQUE,   -- e.g. bill:<id>:2026-10-15:lead
    status               notify_status NOT NULL DEFAULT 'queued',
    provider_message_id  TEXT,
    error                TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at              TIMESTAMPTZ
);

-- ---------------------------------------------------------------------
-- Audit log — append-only, enforced in the database
-- ---------------------------------------------------------------------
CREATE TABLE audit_log (
    id             BIGSERIAL PRIMARY KEY,
    society_id     UUID REFERENCES societies(id) ON DELETE SET NULL,
    actor_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
    action         TEXT NOT NULL,
    entity         TEXT NOT NULL,
    entity_id      TEXT,
    detail         JSONB,
    ip             INET,
    at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_society_time ON audit_log (society_id, at DESC);

CREATE FUNCTION audit_log_is_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only (% not allowed)', TG_OP;
END $$;

CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();

-- ---------------------------------------------------------------------
-- Useful views
-- ---------------------------------------------------------------------
-- Society fund = opening balance + maintenance received − payments made
CREATE VIEW society_fund AS
SELECT s.id AS society_id,
       s.opening_balance
       + COALESCE((SELECT sum(r.amount) FROM maintenance_receipts r
                   JOIN maintenance_bills b ON b.id = r.bill_id
                   WHERE b.society_id = s.id), 0)
       - COALESCE((SELECT sum(p.amount) FROM payment_requests p
                   WHERE p.society_id = s.id AND p.status = 'paid'), 0) AS balance
FROM societies s;

-- Outstanding maintenance per flat
CREATE VIEW flat_dues AS
SELECT b.society_id, b.flat_id, count(*) AS unpaid_months, sum(b.amount) AS amount_due,
       min(b.due_date) AS oldest_due
FROM maintenance_bills b
WHERE b.status = 'unpaid'
GROUP BY b.society_id, b.flat_id;
