"""patient: phone-first patient records, with no password anywhere (Issue 17)

A new table; nothing existing changes, so the release before this one runs on it unaffected. The
unique constraint on ``phone_e164`` is what makes one number one patient even when two sign-ins
race (the service catches the losing insert and reads the winner).

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create ``patient``."""
    op.create_table(
        "patient",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("phone_e164", sa.String(length=16), nullable=False),
        sa.Column("whatsapp_id", sa.String(length=32), nullable=True),
        sa.Column("display_name", sa.String(length=80), nullable=True),
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_channel", sa.String(length=16), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "session_version", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "modified_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patient")),
        sa.UniqueConstraint("phone_e164", name=op.f("uq_patient_phone_e164")),
        sa.UniqueConstraint("whatsapp_id", name=op.f("uq_patient_whatsapp_id")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop ``patient``."""
    op.drop_table("patient", schema=SCHEMA)
