"""notification failure alerts: one team alert per transport per window (Issue 71)

* ``notification_failure_alert``: a transport whose delivery failure rate crossed the threshold in a window,
  with what was measured. Unique by transport and window start, so the watch announces a failing transport
  once per window however many instances run it.

Nothing existing changes; the release before this one runs unaffected.

Revision ID: 0037
Revises: 0036
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create the alert table."""
    op.create_table(
        "notification_failure_alert",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempted", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("failure_rate", sa.Numeric(precision=5, scale=4), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_failure_alert")),
        sa.UniqueConstraint(
            "channel", "window_start", name="uq_notification_failure_alert_window"
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the alert table. The record of past alerts is lost; the ledger is untouched."""
    op.drop_table("notification_failure_alert", schema=SCHEMA)
