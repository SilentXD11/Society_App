from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SR_", extra="ignore")

    env: str = "dev"  # dev | test | prod
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/society_register"

    # Secrets — must be long random strings in production.
    jwt_secret: str = Field(default="dev-only-change-me-dev-only-change-me", min_length=32)
    otp_secret: str = Field(default="dev-only-otp-pepper-change-me-0000000", min_length=32)

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
