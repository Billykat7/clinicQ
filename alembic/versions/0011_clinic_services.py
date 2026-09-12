"""clinic_service: what a clinic offers, and how long each thing takes (Issue 26)

Two new objects; nothing existing changes, so the release before this one runs unaffected.

**The table is ``clinic_service``, not ``service``.** Every module in this codebase has a
``service.py`` holding its persistence layer, so a model called ``Service`` would be misread in
every review it appears in; the table is named to match the model.

``queue_clinic_service`` is the link between a queue and the services it handles. It carries no
columns of its own beyond its two foreign keys, and both ends are already site-scoped by their
parents, which is why it is a plain association table rather than a model: a mapped class would add
a row to the tenancy guard's surface for nothing.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema, ServiceCategory

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create ``clinic_service``, its per-site unique constraints, and the queue link table."""
    op.create_table(
        "clinic_service",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column(
            "category",
            sa.String(length=24),
            nullable=False,
            server_default=ServiceCategory.OTHER.value,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        # The wait estimator's prior until Issue 42 has real samples; validated to 1..240 minutes.
        sa.Column(
            "expected_minutes", sa.Integer(), nullable=False, server_default="15"
        ),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "requires_appointment",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
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
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_clinic_service_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clinic_service")),
        sa.UniqueConstraint("site_id", "slug", name="uq_clinic_service_site_id_slug"),
        sa.UniqueConstraint("site_id", "name", name="uq_clinic_service_site_id_name"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_clinic_service_site_active",
        "clinic_service",
        ["site_id", "is_active", "display_order"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "queue_clinic_service",
        sa.Column("queue_id", sa.String(length=36), nullable=False),
        sa.Column("clinic_service_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_queue_clinic_service_queue_id_queue"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["clinic_service_id"],
            [f"{SCHEMA}.clinic_service.id"],
            name=op.f("fk_queue_clinic_service_clinic_service_id_clinic_service"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "queue_id", "clinic_service_id", name=op.f("pk_queue_clinic_service")
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop both tables. Reversible: the wait estimator that reads them arrives with Issue 42."""
    op.drop_table("queue_clinic_service", schema=SCHEMA)
    op.drop_index(
        "ix_clinicq_clinic_service_site_active",
        table_name="clinic_service",
        schema=SCHEMA,
    )
    op.drop_table("clinic_service", schema=SCHEMA)
