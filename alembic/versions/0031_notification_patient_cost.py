"""notification: patients as recipients, cost, dedupe and fallback; patient transport preference (Issue 63)

The delivery ledger was built for account holders (an email address or a phone number). A patient has
no account, so a patient notification needs:

* ``patient_id``: who it is for, whatever transport it went out on;
* ``site_id``: which clinic sent it, so its cost is reportable per clinic;
* ``event``: what happened to the ticket (``called``, ``next``, …);
* ``dedupe_key`` (unique): one queue event, one message, however often the event is replayed;
* ``fallback_of_id``: the free transport that failed before this row, so the chain is on the ledger;
* ``cost`` and ``cost_currency``: what the provider charged.

Every new column is nullable, so the release before this one reads and writes the table unchanged.
``patient_notification_preference`` is a new table holding a patient's preferred transport.

Revision ID: 0031
Revises: 0030
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Add the patient, clinic, event, dedupe, fallback and cost columns, and the preference table."""
    op.add_column(
        "notification",
        sa.Column("patient_id", sa.String(length=36), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "notification",
        sa.Column("site_id", sa.String(length=36), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "notification",
        sa.Column("event", sa.String(length=20), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "notification",
        sa.Column("dedupe_key", sa.String(length=160), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "notification",
        sa.Column("fallback_of_id", sa.String(length=36), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "notification",
        sa.Column("cost", sa.Numeric(precision=10, scale=4), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "notification",
        sa.Column("cost_currency", sa.String(length=3), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        op.f("fk_notification_patient_id_patient"),
        "notification",
        "patient",
        ["patient_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_notification_site_id_site"),
        "notification",
        "site",
        ["site_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_notification_fallback_of_id_notification"),
        "notification",
        "notification",
        ["fallback_of_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        op.f("uq_notification_dedupe_key"),
        "notification",
        ["dedupe_key"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_notification_patient_id",
        "notification",
        ["patient_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_notification_site_id",
        "notification",
        ["site_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "patient_notification_preference",
        sa.Column("patient_id", sa.String(length=36), nullable=False),
        sa.Column("preferred_channel", sa.String(length=10), nullable=True),
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
            name=op.f("fk_patient_notification_preference_patient_id_patient"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "patient_id", name=op.f("pk_patient_notification_preference")
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the preference table and the new columns. Loses the patient, cost and dedupe details."""
    op.drop_table("patient_notification_preference", schema=SCHEMA)
    op.drop_index(
        "ix_clinicq_notification_site_id", table_name="notification", schema=SCHEMA
    )
    op.drop_index(
        "ix_clinicq_notification_patient_id", table_name="notification", schema=SCHEMA
    )
    op.drop_constraint(
        op.f("uq_notification_dedupe_key"),
        "notification",
        type_="unique",
        schema=SCHEMA,
    )
    for constraint in (
        "fk_notification_fallback_of_id_notification",
        "fk_notification_site_id_site",
        "fk_notification_patient_id_patient",
    ):
        op.drop_constraint(
            op.f(constraint), "notification", type_="foreignkey", schema=SCHEMA
        )
    for column in (
        "cost_currency",
        "cost",
        "fallback_of_id",
        "dedupe_key",
        "event",
        "site_id",
        "patient_id",
    ):
        op.drop_column("notification", column, schema=SCHEMA)
