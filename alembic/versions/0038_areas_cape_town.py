"""areas: Cape Town's suburbs and townships, so a search from one finds the Cape Town clinics (Issue 23 follow-up)

Revision ``0014`` seeded the places a patient searches from in Gauteng and KwaZulu-Natal, the two provinces the
demo directory then spanned. The directory now has fifteen Cape Town clinics (``scripts/db/demo_dataset.py``),
so this revision adds the places around them. No table changes.

**The dataset, its source and its licence.** ``alembic/data/0038_areas_cape_town.csv``: **513 places**, the
OpenStreetMap nodes tagged ``place`` = city, town, suburb, village, quarter or neighbourhood inside the City of
Cape Town, with its OSM element in ``osm_ref``, extracted through the Overpass API on 2026-09-15 (OSM base
timestamps 2026-09-15T17:39Z for the places, 2026-07-28T02:16Z for the boundary). Every place's municipality is
the City of Cape Town. Of the 527 nodes, the Prince Edward Islands' (inside the City's boundary, outside the
country a search can answer) are left out, and one place is kept per name, because a suggestion labelled
"Riverside, City of Cape Town" twice could not be chosen between: 13 names were mapped twice, most of them the
same place. Alternative names are the node's own name tags plus the reviewed everyday spellings in
``scripts/db/build_area_dataset.py`` (``COMMON_NAMES``: "Elsies River", "Gugulethu", "Mitchell's Plain",
"CPT"). That script rebuilds the CSV from the saved extract and records both queries.

    © OpenStreetMap contributors. Available under the Open Database Licence (ODbL) 1.0:
    https://www.openstreetmap.org/copyright

A downgrade deletes exactly these places, by ``osm_ref``; their names and any patient's recent searches from
them go with them (``ON DELETE CASCADE``).

Revision ID: 0038
Revises: 0037
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

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value

#: The dataset this revision seeds. Frozen with the revision: a later dataset is a later migration.
DATASET = Path(__file__).resolve().parents[1] / "data" / "0038_areas_cape_town.csv"

#: How many rows each bulk insert or delete carries.
_BATCH = 500


def _rows() -> list[dict[str, str]]:
    """The dataset's rows, in file order."""
    with DATASET.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def upgrade() -> None:
    """Seed the Cape Town places into ``area`` and their names into ``area_name``, as ``0014`` did."""
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
    for row in _rows():
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
                    latitude=float(row["latitude"]), longitude=float(row["longitude"])
                ),
            }
        )
        # One row per distinct folded key: "IKapa" and "iKapa" are the same search.
        keys: dict[str, tuple[str, bool]] = {}
        spellings = [row["name"], *filter(None, row["alternative_names"].split(";"))]
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


def downgrade() -> None:
    """Delete exactly the places this revision added; their names and recent searches cascade."""
    area = sa.table("area", sa.column("osm_ref", sa.String()), schema=SCHEMA)
    refs = [row["osm_ref"] for row in _rows()]
    for start in range(0, len(refs), _BATCH):
        op.execute(
            sa.delete(area).where(area.c.osm_ref.in_(refs[start : start + _BATCH]))
        )
