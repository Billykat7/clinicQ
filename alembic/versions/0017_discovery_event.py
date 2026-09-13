"""discovery_event and site.analytics_enabled: anonymous view-to-join analytics (Issue 38)

One new table and one new column on ``site``; nothing is dropped, so the release before this one runs
unaffected (the column has a server default).

``discovery_event`` records a search, a clinic view, a join started or a join completed, with the
channel, the Johannesburg service day and a **daily-rotating HMAC** of the discovery session. It has
no column that could hold a person's identity or position: no user, patient, phone number, IP address
or coordinate. A search keeps only how it was made (position or area, radius, sector) and how many
clinics it found. ``site_id`` carries no foreign key, so a report outlives a clinic's listing; the
``(site_id, kind, service_day)`` index is the conversion report's read.

``site.analytics_enabled`` (default true) is a clinic's opt-out: while false, no view or join of it is
recorded.

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create ``discovery_event`` and add the clinic's opt-out."""
    op.create_table(
        "discovery_event",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        # Business time, Africa/Johannesburg.
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_day", sa.Date(), nullable=False),
        sa.Column("session_ref", sa.String(length=64), nullable=True),
        sa.Column("site_id", sa.String(length=36), nullable=True),
        sa.Column("sector", sa.String(length=16), nullable=True),
        sa.Column("origin_basis", sa.String(length=16), nullable=True),
        sa.Column("radius_m", sa.Integer(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_discovery_event")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_discovery_event_site_kind_day",
        "discovery_event",
        ["site_id", "kind", "service_day"],
        unique=False,
        schema=SCHEMA,
    )
    op.add_column(
        "site",
        sa.Column(
            "analytics_enabled", sa.Boolean(), nullable=False, server_default="true"
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the opt-out and the events. Reversible, but the recorded history goes with the table."""
    op.drop_column("site", "analytics_enabled", schema=SCHEMA)
    op.drop_index(
        "ix_clinicq_discovery_event_site_kind_day",
        table_name="discovery_event",
        schema=SCHEMA,
    )
    op.drop_table("discovery_event", schema=SCHEMA)
