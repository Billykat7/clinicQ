"""site: one clinic, with a PostGIS position and the GiST index discovery needs (Issue 23)

One new table; nothing existing changes, so the release before this one runs unaffected.

Two things here that autogenerate would not produce on its own, and that any later revision
touching this table has to keep:

* the **``location`` column is ``geography(Point, 4326)``** on PostgreSQL, and plain text on any
  other dialect. ``src.database.types.PointGeography`` renders both, so the SQLite test database
  takes the same model; the ``geography`` type itself is ``public``'s, installed by the baseline's
  ``CREATE EXTENSION postgis``;
* the **GiST index is created here**, ahead of any data, which is the whole reason the column lands
  in M4 rather than in M5. ``ST_DWithin`` on a geography can only be an index scan with it, and
  adding it to a populated ``site`` table later would mean a rebuild during discovery's milestone.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import SITE_DEFAULT_STATUS, DbSchema
from src.database.types import PointGeography

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create ``site``, its GiST location index and the directory's sector/status index."""
    op.create_table(
        "site",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("sector", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default=SITE_DEFAULT_STATUS.value,
        ),
        # WGS 84 (SRID 4326), so a distance is metres on the spheroid rather than degrees.
        sa.Column("location", PointGeography(), nullable=False),
        sa.Column("address_line", sa.String(length=200), nullable=False),
        sa.Column("suburb", sa.String(length=120), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("province", sa.String(length=32), nullable=False),
        sa.Column("postal_code", sa.String(length=10), nullable=True),
        sa.Column("phone_e164", sa.String(length=20), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_site")),
        sa.UniqueConstraint("slug", name=op.f("uq_site_slug")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_site_location_gist",
        "site",
        ["location"],
        unique=False,
        schema=SCHEMA,
        postgresql_using="gist",
    )
    op.create_index(
        "ix_clinicq_site_sector_status",
        "site",
        ["sector", "status"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the table and both indexes. Reversible: nothing else references ``site`` yet."""
    op.drop_index("ix_clinicq_site_sector_status", table_name="site", schema=SCHEMA)
    op.drop_index("ix_clinicq_site_location_gist", table_name="site", schema=SCHEMA)
    op.drop_table("site", schema=SCHEMA)
