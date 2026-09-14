"""queue_request_key: one staff action on the queue is done once, however often it is sent (Issue 50)

One new table; nothing existing changes.

A row per ``Idempotency-Key`` a staff member sent with *Call next*, a status change or an undone call:
who sent it, the clinic, the operation and what it was asked of, the ticket it moved, and when. The
unique ``(user_id, key)`` constraint is what makes a double tap call one patient: a second request with
the same key cannot insert its row until the first transaction ends, and then replays the first one's
answer. Rows are deleted with their user, clinic or ticket, and by the hourly sweep after a day.

Business time: ``created_at`` is Africa/Johannesburg.

Revision ID: 0027
Revises: 0026
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create ``queue_request_key`` with its unique key and the sweep's index."""
    op.create_table(
        "queue_request_key",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("target", sa.String(length=80), nullable=False),
        sa.Column("ticket_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_queue_request_key_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            [f"{SCHEMA}.ticket.id"],
            name=op.f("fk_queue_request_key_ticket_id_ticket"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_queue_request_key_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_queue_request_key")),
        sa.UniqueConstraint("user_id", "key", name="uq_queue_request_key_user_key"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_queue_request_key_created",
        "queue_request_key",
        ["created_at"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the table. Replays of requests sent before the downgrade are then real second requests."""
    op.drop_index(
        "ix_clinicq_queue_request_key_created",
        table_name="queue_request_key",
        schema=SCHEMA,
    )
    op.drop_table("queue_request_key", schema=SCHEMA)
