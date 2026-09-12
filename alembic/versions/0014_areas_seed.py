"""area, area_name, patient_recent_area: searching from a suburb instead of a position (Issue 34)

Three new tables and the ``pg_trgm`` extension; nothing existing changes, so the release before this
one runs unaffected.

* ``area`` is one named place (a suburb, township, town or city) with its municipality, province and
  centroid, which is what a search without GPS measures from.
* ``area_name`` is every name a place is found by, folded into ``search_key``
  (:func:`src.commons.search.fold_for_search`) under a GIN trigram index, so "Soweeto" still finds
  Soweto and "Tembisa" finds Thembisa.
* ``patient_recent_area`` is the handful of areas a patient last searched from.

**The dataset, its source and its licence.** ``area`` and ``area_name`` are seeded here from
``alembic/data/0014_areas.csv``: **2,081 places**, 1,160 in Gauteng and 921 in KwaZulu-Natal, the two
provinces the demo directory spans. Every row is an OpenStreetMap node tagged ``place`` = city, town,
suburb, village, quarter or neighbourhood, with its OSM element in ``osm_ref``, extracted through the
Overpass API on 2026-09-13 (OSM base timestamp 2026-09-12T22:08Z). Each place's municipality is the
most local administrative boundary (``admin_level`` 8, else 6) its point falls in, and its
alternative names are the node's own ``alt_name`` / ``old_name`` / ``official_name`` / ``short_name``
/ ``loc_name`` / ``name:<language>`` tags plus a short, reviewed list of everyday spellings
(``scripts/db/build_area_dataset.py``, ``COMMON_NAMES``). That script rebuilds the CSV from the saved
extract and records both Overpass queries.

    © OpenStreetMap contributors. Available under the Open Database Licence (ODbL) 1.0:
    https://www.openstreetmap.org/copyright

ODbL requires attribution wherever the data is shown (the discovery pages credit OpenStreetMap) and
that a database derived from it is offered under the same licence. Prepared and documented by F
(Data & Research); the search over it is A's.

``pg_trgm`` is created in ``public``, like PostGIS in ``0001``, because it is shared with anything else
in the database. A downgrade drops the tables and leaves the extension installed.

Revision ID: 0014
Revises: 0013
"""

import csv
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema
from src.commons.geo import Coordinates
from src.commons.ids import new_id
from src.commons.search import fold_for_search
from src.database.types import PointGeography

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value

#: The dataset this revision seeds. Frozen with the revision: a later dataset is a later migration.
DATASET = Path(__file__).resolve().parents[1] / "data" / "0014_areas.csv"

#: How many rows each bulk insert carries.
_BATCH = 500


def _is_postgres() -> bool:
    """Whether this run is against PostgreSQL (the extension and the GIN index are PG-only)."""
    return op.get_bind().dialect.name == "postgresql"


def _timestamps() -> list[sa.Column]:
    """``created_at`` and ``modified_at``, as every table in this schema has them."""
    return [
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
    ]


def _seed() -> tuple[int, int]:
    """Load the dataset into ``area`` and ``area_name``. Returns ``(areas, names)``."""
    area = sa.table(
        "area",
        sa.column("id", sa.String()),
        sa.column("osm_ref", sa.String()),
        sa.column("name", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("municipality", sa.String()),
        sa.column("province", sa.String()),
        sa.column("centroid", PointGeography()),
        schema=SCHEMA,
    )
    area_name = sa.table(
        "area_name",
        sa.column("id", sa.String()),
        sa.column("area_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("search_key", sa.String()),
        sa.column("is_primary", sa.Boolean()),
        schema=SCHEMA,
    )
    areas: list[dict[str, object]] = []
    names: list[dict[str, object]] = []
    with DATASET.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            area_id = new_id()
            areas.append(
                {
                    "id": area_id,
                    "osm_ref": row["osm_ref"],
                    "name": row["name"],
                    "kind": row["kind"],
                    "municipality": row["municipality"] or None,
                    "province": row["province"],
                    "centroid": Coordinates(
                        latitude=float(row["latitude"]),
                        longitude=float(row["longitude"]),
                    ),
                }
            )
            # One row per distinct folded key: "iGoli" and "EGoli" are the same search.
            keys: dict[str, tuple[str, bool]] = {}
            spellings = [
                row["name"],
                *filter(None, row["alternative_names"].split(";")),
            ]
            for position, spelling in enumerate(spellings):
                key = fold_for_search(spelling)
                if key and key not in keys:
                    keys[key] = (spelling, position == 0)
            names.extend(
                {
                    "id": new_id(),
                    "area_id": area_id,
                    "name": spelling,
                    "search_key": key,
                    "is_primary": is_primary,
                }
                for key, (spelling, is_primary) in keys.items()
            )
    for start in range(0, len(areas), _BATCH):
        op.bulk_insert(area, areas[start : start + _BATCH])
    for start in range(0, len(names), _BATCH):
        op.bulk_insert(area_name, names[start : start + _BATCH])
    return len(areas), len(names)


def upgrade() -> None:
    """Create the three tables and the trigram index, and seed the areas."""
    if _is_postgres():
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")

    op.create_table(
        "area",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("osm_ref", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("municipality", sa.String(length=160), nullable=True),
        sa.Column("province", sa.String(length=32), nullable=False),
        sa.Column("centroid", PointGeography(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_area")),
        sa.UniqueConstraint("osm_ref", name=op.f("uq_area_osm_ref")),
        schema=SCHEMA,
    )
    op.create_table(
        "area_name",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("area_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("search_key", sa.String(length=160), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["area_id"],
            [f"{SCHEMA}.area.id"],
            name=op.f("fk_area_name_area_id_area"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_area_name")),
        sa.UniqueConstraint(
            "area_id", "search_key", name="uq_area_name_area_id_search_key"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_area_name_search_key_trgm",
        "area_name",
        ["search_key"],
        unique=False,
        schema=SCHEMA,
        postgresql_using="gin",
        postgresql_ops={"search_key": "gin_trgm_ops"},
    )
    op.create_table(
        "patient_recent_area",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("patient_id", sa.String(length=36), nullable=False),
        sa.Column("area_id", sa.String(length=36), nullable=False),
        # Business time, Africa/Johannesburg: when the patient last searched from this area.
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            [f"{SCHEMA}.patient.id"],
            name=op.f("fk_patient_recent_area_patient_id_patient"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["area_id"],
            [f"{SCHEMA}.area.id"],
            name=op.f("fk_patient_recent_area_area_id_area"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patient_recent_area")),
        sa.UniqueConstraint(
            "patient_id", "area_id", name="uq_patient_recent_area_patient_id"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_patient_recent_area_patient_used",
        "patient_recent_area",
        ["patient_id", "used_at"],
        unique=False,
        schema=SCHEMA,
    )
    _seed()


def downgrade() -> None:
    """Drop the three tables. The ``pg_trgm`` extension stays: it is shared, like PostGIS."""
    op.drop_index(
        "ix_clinicq_patient_recent_area_patient_used",
        table_name="patient_recent_area",
        schema=SCHEMA,
    )
    op.drop_table("patient_recent_area", schema=SCHEMA)
    op.drop_index(
        "ix_clinicq_area_name_search_key_trgm", table_name="area_name", schema=SCHEMA
    )
    op.drop_table("area_name", schema=SCHEMA)
    op.drop_table("area", schema=SCHEMA)
