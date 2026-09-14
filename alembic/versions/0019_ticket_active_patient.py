"""ticket: one active ticket per patient per queue, and a name only the desk gives (Issue 40)

Two changes to ``ticket``; no data is rewritten, and no route wrote tickets before this revision.

**``uq_ticket_active_patient``**, a partial unique index on ``(queue_id, service_day, patient_id)``.
``join_queue()`` returns a patient's existing ticket when they join a queue they are already in
("you already hold A041"). Two joins by the same patient in the same instant can both pass that
check before either has written, so the rule is also held here: the second insert is refused and the
join service reads the winner's ticket back. It covers only a patient's tickets still in the day
(``waiting``, ``called``, ``recalled``, ``in_progress``), so a patient who was seen, cancelled or
transferred may join again, and a walk-in with no patient is never constrained. ``service_day`` is in
the key, so a ticket left open overnight cannot block tomorrow's join.

**``display_name`` becomes ``walk_in_name``**, with ``ck_ticket_walk_in_name`` allowing it only on a
walk-in. A patient who joins by phone is named by their own record, which reaches a public screen only
through ``board_projection`` and their consent (Issue 21); a copy of that name on the ticket would
have been a second, unconsented route to the board. What remains is what the front desk writes down
to call a walk-in by, staff-facing only.

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value

#: The active statuses at this revision, frozen (a later status arrives with its own migration).
_ACTIVE = "patient_id IS NOT NULL AND status IN ('called', 'in_progress', 'recalled', 'waiting')"


def upgrade() -> None:
    """Rename the name column, constrain it to walk-ins, and add the partial unique index."""
    op.alter_column(
        "ticket", "display_name", new_column_name="walk_in_name", schema=SCHEMA
    )
    op.create_check_constraint(
        op.f("ck_ticket_walk_in_name"),
        "ticket",
        "walk_in_name IS NULL OR source = 'walk_in'",
        schema=SCHEMA,
    )
    op.create_index(
        "uq_ticket_active_patient",
        "ticket",
        ["queue_id", "service_day", "patient_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text(_ACTIVE),
    )


def downgrade() -> None:
    """Reverse all three. The join service's own duplicate check still holds without the index."""
    op.drop_index("uq_ticket_active_patient", table_name="ticket", schema=SCHEMA)
    op.drop_constraint(
        op.f("ck_ticket_walk_in_name"), "ticket", type_="check", schema=SCHEMA
    )
    op.alter_column(
        "ticket", "walk_in_name", new_column_name="display_name", schema=SCHEMA
    )
