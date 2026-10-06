import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


Amount = Decimal  # serialised as string to keep paise exact

# ---------------------------------------------------------------- auth
class OtpRequest(BaseModel):
    destination: str = Field(min_length=5, max_length=254, description="Phone (+91…) or email")


class OtpVerify(OtpRequest):
    code: str = Field(pattern=r"^\d{6}$")


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: str


class StepUpVerify(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class StepUpToken(BaseModel):
    step_up_token: str
    expires_in_seconds: int


class MembershipOut(ORM):
    society_id: uuid.UUID
    society_name: str
    role: str
    title: str | None
    is_signatory: bool


class MeOut(BaseModel):
    id: uuid.UUID
    full_name: str
    phone: str | None
    email: str | None
    memberships: list[MembershipOut]


# ---------------------------------------------------------------- society & members
class SocietyOut(ORM):
    id: uuid.UUID
    name: str
    reg_no: str | None
    address: str | None
    currency: str
    timezone: str
    due_day: int
    monthly_charge: Amount
    opening_balance: Amount
    pay_info: str | None


class SocietyUpdate(BaseModel):
    name: str | None = None
    reg_no: str | None = None
    address: str | None = None
    due_day: int | None = Field(default=None, ge=1, le=28)
    monthly_charge: Amount | None = Field(default=None, ge=0)
    opening_balance: Amount | None = None
    pay_info: str | None = None


class MemberIn(BaseModel):
    full_name: str = Field(min_length=1)
    phone: str | None = None
    email: str | None = None
    role: Literal["committee", "owner", "tenant", "viewer"]
    title: str | None = None
    is_signatory: bool = False

    @model_validator(mode="after")
    def _check(self):
        if not (self.phone or self.email):
            raise ValueError("Give a phone number or an email address.")
        if self.is_signatory and self.role != "committee":
            raise ValueError("Only committee members can be bank signatories.")
        return self


class MemberOut(BaseModel):
    user_id: uuid.UUID
    full_name: str
    phone: str | None
    email: str | None
    role: str
    title: str | None
    is_signatory: bool
    active: bool


# ---------------------------------------------------------------- flats
class FlatIn(BaseModel):
    wing: str = ""
    number: str = Field(min_length=1)
    area_sqft: Decimal | None = None
    parking: str | None = None
    occupancy: Literal["owner", "tenant", "vacant"] = "owner"
    monthly_charge: Amount | None = Field(default=None, ge=0)
    notes: str | None = None


class FlatOut(ORM, FlatIn):
    id: uuid.UUID
    label: str


class ResidentIn(BaseModel):
    kind: Literal["owner", "tenant"]
    full_name: str
    phone: str | None = None
    email: str | None = None
    moved_in: date | None = None
    link_login: bool = Field(default=True, description="Create/attach a login so they can see their dues")


class ResidentOut(ORM):
    id: uuid.UUID
    flat_id: uuid.UUID
    kind: str
    full_name: str
    phone: str | None
    email: str | None
    moved_in: date | None
    moved_out: date | None
    user_id: uuid.UUID | None


# ---------------------------------------------------------------- maintenance
class GenerateBillsIn(BaseModel):
    period: date = Field(description="Any date in the month to bill")


class MaintenanceBillOut(ORM):
    id: uuid.UUID
    flat_id: uuid.UUID
    period: date
    amount: Amount
    due_date: date
    status: str
    note: str | None
    paid: Amount = Decimal(0)


class ReceiptIn(BaseModel):
    amount: Amount = Field(gt=0)
    paid_on: date
    mode: Literal["upi", "bank_transfer", "cash", "cheque", "card"]
    reference: str | None = None


# ---------------------------------------------------------------- vendors & staff
class VendorIn(BaseModel):
    name: str
    service: str | None = None
    contact: str | None = None
    phone: str | None = None
    email: str | None = None
    gstin: str | None = Field(default=None, pattern=r"^[0-9]{2}[A-Z0-9]{13}$")
    address: str | None = None


class VendorOut(ORM, VendorIn):
    id: uuid.UUID
    active: bool


class StaffIn(BaseModel):
    role: Literal["security", "sweeper", "other"]
    full_name: str
    phone: str | None = None
    post: str | None = None
    shift: str | None = None
    agency_id: uuid.UUID | None = None
    monthly_pay: Amount = Field(default=Decimal(0), ge=0)
    joined_on: date | None = None
    left_on: date | None = None
    id_proof_note: str | None = None


class StaffOut(ORM, StaffIn):
    id: uuid.UUID


# ---------------------------------------------------------------- recurring bills
Frequency = Literal["monthly", "quarterly", "half_yearly", "yearly", "one_time"]


class BillIn(BaseModel):
    name: str
    category: str
    vendor_id: uuid.UUID | None = None
    payee: str | None = None
    amount_source: Literal["fixed", "security_pay", "sweeper_pay", "all_staff_pay"] = "fixed"
    amount: Amount | None = Field(default=None, ge=0)
    frequency: Frequency = "monthly"
    next_due: date
    lead_days: int = Field(default=7, ge=0, le=90)
    autopay: bool = False
    lender: str | None = None
    total_instalments: int | None = Field(default=None, gt=0)
    paid_instalments: int = Field(default=0, ge=0)
    notes: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.amount_source == "fixed" and self.amount is None:
            raise ValueError("Enter the amount, or choose a staff-pay amount source.")
        if not (self.vendor_id or self.payee):
            raise ValueError("Choose a vendor or enter the payee.")
        return self


class BillOut(ORM):
    id: uuid.UUID
    name: str
    category: str
    vendor_id: uuid.UUID | None
    payee: str | None
    amount_source: str
    amount: Amount | None
    frequency: str
    next_due: date | None
    lead_days: int
    autopay: bool
    lender: str | None
    total_instalments: int | None
    paid_instalments: int
    active: bool
    current_amount: Amount = Decimal(0)


class RaisePaymentIn(BaseModel):
    mode: Literal["upi", "bank_transfer", "cash", "cheque", "card"] = "bank_transfer"
    cheque_no: str | None = None
    amount: Amount | None = Field(default=None, gt=0, description="Defaults to the bill's amount")


class AutoDebitIn(BaseModel):
    debited_on: date
    amount: Amount | None = Field(default=None, gt=0)
    reference: str | None = None


# ---------------------------------------------------------------- payments
class PaymentIn(BaseModel):
    vendor_id: uuid.UUID | None = None
    payee: str | None = None
    purpose: str
    category: str
    amount: Amount = Field(gt=0)
    mode: Literal["upi", "bank_transfer", "cash", "cheque", "card"]
    cheque_no: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if not (self.vendor_id or self.payee):
            raise ValueError("Choose a vendor or enter the payee.")
        return self


class ApprovalOut(ORM):
    user_id: uuid.UUID
    method: str
    approved_at: datetime


class PaymentOut(ORM):
    id: uuid.UUID
    recurring_bill_id: uuid.UUID | None
    bill_due_date: date | None
    vendor_id: uuid.UUID | None
    payee: str
    purpose: str
    category: str
    amount: Amount
    mode: str
    cheque_no: str | None
    status: str
    requested_by: uuid.UUID
    requested_at: datetime
    reject_reason: str | None
    paid_on: date | None
    reference: str | None
    approvals: list[ApprovalOut] = []


class RejectIn(BaseModel):
    reason: str = Field(min_length=3)


class MarkPaidIn(BaseModel):
    paid_on: date
    reference: str | None = None


# ---------------------------------------------------------------- fixed deposits
class FdIn(BaseModel):
    bank: str
    branch: str | None = None
    fd_number: str | None = Field(default=None, description="Only the last 4 digits are kept")
    principal: Amount = Field(gt=0)
    rate_pct: Decimal | None = Field(default=None, ge=0, lt=50)
    payout: Literal["cumulative", "monthly", "quarterly"] = "cumulative"
    start_date: date
    maturity_date: date
    maturity_amount: Amount | None = Field(default=None, gt=0, description="Estimated if left out")
    auto_renew: bool = False
    holder: str | None = None
    nominee: str | None = None
    notes: str | None = None

    @field_validator("fd_number")
    @classmethod
    def _last4(cls, v):
        if v is None:
            return v
        digits = "".join(c for c in v if c.isdigit())
        if len(digits) < 4:
            raise ValueError("FD number needs at least 4 digits.")
        return digits[-4:]

    @model_validator(mode="after")
    def _dates(self):
        if self.maturity_date <= self.start_date:
            raise ValueError("Maturity date must be after the start date.")
        return self


class FdOut(ORM):
    id: uuid.UUID
    bank: str
    branch: str | None
    fd_last4: str | None
    principal: Amount
    rate_pct: Decimal | None
    payout: str
    start_date: date
    maturity_date: date
    maturity_amount: Amount | None
    auto_renew: bool
    holder: str | None
    nominee: str | None
    status: str
    renewed_from_id: uuid.UUID | None
    closed_on: date | None
    closed_amount: Amount | None
    days_to_maturity: int | None = None


class FdSummary(BaseModel):
    active_count: int
    total_principal: Amount
    total_maturity_value: Amount
    yearly_interest_estimate: Amount
    maturing_30_days: int
    maturing_90_days: int
    next_maturity: date | None


class FdRenewIn(BaseModel):
    rate_pct: Decimal = Field(ge=0, lt=50)
    months: int = Field(gt=0, le=120)
    principal: Amount | None = Field(default=None, gt=0, description="Defaults to the maturity amount for cumulative FDs")
    payout: Literal["cumulative", "monthly", "quarterly"] | None = None
    auto_renew: bool | None = None


class FdCloseIn(BaseModel):
    closed_on: date
    amount_credited: Amount = Field(gt=0)


# ---------------------------------------------------------------- calendar
class UpcomingItem(BaseModel):
    due: date
    kind: str
    title: str
    amount: Amount
    direction: str
    ref_id: uuid.UUID | None
    note: str
    autopay: bool
    days_until: int


class UpcomingOut(BaseModel):
    today: date
    days: int
    to_pay_total: Amount
    coming_in_total: Amount
    overdue_total: Amount
    items: list[UpcomingItem]


class AuditOut(ORM):
    id: int
    actor_user_id: uuid.UUID | None
    action: str
    entity: str
    entity_id: str | None
    detail: dict | None
    at: datetime
