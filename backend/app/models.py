"""SQLAlchemy models for the tables the API currently uses.

db/schema.sql is the source of truth (applied by the Alembic migration);
these classes map onto it. Tables that exist in the schema but have no
model yet (documents, AMC, e-sign, handover…) are listed in the README.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Integer, Numeric, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import ENUM, INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import DateTime


def pg_enum(name: str, *values: str) -> ENUM:
    return ENUM(*values, name=name, create_type=False)


MemberRole = pg_enum("member_role", "committee", "owner", "tenant", "viewer")
OtpChannel = pg_enum("otp_channel", "sms", "email")
OtpPurpose = pg_enum("otp_purpose", "login", "step_up")
Occupancy = pg_enum("occupancy_type", "owner", "tenant", "vacant")
ResidentKind = pg_enum("resident_kind", "owner", "tenant")
BillStatus = pg_enum("bill_status", "unpaid", "paid", "waived")
PayMode = pg_enum("pay_mode", "upi", "bank_transfer", "cash", "cheque", "card", "auto_debit")
Frequency = pg_enum("frequency_type", "monthly", "quarterly", "half_yearly", "yearly", "one_time")
AmountSource = pg_enum("amount_source", "fixed", "security_pay", "sweeper_pay", "all_staff_pay")
PaymentStatus = pg_enum("payment_status", "pending_approval", "approved", "rejected", "paid")
ApprovalMethod = pg_enum("approval_method", "passkey", "otp")
FdPayout = pg_enum("fd_payout", "cumulative", "monthly", "quarterly")
FdStatus = pg_enum("fd_status", "active", "matured", "renewed", "closed")
StaffRole = pg_enum("staff_role", "security", "sweeper", "other")
NotifyStatus = pg_enum("notify_status", "queued", "sent", "failed", "skipped")

Money = Numeric(14, 2)
TS = DateTime(timezone=True)


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())


def fk(target: str, **kw) -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target), **kw)


class Base(DeclarativeBase):
    pass


class Society(Base):
    __tablename__ = "societies"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(Text)
    reg_no: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3), server_default="INR")
    timezone: Mapped[str] = mapped_column(Text, server_default="Asia/Kolkata")
    due_day: Mapped[int] = mapped_column(SmallInteger, server_default="10")
    monthly_charge: Mapped[Decimal] = mapped_column(Money, server_default="0")
    opening_balance: Mapped[Decimal] = mapped_column(Money, server_default="0")
    pay_info: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    full_name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text, unique=True)
    email: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(TS)


class Membership(Base):
    __tablename__ = "memberships"
    id: Mapped[uuid.UUID] = pk()
    society_id: Mapped[uuid.UUID] = fk("societies.id")
    user_id: Mapped[uuid.UUID] = fk("users.id")
    role: Mapped[str] = mapped_column(MemberRole)
    title: Mapped[str | None] = mapped_column(Text)
    is_signatory: Mapped[bool] = mapped_column(Boolean, server_default="false")
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class OtpCode(Base):
    __tablename__ = "otp_codes"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID | None] = fk("users.id")
    destination: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(OtpChannel)
    purpose: Mapped[str] = mapped_column(OtpPurpose)
    code_hash: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(TS)
    consumed_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    request_ip: Mapped[str | None] = mapped_column(INET)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = fk("users.id")
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)


class Flat(Base):
    __tablename__ = "flats"
    id: Mapped[uuid.UUID] = pk()
    society_id: Mapped[uuid.UUID] = fk("societies.id")
    wing: Mapped[str] = mapped_column(Text, server_default="")
    number: Mapped[str] = mapped_column(Text)
    area_sqft: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    parking: Mapped[str | None] = mapped_column(Text)
    occupancy: Mapped[str] = mapped_column(Occupancy, server_default="owner")
    monthly_charge: Mapped[Decimal | None] = mapped_column(Money)
    notes: Mapped[str | None] = mapped_column(Text)

    @property
    def label(self) -> str:
        return f"{self.wing}-{self.number}" if self.wing else self.number


class Resident(Base):
    __tablename__ = "residents"
    id: Mapped[uuid.UUID] = pk()
    society_id: Mapped[uuid.UUID] = fk("societies.id")
    flat_id: Mapped[uuid.UUID] = fk("flats.id")
    user_id: Mapped[uuid.UUID | None] = fk("users.id")
    kind: Mapped[str] = mapped_column(ResidentKind)
    full_name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    moved_in: Mapped[date | None] = mapped_column(Date)
    moved_out: Mapped[date | None] = mapped_column(Date)


class MaintenanceBill(Base):
    __tablename__ = "maintenance_bills"
    id: Mapped[uuid.UUID] = pk()
    society_id: Mapped[uuid.UUID] = fk("societies.id")
    flat_id: Mapped[uuid.UUID] = fk("flats.id")
    period: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)
    due_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(BillStatus, server_default="unpaid")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class MaintenanceReceipt(Base):
    __tablename__ = "maintenance_receipts"
    id: Mapped[uuid.UUID] = pk()
    bill_id: Mapped[uuid.UUID] = fk("maintenance_bills.id")
    amount: Mapped[Decimal] = mapped_column(Money)
    paid_on: Mapped[date] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(PayMode)
    reference: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[uuid.UUID | None] = fk("users.id")
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class Vendor(Base):
    __tablename__ = "vendors"
    id: Mapped[uuid.UUID] = pk()
    society_id: Mapped[uuid.UUID] = fk("societies.id")
    name: Mapped[str] = mapped_column(Text)
    service: Mapped[str | None] = mapped_column(Text)
    contact: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    gstin: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")


class RecurringBill(Base):
    __tablename__ = "recurring_bills"
    id: Mapped[uuid.UUID] = pk()
    society_id: Mapped[uuid.UUID] = fk("societies.id")
    name: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    vendor_id: Mapped[uuid.UUID | None] = fk("vendors.id")
    payee: Mapped[str | None] = mapped_column(Text)
    amount_source: Mapped[str] = mapped_column(AmountSource, server_default="fixed")
    amount: Mapped[Decimal | None] = mapped_column(Money)
    frequency: Mapped[str] = mapped_column(Frequency, server_default="monthly")
    next_due: Mapped[date | None] = mapped_column(Date)
    anchor_day: Mapped[int | None] = mapped_column(SmallInteger)
    lead_days: Mapped[int] = mapped_column(SmallInteger, server_default="7")
    autopay: Mapped[bool] = mapped_column(Boolean, server_default="false")
    lender: Mapped[str | None] = mapped_column(Text)
    total_instalments: Mapped[int | None] = mapped_column(Integer)
    paid_instalments: Mapped[int] = mapped_column(Integer, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    notes: Mapped[str | None] = mapped_column(Text)


class PaymentRequest(Base):
    __tablename__ = "payment_requests"
    id: Mapped[uuid.UUID] = pk()
    society_id: Mapped[uuid.UUID] = fk("societies.id")
    recurring_bill_id: Mapped[uuid.UUID | None] = fk("recurring_bills.id")
    bill_due_date: Mapped[date | None] = mapped_column(Date)
    vendor_id: Mapped[uuid.UUID | None] = fk("vendors.id")
    payee: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Money)
    mode: Mapped[str] = mapped_column(PayMode)
    cheque_no: Mapped[str | None] = mapped_column(Text)
    invoice_doc_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(PaymentStatus, server_default="pending_approval")
    requested_by: Mapped[uuid.UUID] = fk("users.id")
    requested_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    reject_reason: Mapped[str | None] = mapped_column(Text)
    paid_on: Mapped[date | None] = mapped_column(Date)
    reference: Mapped[str | None] = mapped_column(Text)
    paid_by: Mapped[uuid.UUID | None] = fk("users.id")


class PaymentApproval(Base):
    __tablename__ = "payment_approvals"
    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("payment_requests.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    method: Mapped[str] = mapped_column(ApprovalMethod)
    approved_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class FixedDeposit(Base):
    __tablename__ = "fixed_deposits"
    id: Mapped[uuid.UUID] = pk()
    society_id: Mapped[uuid.UUID] = fk("societies.id")
    bank: Mapped[str] = mapped_column(Text)
    branch: Mapped[str | None] = mapped_column(Text)
    fd_last4: Mapped[str | None] = mapped_column(String(4))
    principal: Mapped[Decimal] = mapped_column(Money)
    rate_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    payout: Mapped[str] = mapped_column(FdPayout, server_default="cumulative")
    start_date: Mapped[date] = mapped_column(Date)
    maturity_date: Mapped[date] = mapped_column(Date)
    maturity_amount: Mapped[Decimal | None] = mapped_column(Money)
    auto_renew: Mapped[bool] = mapped_column(Boolean, server_default="false")
    holder: Mapped[str | None] = mapped_column(Text)
    nominee: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(FdStatus, server_default="active")
    renewed_from_id: Mapped[uuid.UUID | None] = fk("fixed_deposits.id")
    closed_on: Mapped[date | None] = mapped_column(Date)
    closed_amount: Mapped[Decimal | None] = mapped_column(Money)
    receipt_doc_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    notes: Mapped[str | None] = mapped_column(Text)


class Staff(Base):
    __tablename__ = "staff"
    id: Mapped[uuid.UUID] = pk()
    society_id: Mapped[uuid.UUID] = fk("societies.id")
    role: Mapped[str] = mapped_column(StaffRole)
    full_name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    post: Mapped[str | None] = mapped_column(Text)
    shift: Mapped[str | None] = mapped_column(Text)
    agency_id: Mapped[uuid.UUID | None] = fk("vendors.id")
    monthly_pay: Mapped[Decimal] = mapped_column(Money, server_default="0")
    joined_on: Mapped[date | None] = mapped_column(Date)
    left_on: Mapped[date | None] = mapped_column(Date)
    id_proof_note: Mapped[str | None] = mapped_column(Text)


class NotificationLog(Base):
    __tablename__ = "notification_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    society_id: Mapped[uuid.UUID | None] = fk("societies.id")
    user_id: Mapped[uuid.UUID | None] = fk("users.id")
    channel: Mapped[str] = mapped_column(Text)
    destination: Mapped[str] = mapped_column(Text)
    template: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(NotifyStatus, server_default="queued")
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(TS)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    society_id: Mapped[uuid.UUID | None] = fk("societies.id")
    actor_user_id: Mapped[uuid.UUID | None] = fk("users.id")
    action: Mapped[str] = mapped_column(Text)
    entity: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(INET)
    at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
