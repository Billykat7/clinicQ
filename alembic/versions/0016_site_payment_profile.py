"""site_payment_profile and site_payment_medical_aid: what a private clinic says it accepts (Issue 37)

Two new tables; nothing existing changes, so the release before this one runs unaffected.

A private clinic's **self-reported** payment methods (cash, card), the medical schemes it says it
accepts (one row per scheme from a controlled list, with a free-text name for ``other``), a co-payment
notice, and when it last confirmed them. It is a directory tag shown with "reported by the clinic,
please confirm", never an eligibility or claims check (backlog item 1). ``site_payment_profile`` is
keyed by the clinic; scheme rows cascade from it, and it cascades from the clinic. The
``(scheme, site_id)`` index serves discovery's filter under Private.

Only private clinics hold a profile. A check constraint cannot see the clinic's sector, so the rule
is enforced in ``src/modules/sites/payment_profile.py`` (and a clinic that becomes public loses its
profile there).

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create the profile and its scheme rows."""
    op.create_table(
        "site_payment_profile",
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("accepts_cash", sa.Boolean(), nullable=False),
        sa.Column("accepts_card", sa.Boolean(), nullable=False),
        sa.Column("copay_notice", sa.Text(), nullable=True),
        # Business time, Africa/Johannesburg: when the clinic last saved or re-confirmed it.
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        # No foreign key: the confirmation outlives the account of whoever made it.
        sa.Column("confirmed_by", sa.String(length=36), nullable=True),
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
            name=op.f("fk_site_payment_profile_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("site_id", name=op.f("pk_site_payment_profile")),
        schema=SCHEMA,
    )
    op.create_table(
        "site_payment_medical_aid",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("scheme", sa.String(length=32), nullable=False),
        sa.Column("other_name", sa.String(length=80), nullable=True),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site_payment_profile.site_id"],
            name=op.f("fk_site_payment_medical_aid_site_id_site_payment_profile"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_site_payment_medical_aid")),
        sa.UniqueConstraint(
            "site_id", "scheme", name="uq_site_payment_medical_aid_site_id"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_site_payment_medical_aid_scheme",
        "site_payment_medical_aid",
        ["scheme", "site_id"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop both tables. Reversible: the filter is behind PAYMENT_FILTER_ENABLED anyway."""
    op.drop_index(
        "ix_clinicq_site_payment_medical_aid_scheme",
        table_name="site_payment_medical_aid",
        schema=SCHEMA,
    )
    op.drop_table("site_payment_medical_aid", schema=SCHEMA)
    op.drop_table("site_payment_profile", schema=SCHEMA)
