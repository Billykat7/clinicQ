"""patient preferences: quiet hours, muted events, a global opt-out, and SMS replies (Issue 67)

* ``patient_notification_preference``: ``quiet_hours_start`` and ``quiet_hours_end`` (Johannesburg wall
  clock), ``muted_events`` (a JSON list, empty by default), ``opted_out_at`` (set when the patient stops
  every message) and ``opted_out_via`` (where they last changed their preferences).
* ``sms_inbound_event``: one row per SMS reply, keyed by the gateway's message id, keeping the keyword, the
  outcome and the patient, never the number or the text.

Nothing existing is dropped; the release before this one runs unaffected.

Revision ID: 0036
Revises: 0035
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value
TABLE = "patient_notification_preference"


def upgrade() -> None:
    """Add the preference columns and the replies table."""
    op.add_column(
        TABLE, sa.Column("quiet_hours_start", sa.Time(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        TABLE, sa.Column("quiet_hours_end", sa.Time(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        TABLE,
        sa.Column(
            "muted_events", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column("opted_out_via", sa.String(length=16), nullable=True),
        schema=SCHEMA,
    )
    op.create_table(
        "sms_inbound_event",
        sa.Column("id", sa.String(length=320), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("keyword", sa.String(length=16), nullable=True),
        sa.Column("outcome", sa.String(length=12), nullable=False),
        sa.Column("patient_id", sa.String(length=36), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sms_inbound_event")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the replies table and the columns. Every patient's opt-out and quiet hours are lost."""
    op.drop_table("sms_inbound_event", schema=SCHEMA)
    for column in (
        "opted_out_via",
        "opted_out_at",
        "muted_events",
        "quiet_hours_end",
        "quiet_hours_start",
    ):
        op.drop_column(TABLE, column, schema=SCHEMA)
