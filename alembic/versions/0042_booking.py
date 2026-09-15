"""booking: references, channels, reschedules and conversion into tickets (Issue 81)

* ``appointment.reference``: six unambiguous characters, unique (``uq_appointment_reference``); existing
  bookings are given one here.
* ``appointment.source``: the channel it was booked through; ``rescheduled_from_id``, ``converted_at`` and
  ``lapsed_at`` record its later life. ``ix_clinicq_appointment_status`` serves the conversion sweep.
* ``appointment_policy.convert_lead_minutes``: how long before its time a booking becomes a ticket (5 to 240,
  default 30, ``ck_appointment_policy_convert_lead_minutes_range``).
* ``ticket.appointment_id``: the booking a ticket came from, unique (``uq_ticket_appointment``): the
  conversion's idempotency key.

Every new column is nullable or defaulted; the release before this one runs unaffected.

Revision ID: 0042
Revises: 0041
"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value
#: The ticket reference alphabet (src/modules/queue/sequence.py), written out so this revision never changes.
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def upgrade() -> None:
    """Add the booking columns, give existing bookings a reference, and link tickets to bookings."""
    op.add_column(
        "appointment",
        sa.Column("reference", sa.String(length=6), nullable=True),
        schema=SCHEMA,
    )
    bind = op.get_bind()
    table = sa.table(
        "appointment", sa.column("id"), sa.column("reference"), schema=SCHEMA
    )
    for (appointment_id,) in bind.execute(sa.select(table.c.id)).all():
        bind.execute(
            table.update()
            .where(table.c.id == appointment_id)
            .values(reference="".join(secrets.choice(_ALPHABET) for _ in range(6)))
        )
    op.alter_column("appointment", "reference", nullable=False, schema=SCHEMA)
    op.add_column(
        "appointment",
        sa.Column("source", sa.String(length=16), server_default="web", nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "appointment",
        sa.Column("rescheduled_from_id", sa.String(length=36), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "appointment",
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "appointment",
        sa.Column("lapsed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_appointment_status",
        "appointment",
        ["status"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        "uq_appointment_reference", "appointment", ["reference"], schema=SCHEMA
    )
    op.create_foreign_key(
        op.f("fk_appointment_rescheduled_from_id_appointment"),
        "appointment",
        "appointment",
        ["rescheduled_from_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.add_column(
        "appointment_policy",
        sa.Column(
            "convert_lead_minutes", sa.Integer(), server_default="30", nullable=False
        ),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_appointment_policy_convert_lead_minutes_range"),
        "appointment_policy",
        "convert_lead_minutes BETWEEN 5 AND 240",
        schema=SCHEMA,
    )
    op.add_column(
        "ticket",
        sa.Column("appointment_id", sa.String(length=36), nullable=True),
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        "uq_ticket_appointment", "ticket", ["appointment_id"], schema=SCHEMA
    )
    op.create_foreign_key(
        op.f("fk_ticket_appointment_id_appointment"),
        "ticket",
        "appointment",
        ["appointment_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Drop them. References, channels and the ticket-to-booking link are lost; bookings and tickets stay."""
    op.drop_constraint(
        op.f("fk_ticket_appointment_id_appointment"),
        "ticket",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_constraint("uq_ticket_appointment", "ticket", schema=SCHEMA, type_="unique")
    op.drop_column("ticket", "appointment_id", schema=SCHEMA)
    op.drop_constraint(
        op.f("ck_appointment_policy_convert_lead_minutes_range"),
        "appointment_policy",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_column("appointment_policy", "convert_lead_minutes", schema=SCHEMA)
    op.drop_constraint(
        op.f("fk_appointment_rescheduled_from_id_appointment"),
        "appointment",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_appointment_reference", "appointment", schema=SCHEMA, type_="unique"
    )
    op.drop_index(
        "ix_clinicq_appointment_status", table_name="appointment", schema=SCHEMA
    )
    op.drop_column("appointment", "lapsed_at", schema=SCHEMA)
    op.drop_column("appointment", "converted_at", schema=SCHEMA)
    op.drop_column("appointment", "rescheduled_from_id", schema=SCHEMA)
    op.drop_column("appointment", "source", schema=SCHEMA)
    op.drop_column("appointment", "reference", schema=SCHEMA)
