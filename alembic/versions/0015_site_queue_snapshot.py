"""site_queue_snapshot: each queue's last known length (Issue 36)

One new table; nothing existing changes, so the release before this one runs unaffected.

A row per queue: its length and average wait (both nullable, "not measured" until Issues 39 and 42)
and when they were taken. Redis serves most reads (``src/modules/queue/snapshot.py``); this table is
what a cold or flushed cache falls back to, what the write-through path updates when a queue changes,
and what the reconciliation sweep repairs. ``queue_id`` is the key because there is exactly one
current snapshot per queue; ``site_id`` is indexed because discovery reads a page of clinics' queues
at once. Both cascade: a snapshot of a deleted queue means nothing.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create ``site_queue_snapshot`` and its site index."""
    op.create_table(
        "site_queue_snapshot",
        sa.Column("queue_id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("waiting", sa.Integer(), nullable=True),
        sa.Column("average_wait_minutes", sa.Integer(), nullable=True),
        # Business time, Africa/Johannesburg: when the snapshot was taken.
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_site_queue_snapshot_queue_id_queue"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_site_queue_snapshot_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("queue_id", name=op.f("pk_site_queue_snapshot")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_site_queue_snapshot_site_id",
        "site_queue_snapshot",
        ["site_id"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the table. Reversible: discovery then reads the queue lengths directly again."""
    op.drop_index(
        "ix_clinicq_site_queue_snapshot_site_id",
        table_name="site_queue_snapshot",
        schema=SCHEMA,
    )
    op.drop_table("site_queue_snapshot", schema=SCHEMA)
