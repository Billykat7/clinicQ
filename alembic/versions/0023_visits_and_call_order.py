"""visits, the order a queue is called in, and where a transfer lands (Issue 45)

**``visit``** links the tickets of one patient's journey through a clinic (triage, a consulting room,
the pharmacy). Every ticket now belongs to one: ``ticket.visit_id`` is ``NOT NULL``. Existing tickets
are backfilled with a visit each (a ticket issued before visits existed was a journey of one leg),
before the column is made required, so the upgrade works on a database that already has tickets.

**``ticket.transferred_from_id``** points a transfer's new ticket at the leg before it.

**``ticket.order_key``** is the order a queue is called in, lowest first. It starts as the ticket's
sequence (the backfill copies it), so nothing already waiting changes place. A transfer placed by
arrival order, or a priority override (Issue 46), sets a key between two neighbours instead. A
patient's position is still derived from this order at every read, never stored.

**``site.transfer_placement``** is ``arrival_order`` (the default, by when the visit began) or
``back_of_line``, with a check constraint.

Business time: ``visit.started_at`` is Africa/Johannesburg.

Revision ID: 0023
Revises: 0022
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create ``visit``, give every ticket one, and add the order and placement columns."""
    op.create_table(
        "visit",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("patient_id", sa.String(length=36), nullable=True),
        # Business time, Africa/Johannesburg: when the visit's first ticket was issued.
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["patient_id"],
            [f"{SCHEMA}.patient.id"],
            name=op.f("fk_visit_patient_id_patient"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_visit_site_id_site"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visit")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_visit_site_started",
        "visit",
        ["site_id", "started_at"],
        unique=False,
        schema=SCHEMA,
    )

    op.add_column(
        "ticket",
        sa.Column("visit_id", sa.String(length=36), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "ticket",
        sa.Column("transferred_from_id", sa.String(length=36), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "ticket", sa.Column("order_key", sa.Float(), nullable=True), schema=SCHEMA
    )

    # Backfill: one visit per existing ticket, and the call order it already had.
    bind = op.get_bind()
    tickets = bind.execute(
        sa.text(f"SELECT id, site_id, patient_id, joined_at FROM {SCHEMA}.ticket")
    ).all()
    for ticket_id, site_id, patient_id, joined_at in tickets:
        visit_id = str(uuid.uuid7())
        bind.execute(
            sa.text(
                f"INSERT INTO {SCHEMA}.visit (id, site_id, patient_id, started_at) "
                "VALUES (:id, :site, :patient, :started)"
            ),
            {
                "id": visit_id,
                "site": site_id,
                "patient": patient_id,
                "started": joined_at,
            },
        )
        bind.execute(
            sa.text(f"UPDATE {SCHEMA}.ticket SET visit_id = :visit WHERE id = :id"),
            {"visit": visit_id, "id": ticket_id},
        )
    bind.execute(sa.text(f"UPDATE {SCHEMA}.ticket SET order_key = sequence"))

    op.alter_column("ticket", "visit_id", nullable=False, schema=SCHEMA)
    op.alter_column("ticket", "order_key", nullable=False, schema=SCHEMA)
    op.create_foreign_key(
        op.f("fk_ticket_visit_id_visit"),
        "ticket",
        "visit",
        ["visit_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_ticket_transferred_from_id_ticket"),
        "ticket",
        "ticket",
        ["transferred_from_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_clinicq_ticket_visit",
        "ticket",
        ["visit_id", "joined_at"],
        unique=False,
        schema=SCHEMA,
    )

    op.add_column(
        "site",
        sa.Column(
            "transfer_placement",
            sa.String(length=16),
            server_default="arrival_order",
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_site_transfer_placement"),
        "site",
        "transfer_placement IN ('arrival_order', 'back_of_line')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the columns and the visits. A transfer's legs then stand as unrelated tickets."""
    op.drop_constraint(
        op.f("ck_site_transfer_placement"), "site", type_="check", schema=SCHEMA
    )
    op.drop_column("site", "transfer_placement", schema=SCHEMA)
    op.drop_index("ix_clinicq_ticket_visit", table_name="ticket", schema=SCHEMA)
    op.drop_constraint(
        op.f("fk_ticket_transferred_from_id_ticket"),
        "ticket",
        type_="foreignkey",
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("fk_ticket_visit_id_visit"), "ticket", type_="foreignkey", schema=SCHEMA
    )
    op.drop_column("ticket", "order_key", schema=SCHEMA)
    op.drop_column("ticket", "transferred_from_id", schema=SCHEMA)
    op.drop_column("ticket", "visit_id", schema=SCHEMA)
    op.drop_index("ix_clinicq_visit_site_started", table_name="visit", schema=SCHEMA)
    op.drop_table("visit", schema=SCHEMA)
