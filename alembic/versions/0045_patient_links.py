"""patient links: one phone acting for another person (Issue 84)

* ``patient_link``: proxy, dependant, relationship, when the dependant's number was proved, and when the
  link was ended (``uq_patient_link_pair``, ``ix_clinicq_patient_link_proxy``, ``ix_clinicq_patient_link_dependant``).
* ``ticket.proxy_patient_id`` and ``appointment.proxy_patient_id``: who acted, when it was not the
  patient themselves. The ticket and the booking still belong to the patient.
* ``patient.phone_e164`` becomes nullable: a dependant with no phone of their own (a small child) is a
  record that can never sign in and whose messages go to the proxy's phone. It stays unique, and
  PostgreSQL allows many rows with no number.

Nothing existing changes: every patient today keeps their number and no link exists yet.

Revision ID: 0045
Revises: 0044
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every object this project owns is schema-qualified; nothing goes in ``public``.
SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Add the links, the two proxy columns, and let a dependant have no phone."""
    op.create_table(
        "patient_link",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("proxy_patient_id", sa.String(length=36), nullable=False),
        sa.Column("dependant_patient_id", sa.String(length=36), nullable=False),
        sa.Column("relationship_kind", sa.String(length=16), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=36), nullable=True),
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
            ["dependant_patient_id"],
            [f"{SCHEMA}.patient.id"],
            name=op.f("fk_patient_link_dependant_patient_id_patient"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["proxy_patient_id"],
            [f"{SCHEMA}.patient.id"],
            name=op.f("fk_patient_link_proxy_patient_id_patient"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patient_link")),
        sa.UniqueConstraint(
            "proxy_patient_id", "dependant_patient_id", name="uq_patient_link_pair"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_patient_link_dependant",
        "patient_link",
        ["dependant_patient_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_patient_link_proxy",
        "patient_link",
        ["proxy_patient_id", "revoked_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.add_column(
        "appointment",
        sa.Column("proxy_patient_id", sa.String(length=36), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        op.f("fk_appointment_proxy_patient_id_patient"),
        "appointment",
        "patient",
        ["proxy_patient_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.alter_column(
        "patient",
        "phone_e164",
        existing_type=sa.VARCHAR(length=16),
        nullable=True,
        schema=SCHEMA,
    )
    op.add_column(
        "ticket",
        sa.Column("proxy_patient_id", sa.String(length=36), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        op.f("fk_ticket_proxy_patient_id_patient"),
        "ticket",
        "patient",
        ["proxy_patient_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Drop them again. A patient with no number must be removed first, or the column cannot go back."""
    op.drop_constraint(
        op.f("fk_ticket_proxy_patient_id_patient"),
        "ticket",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("ticket", "proxy_patient_id", schema=SCHEMA)
    op.alter_column(
        "patient",
        "phone_e164",
        existing_type=sa.VARCHAR(length=16),
        nullable=False,
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("fk_appointment_proxy_patient_id_patient"),
        "appointment",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("appointment", "proxy_patient_id", schema=SCHEMA)
    op.drop_index(
        "ix_clinicq_patient_link_proxy", table_name="patient_link", schema=SCHEMA
    )
    op.drop_index(
        "ix_clinicq_patient_link_dependant", table_name="patient_link", schema=SCHEMA
    )
    op.drop_table("patient_link", schema=SCHEMA)
