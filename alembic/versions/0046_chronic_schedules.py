"""chronic schedules: a repeating medication collection per patient (Issue 85)

* ``chronic_schedule``: the clinic, the collection queue, the patient, the interval and the next due day,
  the grace period, what has been reminded and followed up for (so each goes once per cycle), the join
  token a reminder carries, and when it was stopped.
* One active schedule per patient and queue (``uq_chronic_schedule_patient_queue``), an unguessable join
  token (``uq_chronic_schedule_join_token``), and indexes for the sweep and the clinic's own list.

Nothing existing changes: no schedule exists until a clinic makes one.

Revision ID: 0046
Revises: 0045
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every object this project owns is schema-qualified; nothing goes in ``public``.
SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create the repeating collections table."""
    op.create_table(
        "chronic_schedule",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("queue_id", sa.String(length=36), nullable=False),
        sa.Column("patient_id", sa.String(length=36), nullable=False),
        sa.Column("service", sa.String(length=60), nullable=True),
        sa.Column("interval_days", sa.Integer(), nullable=False),
        sa.Column("next_due_on", sa.Date(), nullable=False),
        sa.Column("grace_days", sa.Integer(), nullable=False),
        sa.Column("last_collected_on", sa.Date(), nullable=True),
        sa.Column("reminded_for", sa.Date(), nullable=True),
        sa.Column("followed_up_for", sa.Date(), nullable=True),
        sa.Column("join_token", sa.String(length=64), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_by", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
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
            name=op.f("fk_chronic_schedule_patient_id_patient"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_chronic_schedule_queue_id_queue"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_chronic_schedule_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chronic_schedule")),
        sa.UniqueConstraint(
            "patient_id", "queue_id", name="uq_chronic_schedule_patient_queue"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_chronic_schedule_due",
        "chronic_schedule",
        ["next_due_on", "stopped_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_chronic_schedule_site",
        "chronic_schedule",
        ["site_id", "stopped_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_chronic_schedule_join_token",
        "chronic_schedule",
        ["join_token"],
        unique=True,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop it. Nothing else refers to it, and no other table changes."""
    op.drop_index(
        "uq_chronic_schedule_join_token", table_name="chronic_schedule", schema=SCHEMA
    )
    op.drop_index(
        "ix_clinicq_chronic_schedule_site", table_name="chronic_schedule", schema=SCHEMA
    )
    op.drop_index(
        "ix_clinicq_chronic_schedule_due", table_name="chronic_schedule", schema=SCHEMA
    )
    op.drop_table("chronic_schedule", schema=SCHEMA)
