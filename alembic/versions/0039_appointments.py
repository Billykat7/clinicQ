"""appointments: bookable slots that share a queue's daily limit with walk-ins (Issue 80)

* ``appointment_policy``: a clinic's booking horizon and minimum lead time.
* ``appointment_template_window`` and ``appointment_day_override``: a queue's weekly windows and one
  date's replacement windows, which slots are generated from.
* ``appointment_slot``: one bookable time. ``ck_appointment_slot_not_overbooked`` refuses a
  ``booked_count`` above ``capacity``, and ``uq_appointment_slot_queue_start`` keeps generation
  idempotent.
* ``appointment_block``: a range a manager took out of the book (a staff absence).
* ``appointment``: one place held by one patient.
* ``queue_capacity_day``: the row walk-in joins and bookings both lock before counting a queue's day.

Nothing existing changes; the release before this one runs unaffected (it never reads these tables).

Revision ID: 0039
Revises: 0038
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every object this project owns is schema-qualified; nothing goes in ``public``.
SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create the appointment tables."""
    op.create_table(
        "appointment_policy",
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("min_lead_minutes", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "horizon_days BETWEEN 1 AND 365",
            name=op.f("ck_appointment_policy_horizon_days_range"),
        ),
        sa.CheckConstraint(
            "min_lead_minutes BETWEEN 0 AND 10080",
            name=op.f("ck_appointment_policy_min_lead_minutes_range"),
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_appointment_policy_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("site_id", name=op.f("pk_appointment_policy")),
        schema=SCHEMA,
    )
    op.create_table(
        "appointment_block",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("queue_id", sa.String(length=36), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("lifted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "ends_at > starts_at", name=op.f("ck_appointment_block_ends_after_start")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_appointment_block_created_by_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_appointment_block_queue_id_queue"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_appointment_block_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointment_block")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_appointment_block_site_starts",
        "appointment_block",
        ["site_id", "starts_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "appointment_day_override",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("queue_id", sa.String(length=36), nullable=False),
        sa.Column("service_id", sa.String(length=36), nullable=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=True),
        sa.Column("ends_at", sa.Time(), nullable=True),
        sa.Column("slot_minutes", sa.Integer(), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=True),
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
        sa.CheckConstraint(
            "(starts_at IS NULL) = (ends_at IS NULL) AND (starts_at IS NULL) = (slot_minutes IS NULL) AND (starts_at IS NULL) = (capacity IS NULL)",
            name=op.f("ck_appointment_day_override_window_complete"),
        ),
        sa.CheckConstraint(
            "capacity IS NULL OR capacity BETWEEN 1 AND 50",
            name=op.f("ck_appointment_day_override_capacity_range"),
        ),
        sa.CheckConstraint(
            "slot_minutes IS NULL OR slot_minutes BETWEEN 5 AND 240",
            name=op.f("ck_appointment_day_override_slot_minutes_range"),
        ),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_appointment_day_override_queue_id_queue"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            [f"{SCHEMA}.clinic_service.id"],
            name=op.f("fk_appointment_day_override_service_id_clinic_service"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_appointment_day_override_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointment_day_override")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_appointment_day_override_queue_day",
        "appointment_day_override",
        ["queue_id", "day"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "appointment_slot",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("queue_id", sa.String(length=36), nullable=False),
        sa.Column("service_id", sa.String(length=36), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_day", sa.Date(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("booked_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "booked_count <= capacity", name=op.f("ck_appointment_slot_not_overbooked")
        ),
        sa.CheckConstraint(
            "booked_count >= 0",
            name=op.f("ck_appointment_slot_booked_count_not_negative"),
        ),
        sa.CheckConstraint(
            "capacity BETWEEN 1 AND 50", name=op.f("ck_appointment_slot_capacity_range")
        ),
        sa.CheckConstraint(
            "ends_at > starts_at", name=op.f("ck_appointment_slot_ends_after_start")
        ),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_appointment_slot_queue_id_queue"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            [f"{SCHEMA}.clinic_service.id"],
            name=op.f("fk_appointment_slot_service_id_clinic_service"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_appointment_slot_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointment_slot")),
        sa.UniqueConstraint(
            "queue_id", "starts_at", name="uq_appointment_slot_queue_start"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_appointment_slot_queue_day",
        "appointment_slot",
        ["queue_id", "service_day"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_appointment_slot_site_day",
        "appointment_slot",
        ["site_id", "service_day"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "appointment_template_window",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("queue_id", sa.String(length=36), nullable=False),
        sa.Column("service_id", sa.String(length=36), nullable=True),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=False),
        sa.Column("ends_at", sa.Time(), nullable=False),
        sa.Column("slot_minutes", sa.Integer(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "capacity BETWEEN 1 AND 50",
            name=op.f("ck_appointment_template_window_capacity_range"),
        ),
        sa.CheckConstraint(
            "slot_minutes BETWEEN 5 AND 240",
            name=op.f("ck_appointment_template_window_slot_minutes_range"),
        ),
        sa.CheckConstraint(
            "weekday BETWEEN 0 AND 6",
            name=op.f("ck_appointment_template_window_weekday_range"),
        ),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_appointment_template_window_queue_id_queue"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            [f"{SCHEMA}.clinic_service.id"],
            name=op.f("fk_appointment_template_window_service_id_clinic_service"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_appointment_template_window_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointment_template_window")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_appointment_template_window_queue",
        "appointment_template_window",
        ["queue_id", "weekday"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "queue_capacity_day",
        sa.Column("queue_id", sa.String(length=36), nullable=False),
        sa.Column("service_day", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_queue_capacity_day_queue_id_queue"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "queue_id", "service_day", name=op.f("pk_queue_capacity_day")
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "appointment",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("queue_id", sa.String(length=36), nullable=False),
        sa.Column("slot_id", sa.String(length=36), nullable=False),
        sa.Column("patient_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("booked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_appointment_patient_id_patient"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_appointment_queue_id_queue"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_appointment_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["slot_id"],
            [f"{SCHEMA}.appointment_slot.id"],
            name=op.f("fk_appointment_slot_id_appointment_slot"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointment")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_appointment_patient",
        "appointment",
        ["patient_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_appointment_slot_status",
        "appointment",
        ["slot_id", "status"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the appointment tables. Every slot, block and booking is lost; tickets are untouched."""
    op.drop_index(
        "ix_clinicq_appointment_slot_status", table_name="appointment", schema=SCHEMA
    )
    op.drop_index(
        "ix_clinicq_appointment_patient", table_name="appointment", schema=SCHEMA
    )
    op.drop_table("appointment", schema=SCHEMA)
    op.drop_table("queue_capacity_day", schema=SCHEMA)
    op.drop_index(
        "ix_clinicq_appointment_template_window_queue",
        table_name="appointment_template_window",
        schema=SCHEMA,
    )
    op.drop_table("appointment_template_window", schema=SCHEMA)
    op.drop_index(
        "ix_clinicq_appointment_slot_site_day",
        table_name="appointment_slot",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_clinicq_appointment_slot_queue_day",
        table_name="appointment_slot",
        schema=SCHEMA,
    )
    op.drop_table("appointment_slot", schema=SCHEMA)
    op.drop_index(
        "ix_clinicq_appointment_day_override_queue_day",
        table_name="appointment_day_override",
        schema=SCHEMA,
    )
    op.drop_table("appointment_day_override", schema=SCHEMA)
    op.drop_index(
        "ix_clinicq_appointment_block_site_starts",
        table_name="appointment_block",
        schema=SCHEMA,
    )
    op.drop_table("appointment_block", schema=SCHEMA)
    op.drop_table("appointment_policy", schema=SCHEMA)
