"""Initial schema from db/schema.sql

Revision ID: 0001
Create Date: 2026-10-04
"""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = Path(__file__).resolve().parents[2] / "db" / "schema.sql"


def upgrade() -> None:
    # Run on the raw driver cursor with no parameters, so "::jsonb" casts and the "%"
    # in the audit trigger's message aren't treated as bind placeholders.
    with op.get_bind().connection.cursor() as cur:
        cur.execute(SCHEMA.read_text())


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
