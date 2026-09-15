"""virtual waiting room: a clinic's switch, and each ticket's trip, alert and "on my way" (Issue 86)

* ``site.virtual_waiting_enabled``: off for every clinic, existing and new, until its manager turns it on.
* ``ticket.travel_minutes``: the trip the patient stated when joining (0 to 180, ``ck_ticket_travel_minutes_range``).
* ``ticket.leave_alert_at``: when the patient was told it is time to leave; set once.
* ``ticket.on_my_way_at``: when the patient tapped "On my way", which reception sees.

Every column is nullable or defaulted, so the release before this one runs unaffected.

Revision ID: 0040
Revises: 0039
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Add the clinic's switch and the ticket's three columns."""
    op.add_column(
        "site",
        sa.Column(
            "virtual_waiting_enabled",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "ticket",
        sa.Column("travel_minutes", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "ticket",
        sa.Column("leave_alert_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "ticket",
        sa.Column("on_my_way_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_ticket_travel_minutes_range"),
        "ticket",
        "travel_minutes IS NULL OR travel_minutes BETWEEN 0 AND 180",
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop them. Stated trips and "on my way" taps are lost; tickets and places are untouched."""
    op.drop_constraint(
        op.f("ck_ticket_travel_minutes_range"), "ticket", type_="check", schema=SCHEMA
    )
    op.drop_column("ticket", "on_my_way_at", schema=SCHEMA)
    op.drop_column("ticket", "leave_alert_at", schema=SCHEMA)
    op.drop_column("ticket", "travel_minutes", schema=SCHEMA)
    op.drop_column("site", "virtual_waiting_enabled", schema=SCHEMA)
