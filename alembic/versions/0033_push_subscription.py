"""push_subscription: where a web push reaches a patient's browser (Issue 64)

One new table; nothing existing changes, so the release before this one runs unaffected.

A row per browser endpoint, unique by the endpoint's SHA-256 (``endpoint_hash``): an endpoint URL can be
longer than an index entry allows. The keys the payload is encrypted to (``p256dh``, ``auth``) are the
browser's public material, stored as it gave them. A patient's rows go with the patient (``CASCADE``).
Business time: every datetime column is Africa/Johannesburg.

Revision ID: 0033
Revises: 0032
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create ``push_subscription`` with its unique endpoint hash and the patient's index."""
    op.create_table(
        "push_subscription",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("patient_id", sa.String(length=36), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("endpoint_hash", sa.String(length=64), nullable=False),
        sa.Column("p256dh", sa.String(length=128), nullable=False),
        sa.Column("auth", sa.String(length=48), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=200), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["patient_id"],
            [f"{SCHEMA}.patient.id"],
            name=op.f("fk_push_subscription_patient_id_patient"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_subscription")),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_push_subscription_endpoint_hash",
        "push_subscription",
        ["endpoint_hash"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_push_subscription_patient",
        "push_subscription",
        ["patient_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the table. Every browser must subscribe again after an upgrade."""
    op.drop_index(
        "ix_clinicq_push_subscription_patient",
        table_name="push_subscription",
        schema=SCHEMA,
    )
    op.drop_index(
        "uq_push_subscription_endpoint_hash",
        table_name="push_subscription",
        schema=SCHEMA,
    )
    op.drop_table("push_subscription", schema=SCHEMA)
