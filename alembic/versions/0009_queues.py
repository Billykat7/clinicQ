"""queue: a clinic's named lines, which is what a patient actually joins (Issue 25)

One new table; nothing existing changes, so the release before this one runs unaffected.

Two constraints are the point of the migration rather than incidental to it:

* ``(site_id, slug)`` and ``(site_id, name)`` are unique **per site**, not platform-wide. Every
  clinic has a queue called Triage, and they are different queues;
* the composite index ``(site_id, is_active, display_order)`` is what the board and the four channel
  menus read on every refresh: this clinic's live queues, in the clinic's own order.

``ondelete="CASCADE"`` on ``site_id`` matches the rest of M4: a clinic is soft-deleted in practice
(``site.is_deleted``), so the cascade only ever fires if a row is genuinely removed.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema, QueueKind

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create ``queue``, its two per-site unique constraints and the board's index."""
    op.create_table(
        "queue",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=16),
            nullable=False,
            server_default=QueueKind.OTHER.value,
        ),
        sa.Column("room_label", sa.String(length=60), nullable=True),
        sa.Column(
            "ticket_prefix", sa.String(length=4), nullable=False, server_default="A"
        ),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "expected_service_minutes",
            sa.Integer(),
            nullable=False,
            server_default="10",
        ),
        sa.Column("max_daily_capacity", sa.Integer(), nullable=True),
        # Enforced on the server (src.modules.queues.service.ensure_remote_join_allowed), never by
        # hiding a button: a USSD session does not have buttons.
        sa.Column(
            "allows_remote_join", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
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
            name=op.f("fk_queue_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_queue")),
        sa.UniqueConstraint("site_id", "slug", name="uq_queue_site_id_slug"),
        sa.UniqueConstraint("site_id", "name", name="uq_queue_site_id_name"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_queue_site_active_order",
        "queue",
        ["site_id", "is_active", "display_order"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the table and its index. Reversible: tickets arrive with Issue 39."""
    op.drop_index(
        "ix_clinicq_queue_site_active_order", table_name="queue", schema=SCHEMA
    )
    op.drop_table("queue", schema=SCHEMA)
