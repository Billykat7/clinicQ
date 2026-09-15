"""reminders: when a booking was reminded, confirmed or cancelled by reply (Issue 82)

* ``appointment.reminded_24h_at`` and ``reminded_2h_at``: when each reminder went, so each goes once and #93 can
  compare attendance with and without them.
* ``appointment.confirmed_at``: a reply confirming the booking.
* ``appointment.reply_token``: the secret a web push's Confirm and Cancel buttons send, unique
  (``uq_appointment_reply_token``).
* ``appointment.cancelled_via``: the channel a cancellation by reply came through.

Every column is nullable; the release before this one runs unaffected.

Revision ID: 0043
Revises: 0042
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every object this project owns is schema-qualified; nothing goes in ``public``.
SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Add the reminder and reply columns."""
    op.add_column(
        "appointment",
        sa.Column("reminded_24h_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "appointment",
        sa.Column("reminded_2h_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "appointment",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "appointment",
        sa.Column("reply_token", sa.String(length=43), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "appointment",
        sa.Column("cancelled_via", sa.String(length=16), nullable=True),
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        "uq_appointment_reply_token", "appointment", ["reply_token"], schema=SCHEMA
    )


def downgrade() -> None:
    """Drop them. Reminder history and confirmations are lost; bookings stay."""
    op.drop_constraint(
        "uq_appointment_reply_token", "appointment", schema=SCHEMA, type_="unique"
    )
    op.drop_column("appointment", "cancelled_via", schema=SCHEMA)
    op.drop_column("appointment", "reply_token", schema=SCHEMA)
    op.drop_column("appointment", "confirmed_at", schema=SCHEMA)
    op.drop_column("appointment", "reminded_2h_at", schema=SCHEMA)
    op.drop_column("appointment", "reminded_24h_at", schema=SCHEMA)
