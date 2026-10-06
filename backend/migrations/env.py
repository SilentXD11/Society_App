"""Alembic environment. Uses SR_DATABASE_URL (sync driver for migrations)."""
from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings


def sync_url() -> str:
    return get_settings().database_url.replace("+asyncpg", "+psycopg")


def run_migrations_online() -> None:
    engine = create_engine(sync_url())
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(url=sync_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
