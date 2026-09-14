"""queue_reorder: the record of every clinical priority override (Issue 46)

One new table; nothing existing changes.

A row per override: the ticket and queue, the staff member (name, and id for search, cleared if the
account is removed), the reason code, an optional short note, and the ticket's place among the waiting
patients before and after. The audit row written alongside it is the proof; this row is the detail the
dashboard's reorder trail (Issue 52) and the override counts (Issue 90) read.

Two check constraints are the point: ``ck_queue_reorder_reason_code`` holds the ``PriorityReason``
vocabulary, frozen at this revision, so a reason is never free text; and
``ck_queue_reorder_moves_forward`` records that an override only ever moves a patient forward. The
server refuses an override without a reason before anything is written; the table could not store
one without a code anyway.

Business time: ``created_at`` is Africa/Johannesburg.

Revision ID: 0024
Revises: 0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value

#: The PriorityReason values at this revision, frozen.
_REASONS = (
    "elderly",
    "infant",
    "other",
    "pregnancy",
    "staff_referral",
    "visibly_unwell",
)


def upgrade() -> None:
    """Create ``queue_reorder`` with its constraints and indexes."""
    op.create_table(
        "queue_reorder",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("queue_id", sa.String(length=36), nullable=False),
        sa.Column("ticket_id", sa.String(length=36), nullable=False),
        sa.Column("staff_user_id", sa.String(length=36), nullable=True),
        sa.Column("staff", sa.String(length=255), nullable=False),
        sa.Column("reason_code", sa.String(length=24), nullable=False),
        sa.Column("note", sa.String(length=140), nullable=True),
        sa.Column("position_before", sa.Integer(), nullable=False),
        sa.Column("position_after", sa.Integer(), nullable=False),
        # Business time, Africa/Johannesburg.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "position_before >= 1 AND position_after >= 1 AND position_after < position_before",
            name=op.f("ck_queue_reorder_moves_forward"),
        ),
        sa.CheckConstraint(
            "reason_code IN (" + ", ".join(f"'{reason}'" for reason in _REASONS) + ")",
            name=op.f("ck_queue_reorder_reason_code"),
        ),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_queue_reorder_queue_id_queue"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_queue_reorder_site_id_site"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["staff_user_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_queue_reorder_staff_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            [f"{SCHEMA}.ticket.id"],
            name=op.f("fk_queue_reorder_ticket_id_ticket"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_queue_reorder")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_queue_reorder_site_created",
        "queue_reorder",
        ["site_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_queue_reorder_staff",
        "queue_reorder",
        ["site_id", "staff_user_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the table. The audit rows written with each override remain, as the proof."""
    op.drop_index(
        "ix_clinicq_queue_reorder_staff", table_name="queue_reorder", schema=SCHEMA
    )
    op.drop_index(
        "ix_clinicq_queue_reorder_site_created",
        table_name="queue_reorder",
        schema=SCHEMA,
    )
    op.drop_table("queue_reorder", schema=SCHEMA)
