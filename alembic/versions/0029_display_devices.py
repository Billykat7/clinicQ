"""display_device: kiosk boxes, their pairing, and when each was last heard from (Issue 61)

One new table; nothing existing changes, so the release before this one runs unaffected.

A row per box. It is created unpaired (no ``site_id``) when a box first opens the start address, with
the SHA-256 of its secret (``token_hash``) and of the pairing code on its screen. A clinic manager's
pairing fills ``site_id``, ``paired_at`` and ``paired_by`` and clears the code; revoking sets
``revoked_at``. ``last_seen_at`` is the minute heartbeat, and ``silent_alerted_at`` keeps a silent box
to one alert per silence.

Deleting a clinic deletes its boxes (``CASCADE``): a box shows one clinic's board and nothing else.

Business time: every datetime column is Africa/Johannesburg.

Revision ID: 0029
Revises: 0028
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.commons.enums import DbSchema

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create ``display_device`` with its two unique hashes and the clinic's index."""
    op.create_table(
        "display_device",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=True),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column("queue_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("pairing_code_hash", sa.String(length=64), nullable=True),
        sa.Column("pairing_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paired_by", sa.String(length=36), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=36), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("app_version", sa.String(length=40), nullable=True),
        sa.Column("user_agent", sa.String(length=200), nullable=True),
        sa.Column("silent_alerted_at", sa.DateTime(timezone=True), nullable=True),
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
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_display_device_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_display_device")),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_display_device_token_hash",
        "display_device",
        ["token_hash"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_display_device_pairing_code_hash",
        "display_device",
        ["pairing_code_hash"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_display_device_site",
        "display_device",
        ["site_id", "paired_at"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the table. Every paired box must be paired again after an upgrade."""
    op.drop_index(
        "ix_clinicq_display_device_site", table_name="display_device", schema=SCHEMA
    )
    op.drop_index(
        "uq_display_device_pairing_code_hash",
        table_name="display_device",
        schema=SCHEMA,
    )
    op.drop_index(
        "uq_display_device_token_hash", table_name="display_device", schema=SCHEMA
    )
    op.drop_table("display_device", schema=SCHEMA)
