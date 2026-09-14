"""wait_time_sample, and a queue snapshot that holds a wait range, not an average (Issue 42)

One new table and one reshaped one.

**``wait_time_sample``**: one row per completed visit, written in the transaction that marks the
ticket ``done``: the visit's wait, service and call-interval minutes, the Johannesburg hour it was
called, and when it was recorded. ``ticket_id`` is unique (a visit is sampled once) and every key
cascades, because a sample means nothing without its queue and ticket. The index on
``(queue_id, recorded_at)`` is the estimator's read: a queue's most recent N visits.

**``site_queue_snapshot``** loses ``average_wait_minutes`` and gains ``wait_low_minutes``,
``wait_high_minutes``, ``wait_confidence`` and ``wait_approximate``. The column was never written
with anything but ``NULL`` (the estimator did not exist), so no data is lost, and a single "average
wait" is exactly the number non-negotiable 5's sibling rule, "a range, never a single number",
exists to keep off every surface. The snapshot is a cache the reconciliation sweep rebuilds within a
minute, so the new columns start ``NULL`` and fill themselves.

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create ``wait_time_sample``; swap the snapshot's average for a range."""
    op.create_table(
        "wait_time_sample",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("queue_id", sa.String(length=36), nullable=False),
        sa.Column("ticket_id", sa.String(length=36), nullable=False),
        # The Johannesburg calendar date of the visit, and the Johannesburg hour it was called.
        sa.Column("service_day", sa.Date(), nullable=False),
        sa.Column("called_hour", sa.Integer(), nullable=False),
        sa.Column("wait_minutes", sa.Float(), nullable=False),
        sa.Column("service_minutes", sa.Float(), nullable=True),
        sa.Column("interval_minutes", sa.Float(), nullable=True),
        # Business time, Africa/Johannesburg: when the visit finished.
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_wait_time_sample_queue_id_queue"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_wait_time_sample_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            [f"{SCHEMA}.ticket.id"],
            name=op.f("fk_wait_time_sample_ticket_id_ticket"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wait_time_sample")),
        sa.UniqueConstraint("ticket_id", name=op.f("uq_wait_time_sample_ticket_id")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_wait_time_sample_queue_recorded",
        "wait_time_sample",
        ["queue_id", "recorded_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_wait_time_sample_site_day",
        "wait_time_sample",
        ["site_id", "service_day"],
        unique=False,
        schema=SCHEMA,
    )
    op.drop_column("site_queue_snapshot", "average_wait_minutes", schema=SCHEMA)
    op.add_column(
        "site_queue_snapshot",
        sa.Column("wait_low_minutes", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "site_queue_snapshot",
        sa.Column("wait_high_minutes", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "site_queue_snapshot",
        sa.Column("wait_confidence", sa.String(length=8), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "site_queue_snapshot",
        sa.Column("wait_approximate", sa.Boolean(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Reverse both. The samples are derived data; the snapshot's range becomes an empty average."""
    for column in (
        "wait_approximate",
        "wait_confidence",
        "wait_high_minutes",
        "wait_low_minutes",
    ):
        op.drop_column("site_queue_snapshot", column, schema=SCHEMA)
    op.add_column(
        "site_queue_snapshot",
        sa.Column("average_wait_minutes", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_clinicq_wait_time_sample_site_day",
        table_name="wait_time_sample",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_clinicq_wait_time_sample_queue_recorded",
        table_name="wait_time_sample",
        schema=SCHEMA,
    )
    op.drop_table("wait_time_sample", schema=SCHEMA)
