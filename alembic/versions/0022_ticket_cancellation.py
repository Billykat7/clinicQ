"""ticket cancellation: the channel it came through and the optional reason (Issue 44)

Two nullable columns on ``ticket`` and one check constraint; nothing is rewritten.

* ``cancelled_via``: the channel a cancellation came from (web, USSD, WhatsApp or the front desk), so
  "cancellation works identically on all four channels" can be audited and reported per channel.
* ``cancellation_reason``: one of the ``CancellationReason`` values when the patient chose to give
  one, for the no-show analysis (Issue 93). Never free text.
* ``ck_ticket_cancellation_only_when_cancelled``: only a cancelled ticket carries either, so a report
  cannot count a reason on a ticket that was seen.

There is **no position column**, on purpose: a patient's place is derived from the order of the
tickets still waiting, so one cancellation leaves nobody else's row stale.

Revision ID: 0022
Revises: 0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Add the two columns and the constraint."""
    op.add_column(
        "ticket",
        sa.Column("cancelled_via", sa.String(length=16), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "ticket",
        sa.Column("cancellation_reason", sa.String(length=24), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_ticket_cancellation_only_when_cancelled"),
        "ticket",
        "(cancelled_via IS NULL AND cancellation_reason IS NULL) OR status = 'cancelled'",
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop them; cancelled tickets keep their status and lose how and why."""
    op.drop_constraint(
        op.f("ck_ticket_cancellation_only_when_cancelled"),
        "ticket",
        type_="check",
        schema=SCHEMA,
    )
    op.drop_column("ticket", "cancellation_reason", schema=SCHEMA)
    op.drop_column("ticket", "cancelled_via", schema=SCHEMA)
