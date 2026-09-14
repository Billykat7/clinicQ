"""tickets: a patient's place in a queue, numbered by the database (Issue 39)

Two new tables; nothing existing changes, so the release before this one runs unaffected.

* ``ticket_sequence`` is the counter a ticket number comes from: one row per queue and Johannesburg
  service day, incremented by ``INSERT … ON CONFLICT DO UPDATE … RETURNING``
  (``src/modules/queue/sequence.py``). The row lock that statement takes is what serialises
  concurrent joins on one queue, and a new day is a new row, which is the whole midnight reset.
* ``ticket`` is the ticket. The constraints are the point of the migration:

  - ``uq_ticket_queue_id_service_day_sequence`` makes a repeated number impossible at the database
    level, whatever the application does;
  - ``uq_ticket_reference_code`` keeps the desk's six-character lookup code unique;
  - ``ck_ticket_patient_or_walk_in`` lets a walk-in exist with no patient row (no placeholder
    patients) and a remote join never exist without one;
  - ``ck_ticket_status`` and ``ck_ticket_source`` hold the Issue 4 vocabularies. The literal
    values are frozen here on purpose: a migration records the values of its day, and a later enum
    member arrives with a migration of its own.

  ``ix_clinicq_ticket_board`` ``(queue_id, service_day, status, sequence)`` is the board's query,
  ``ix_clinicq_ticket_patient`` (partial, ``patient_id IS NOT NULL``) a patient's own tickets, and
  ``ix_clinicq_ticket_site_day`` a clinic's day across its queues.

The foreign keys from ``ticket`` are ``RESTRICT``: a queue, clinic or patient with ticket history is
deactivated or soft-deleted, never removed, and a delete that would orphan a day's tickets fails.

Datetimes (``joined_at``, ``called_at``, ``started_at``, ``completed_at``) are business time,
Africa/Johannesburg, stored as ``timestamptz``; ``service_day`` is the Johannesburg calendar date.

**Not reversible once tickets exist.** The downgrade refuses to drop a table holding tickets, rather
than deleting a clinic's queue history as a side effect of a rollback; an empty table drops cleanly.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value

#: The Issue 4 vocabularies as they stand at this revision (see the module docstring).
_STATUSES = (
    "called",
    "cancelled",
    "done",
    "in_progress",
    "no_show",
    "recalled",
    "transferred",
    "waiting",
)
_SOURCES = ("ussd", "walk_in", "web", "whatsapp")


def _in(column: str, values: tuple[str, ...]) -> str:
    """``column IN ('a', 'b')``, in the order the model renders it."""
    return f"{column} IN ({', '.join(f"'{value}'" for value in values)})"


def upgrade() -> None:
    """Create ``ticket_sequence`` and ``ticket``, their constraints and indexes."""
    op.create_table(
        "ticket_sequence",
        sa.Column("queue_id", sa.String(length=36), nullable=False),
        # The Johannesburg calendar date the counter numbers.
        sa.Column("service_day", sa.Date(), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "last_value >= 1", name=op.f("ck_ticket_sequence_last_value_positive")
        ),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_ticket_sequence_queue_id_queue"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "queue_id", "service_day", name=op.f("pk_ticket_sequence")
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "ticket",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("queue_id", sa.String(length=36), nullable=False),
        sa.Column("patient_id", sa.String(length=36), nullable=True),
        # The Johannesburg calendar date the ticket was issued on.
        sa.Column("service_day", sa.Date(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=10), nullable=False),
        sa.Column("reference_code", sa.String(length=6), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=True),
        sa.Column("reason_text", sa.String(length=140), nullable=True),
        sa.Column(
            "comment_consent", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column(
            "status", sa.String(length=16), server_default="waiting", nullable=False
        ),
        # Business time, Africa/Johannesburg.
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("called_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "patient_id IS NOT NULL OR source = 'walk_in'",
            name=op.f("ck_ticket_patient_or_walk_in"),
        ),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_ticket_sequence_positive")),
        sa.CheckConstraint(_in("source", _SOURCES), name=op.f("ck_ticket_source")),
        sa.CheckConstraint(_in("status", _STATUSES), name=op.f("ck_ticket_status")),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            [f"{SCHEMA}.patient.id"],
            name=op.f("fk_ticket_patient_id_patient"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_ticket_queue_id_queue"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_ticket_site_id_site"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket")),
        sa.UniqueConstraint(
            "queue_id",
            "service_day",
            "sequence",
            name="uq_ticket_queue_id_service_day_sequence",
        ),
        sa.UniqueConstraint("reference_code", name="uq_ticket_reference_code"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_ticket_board",
        "ticket",
        ["queue_id", "service_day", "status", "sequence"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_ticket_patient",
        "ticket",
        ["patient_id", "service_day"],
        unique=False,
        schema=SCHEMA,
        postgresql_where=sa.text("patient_id IS NOT NULL"),
    )
    op.create_index(
        "ix_clinicq_ticket_site_day",
        "ticket",
        ["site_id", "service_day"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop both tables, refusing while any ticket exists (see the module docstring)."""
    issued = op.get_bind().execute(sa.text(f"SELECT count(*) FROM {SCHEMA}.ticket"))
    if issued.scalar_one():
        raise RuntimeError(
            "Refusing to drop clinicq.ticket while it holds tickets: that would delete the "
            "clinics' queue history. Export or archive the rows first."
        )
    op.drop_index("ix_clinicq_ticket_site_day", table_name="ticket", schema=SCHEMA)
    op.drop_index("ix_clinicq_ticket_patient", table_name="ticket", schema=SCHEMA)
    op.drop_index("ix_clinicq_ticket_board", table_name="ticket", schema=SCHEMA)
    op.drop_table("ticket", schema=SCHEMA)
    op.drop_table("ticket_sequence", schema=SCHEMA)
