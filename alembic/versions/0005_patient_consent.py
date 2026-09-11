"""patient_consent and patient_consent_event: the current answer, and the proof (Issue 21)

Two new tables; nothing existing changes, so the release before this one runs unaffected.

* ``patient_consent`` holds one row per patient per purpose — the answer the board and the
  notification service read. No row means **not granted**: the most private answer is the absence
  of a decision, so a patient who was never asked is never treated as having agreed.
* ``patient_consent_event`` is the history, one row per answer ever given, with the channel and the
  wording version. It is what proves consent was given, and when it was withdrawn.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create both consent tables."""
    op.create_table(
        "patient_consent",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("patient_id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source_channel", sa.String(length=16), nullable=False),
        sa.Column("wording_version", sa.String(length=32), nullable=False),
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
            name=op.f("fk_patient_consent_patient_id_patient"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patient_consent")),
        sa.UniqueConstraint(
            "patient_id", "purpose", name=op.f("uq_patient_consent_patient_id")
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "patient_consent_event",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("patient_id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("source_channel", sa.String(length=16), nullable=False),
        sa.Column("wording_version", sa.String(length=32), nullable=False),
        sa.Column("recorded_by", sa.String(length=36), nullable=True),
        sa.Column("site_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            [f"{SCHEMA}.patient.id"],
            name=op.f("fk_patient_consent_event_patient_id_patient"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patient_consent_event")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_patient_consent_event_patient",
        "patient_consent_event",
        ["patient_id", "purpose", "created_at"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop both tables."""
    op.drop_index(
        "ix_clinicq_patient_consent_event_patient",
        table_name="patient_consent_event",
        schema=SCHEMA,
    )
    op.drop_table("patient_consent_event", schema=SCHEMA)
    op.drop_table("patient_consent", schema=SCHEMA)
