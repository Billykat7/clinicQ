"""SMS spending controls: a clinic's daily cap, the kill switch, cap alerts and receipt events (Issue 65)

* ``site.sms_daily_cap``: nullable, at least 0; ``NULL`` uses ``SMS_SITE_DAILY_CAP``.
* ``platform_switch``: run-time switches an operator flips from the API (the SMS kill switch), read on
  every send.
* ``sms_cap_alert``: one row per cap reached per day, unique by ``(reason, subject_id, service_day)``, so
  the team is alerted once.
* ``sms_delivery_event``: one row per delivery receipt, keyed by ``provider:message id:status``, so a
  receipt delivered twice is applied once.

Nothing existing is dropped or narrowed; the release before this one runs unaffected.

Revision ID: 0034
Revises: 0033
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def _timestamps() -> list[sa.Column]:
    """``created_at`` and ``modified_at``, as TimestampMixin declares them."""
    return [
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
    ]


def upgrade() -> None:
    """Add the clinic cap column and the three tables."""
    op.add_column(
        "site", sa.Column("sms_daily_cap", sa.Integer(), nullable=True), schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_site_sms_daily_cap"),
        "site",
        "sms_daily_cap IS NULL OR sms_daily_cap >= 0",
        schema=SCHEMA,
    )
    op.create_table(
        "platform_switch",
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("changed_by", sa.String(length=255), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_platform_switch")),
        schema=SCHEMA,
    )
    op.create_table(
        "sms_cap_alert",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.String(length=24), nullable=False),
        sa.Column("subject_id", sa.String(length=36), nullable=False),
        sa.Column("service_day", sa.Date(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sms_cap_alert")),
        sa.UniqueConstraint(
            "reason", "subject_id", "service_day", name="uq_sms_cap_alert_day"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "sms_delivery_event",
        sa.Column("id", sa.String(length=320), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sms_delivery_event")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the tables and the column. The kill switch, caps and receipt history are lost."""
    op.drop_table("sms_delivery_event", schema=SCHEMA)
    op.drop_table("sms_cap_alert", schema=SCHEMA)
    op.drop_table("platform_switch", schema=SCHEMA)
    op.drop_constraint(
        op.f("ck_site_sms_daily_cap"), "site", type_="check", schema=SCHEMA
    )
    op.drop_column("site", "sms_daily_cap", schema=SCHEMA)
