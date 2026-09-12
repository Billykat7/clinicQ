"""site opening hours, public holidays, holiday rules and ad-hoc closures (Issue 24)

Four new tables; nothing existing changes, so the release before this one runs unaffected.

The order they are created in is the order of precedence a reader should have in mind: an ad-hoc
closure beats a holiday rule, which beats the weekly schedule (:mod:`src.modules.sites.hours`).

``public_holiday`` is the one table here with **no** ``site_id``: 16 June is 16 June for every
clinic in the country, and giving each one its own copy would let two clinics disagree about the
calendar. The per-clinic answer lives in ``site_holiday_rule`` instead.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def _timestamps() -> tuple[sa.Column, sa.Column]:
    """The ``created_at`` / ``modified_at`` pair every table in this schema carries."""
    return (
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
    )


def upgrade() -> None:
    """Create the four tables and their indexes."""
    op.create_table(
        "site_opening_hours",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        # 0 = Monday ... 6 = Sunday, as datetime.date.weekday numbers them.
        sa.Column("weekday", sa.Integer(), nullable=False),
        # Wall clock in Africa/Johannesburg. closes_at <= opens_at means the span crosses midnight.
        sa.Column("opens_at", sa.Time(), nullable=False),
        sa.Column("closes_at", sa.Time(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_site_opening_hours_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_site_opening_hours")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_site_opening_hours_site_weekday",
        "site_opening_hours",
        ["site_id", "weekday"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "public_holiday",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("holiday_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        # Set only on the Mondays the Public Holidays Act's Sunday rule creates.
        sa.Column("observed_for", sa.String(length=120), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_public_holiday")),
        sa.UniqueConstraint(
            "holiday_date", name=op.f("uq_public_holiday_holiday_date")
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "site_holiday_rule",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("holiday_date", sa.Date(), nullable=False),
        # Both null means the clinic is closed, said explicitly rather than by omission.
        sa.Column("opens_at", sa.Time(), nullable=True),
        sa.Column("closes_at", sa.Time(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_site_holiday_rule_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_site_holiday_rule")),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_clinicq_site_holiday_rule_site_date",
        "site_holiday_rule",
        ["site_id", "holiday_date"],
        unique=True,
        schema=SCHEMA,
    )

    op.create_table(
        "site_closure",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        # The manager's own words; this is what the patient is shown.
        sa.Column("reason", sa.Text(), nullable=False),
        # Business time, Africa/Johannesburg. ends_at null is "until further notice".
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("announced_by", sa.String(length=36), nullable=True),
        sa.Column("lifted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_site_closure_site_id_site"),
            ondelete="CASCADE",
        ),
        # RESTRICT: a deactivated account is still the author of the closure it announced.
        sa.ForeignKeyConstraint(
            ["announced_by"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_site_closure_announced_by_user"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_site_closure")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_site_closure_site_starts_at",
        "site_closure",
        ["site_id", "starts_at"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the four tables. Reversible: nothing outside Issue 24 references them yet."""
    op.drop_index(
        "ix_clinicq_site_closure_site_starts_at",
        table_name="site_closure",
        schema=SCHEMA,
    )
    op.drop_table("site_closure", schema=SCHEMA)
    op.drop_index(
        "uq_clinicq_site_holiday_rule_site_date",
        table_name="site_holiday_rule",
        schema=SCHEMA,
    )
    op.drop_table("site_holiday_rule", schema=SCHEMA)
    op.drop_table("public_holiday", schema=SCHEMA)
    op.drop_index(
        "ix_clinicq_site_opening_hours_site_weekday",
        table_name="site_opening_hours",
        schema=SCHEMA,
    )
    op.drop_table("site_opening_hours", schema=SCHEMA)
