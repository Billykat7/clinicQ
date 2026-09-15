"""kiosk: a check-in device, a patient's arrival and walk-ins at the door (Issue 83)

* ``display_device.kind``: what a paired device is, ``board`` (every existing one) or ``check_in``.
* ``ticket.arrived_at``: when the patient said they are here at the check-in tablet.
* ``site.kiosk_walk_ins_enabled``: whether that tablet may also start a walk-in ticket.

Every existing device keeps showing the board, and every clinic starts with kiosk walk-ins off, so the
release before this one runs unaffected.

Revision ID: 0044
Revises: 0043
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema, DisplayDeviceKind

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every object this project owns is schema-qualified; nothing goes in ``public``.
SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Add the device kind, the arrival time and the kiosk walk-in switch."""
    op.add_column(
        "display_device",
        sa.Column(
            "kind",
            sa.String(length=10),
            nullable=False,
            server_default=DisplayDeviceKind.BOARD.value,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "ticket",
        sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "site",
        sa.Column(
            "kiosk_walk_ins_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop them again; paired devices go back to being boards."""
    op.drop_column("site", "kiosk_walk_ins_enabled", schema=SCHEMA)
    op.drop_column("ticket", "arrived_at", schema=SCHEMA)
    op.drop_column("display_device", "kind", schema=SCHEMA)
