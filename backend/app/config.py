from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-change-me-dev-only-change-me"
DEV_OTP_SECRET = "dev-only-otp-pepper-change-me-0000000"


def _with_driver(url: str, driver: str) -> str:
    """Accept the plain URL Neon/Render/Supabase give you (postgres:// or postgresql://,
    with ?sslmode=require&channel_binding=require) and adapt it for a specific driver."""
    parts = urlsplit(url.strip())
    if not parts.scheme.startswith("postgres"):
        raise ValueError("SR_DATABASE_URL must be a PostgreSQL URL")
    query = dict(parse_qsl(parts.query))
    ssl = query.pop("sslmode", None) or query.pop("ssl", None)
    if driver == "asyncpg":
        query.pop("channel_binding", None)  # asyncpg doesn't accept it
        if ssl and ssl != "disable":
            query["ssl"] = ssl
    elif ssl:  # psycopg / libpq
        query["sslmode"] = ssl
    return urlunsplit((f"postgresql+{driver}", parts.netloc, parts.path, urlencode(query), parts.fragment))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SR_", extra="ignore")

    env: str = "dev"  # dev | test | prod
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/society_register"

    # Secrets — must be long random strings in production.
    jwt_secret: str = Field(default=DEV_JWT_SECRET, min_length=32)
    otp_secret: str = Field(default=DEV_OTP_SECRET, min_length=32)
    # Lets POST /setup/society create a society and its first committee member.
    # Leave empty to switch that endpoint off.
    setup_token: str = ""

    access_token_minutes: int = 15
    refresh_token_days: int = 30
    step_up_minutes: int = 5  # how long a step-up (OTP/passkey) confirmation lasts

    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5
    otp_max_sends_per_window: int = 3
    otp_send_window_minutes: int = 10

    # Payments: the person who raises a payment may not also approve it.
    maker_checker: bool = True
    approvals_required: int = 2

    # Notifications
    sms_provider: str = "console"  # console | msg91
    email_provider: str = "console"  # console | ses
    msg91_auth_key: str = ""
    msg91_sender_id: str = ""
    msg91_flow_id: str = ""
    ses_from_address: str = ""
    aws_region: str = "ap-south-1"

    # File storage
    s3_bucket: str = ""

    cors_origins: list[str] = ["http://localhost:5173"]
    enable_docs: bool = True  # interactive API docs at /docs

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, v: str) -> str:
        return _with_driver(v, "asyncpg")

    @model_validator(mode="after")
    def _real_secrets_in_prod(self):
        if self.env == "prod" and (self.jwt_secret == DEV_JWT_SECRET or self.otp_secret == DEV_OTP_SECRET):
            raise ValueError("Set SR_JWT_SECRET and SR_OTP_SECRET to long random values in production.")
        return self

    @property
    def sync_database_url(self) -> str:
        """For Alembic (psycopg)."""
        return _with_driver(self.database_url, "psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()
