#!/usr/bin/env python3
"""Build the area dataset migration ``0014`` seeds, from an OpenStreetMap extract (Issue 34).

This is F's tool, kept in the repository so the dataset can be rebuilt and checked rather than
trusted. It reads two saved Overpass API answers and writes ``alembic/data/0014_areas.csv``, which
the migration loads. **Nothing here touches a database or the network**: the extract is fetched by
hand with the two queries below, saved, and passed in, so a rebuild is reproducible from the files.

**Source and licence.** © OpenStreetMap contributors, available under the Open Database Licence
(ODbL) 1.0 <https://www.openstreetmap.org/copyright>. Attribution is required wherever the data is
shown, which is why the discovery pages credit OpenStreetMap, and a derived database must be offered
under the same licence.

**What is extracted.** Every OSM node tagged ``place`` = ``city``, ``town``, ``suburb``, ``village``,
``quarter`` or ``neighbourhood`` with a ``name``, inside the Gauteng and KwaZulu-Natal provincial
boundaries: the two provinces the demo directory spans (``scripts/db/demo_dataset.py``). Each place's
municipality is the local (``admin_level`` 8) or, failing that, metropolitan or district
(``admin_level`` 6) boundary its point falls in, computed here with Shapely.

The two queries, run against ``https://overpass-api.de/api/interpreter``::

    [out:json][timeout:300];
    ( area["name"="Gauteng"]["admin_level"="4"]["boundary"="administrative"];
      area["name"="KwaZulu-Natal"]["admin_level"="4"]["boundary"="administrative"]; )->.provs;
    foreach.provs->.p(
      node(area.p)["place"~"^(city|town|suburb|village|quarter|neighbourhood|township)$"]["name"];
      convert place ::id=id(), ::=::, province=p.u(t["name"]), lat=lat(), lon=lon();
      out; );

    [out:json][timeout:600];
    ( area["name"="Gauteng"]["admin_level"="4"]["boundary"="administrative"];
      area["name"="KwaZulu-Natal"]["admin_level"="4"]["boundary"="administrative"]; )->.provs;
    rel(area.provs)["boundary"="administrative"]["admin_level"~"^(6|8)$"];
    out geom;

**Alternative names** come from two places: the node's own ``alt_name``, ``old_name``,
``official_name``, ``short_name``, ``loc_name`` and ``name:en`` / ``name:zu`` / ``name:xh`` /
``name:af`` / ``name:st`` / ``name:tn`` tags (split on ``;``), and :data:`COMMON_NAMES` below, the
spellings people actually type that OpenStreetMap does not carry. Each entry there names the place
it belongs to by its OSM name and province, and the build fails if that place is not in the extract,
so the list cannot drift into aliases for places that are not there.

Usage::

    python -m scripts.db.build_area_dataset nodes.json bounds.json
"""

import argparse
import csv
import json
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from shapely import Point, Polygon, STRtree
from shapely.ops import polygonize, unary_union

from src.commons.enums import AreaKind, SaProvince

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
#: Where the migration reads the dataset from.
OUTPUT: Final = REPO_ROOT / "alembic" / "data" / "0014_areas.csv"

#: OSM ``place`` values, folded into the kinds a patient recognises.
PLACE_KINDS: Final[dict[str, AreaKind]] = {
    "city": AreaKind.CITY,
    "town": AreaKind.TOWN,
    "township": AreaKind.TOWN,
    "suburb": AreaKind.SUBURB,
    "village": AreaKind.VILLAGE,
    "quarter": AreaKind.NEIGHBOURHOOD,
    "neighbourhood": AreaKind.NEIGHBOURHOOD,
}

#: The OSM name tags read as alternatives, in order.
ALTERNATIVE_NAME_TAGS: Final = (
    "alt_name",
    "old_name",
    "official_name",
    "short_name",
    "loc_name",
    "name:en",
    "name:zu",
    "name:xh",
    "name:af",
    "name:st",
    "name:tn",
)

#: The spellings and names people type that OpenStreetMap does not carry, per place:
#: ``(OSM name, province): alternatives``. Kept short and uncontroversial on purpose; every one is
#: in everyday use on signage, taxi boards or municipal names.
COMMON_NAMES: Final[dict[tuple[str, SaProvince], tuple[str, ...]]] = {
    ("Johannesburg", SaProvince.GAUTENG): ("Joburg", "Jozi", "Egoli", "Jhb"),
    ("Pretoria", SaProvince.GAUTENG): ("Tshwane", "PTA"),
    ("Thembisa", SaProvince.GAUTENG): ("Tembisa",),
    ("Thokoza", SaProvince.GAUTENG): ("Tokoza",),
    ("Katlehong", SaProvince.GAUTENG): ("Kathlehong",),
    ("Alexandra", SaProvince.GAUTENG): ("Alex",),
    ("Soweto", SaProvince.GAUTENG): ("South Western Townships",),
    ("Durban", SaProvince.KWAZULU_NATAL): ("eThekwini", "Durbs"),
    ("Pietermaritzburg", SaProvince.KWAZULU_NATAL): ("Maritzburg", "PMB", "Msunduzi"),
}


@dataclass(frozen=True, slots=True)
class AreaRow:
    """One line of the dataset."""

    osm_ref: str
    name: str
    kind: AreaKind
    municipality: str
    province: SaProvince
    latitude: float
    longitude: float
    alternative_names: tuple[str, ...]


def _rings(
    member_geometry: Iterable[dict[str, Any]],
) -> Iterator[list[tuple[float, float]]]:
    """Yield each outer way of a relation as a list of ``(lon, lat)`` points."""
    for member in member_geometry:
        if member.get("type") == "way" and member.get("role", "outer") in {"outer", ""}:
            points = [(node["lon"], node["lat"]) for node in member.get("geometry", [])]
            if len(points) >= 2:
                yield points


def boundary_polygons(bounds: dict[str, Any]) -> list[tuple[int, str, Polygon]]:
    """``(admin_level, name, polygon)`` for each boundary relation, assembled from its outer ways."""
    out: list[tuple[int, str, Polygon]] = []
    for element in bounds["elements"]:
        tags = element.get("tags", {})
        if element.get("type") != "relation" or "name" not in tags:
            continue
        lines = list(_rings(element.get("members", [])))
        polygons = list(polygonize(lines))
        if not polygons:
            continue
        shape = unary_union(polygons)
        for polygon in getattr(shape, "geoms", [shape]):
            out.append((int(tags["admin_level"]), tags["name"], polygon))
    return out


def _alternatives(tags: dict[str, str], name: str) -> tuple[str, ...]:
    """The node's alternative names, split, trimmed, de-duplicated, never the name itself."""
    seen: dict[str, None] = {}
    for tag in ALTERNATIVE_NAME_TAGS:
        for value in tags.get(tag, "").split(";"):
            value = value.strip()
            if value and value.casefold() != name.casefold():
                seen.setdefault(value, None)
    return tuple(seen)


def build(nodes: dict[str, Any], bounds: dict[str, Any]) -> list[AreaRow]:
    """Turn the two Overpass answers into dataset rows, sorted by province then name.

    Raises:
        ValueError: If a :data:`COMMON_NAMES` entry names a place the extract does not contain.
    """
    polygons = boundary_polygons(bounds)
    tree = STRtree([polygon for _, _, polygon in polygons])
    rows: dict[str, AreaRow] = {}
    for element in nodes["elements"]:
        tags = element["tags"]
        kind = PLACE_KINDS.get(tags.get("place", ""))
        if kind is None:
            continue
        osm_ref = f"node/{element['id']}"
        latitude, longitude = float(tags["lat"]), float(tags["lon"])
        point = Point(longitude, latitude)
        containing = sorted(
            (polygons[i] for i in tree.query(point, predicate="within")),
            key=lambda entry: -entry[0],  # the most local boundary first
        )
        municipality = containing[0][1] if containing else ""
        name = tags["name"].strip()
        province = SaProvince(tags["province"])
        rows[osm_ref] = AreaRow(
            osm_ref=osm_ref,
            name=name,
            kind=kind,
            municipality=municipality,
            province=province,
            latitude=round(latitude, 6),
            longitude=round(longitude, 6),
            alternative_names=_alternatives(tags, name),
        )

    by_name = {(row.name, row.province): row for row in rows.values()}
    for (name, province), extra in COMMON_NAMES.items():
        row = by_name.get((name, province))
        if row is None:
            raise ValueError(
                f"COMMON_NAMES names {name!r} in {province}, which is not in the extract."
            )
        merged = tuple(dict.fromkeys((*row.alternative_names, *extra)))
        rows[row.osm_ref] = AreaRow(
            osm_ref=row.osm_ref,
            name=row.name,
            kind=row.kind,
            municipality=row.municipality,
            province=row.province,
            latitude=row.latitude,
            longitude=row.longitude,
            alternative_names=merged,
        )
    return sorted(
        rows.values(), key=lambda row: (row.province.value, row.name, row.osm_ref)
    )


def write(rows: Iterable[AreaRow], path: Path = OUTPUT) -> int:
    """Write the dataset as CSV; alternative names are ``;``-separated. Returns the row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "osm_ref",
                "name",
                "kind",
                "municipality",
                "province",
                "latitude",
                "longitude",
                "alternative_names",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.osm_ref,
                    row.name,
                    row.kind.value,
                    row.municipality,
                    row.province.value,
                    f"{row.latitude:.6f}",
                    f"{row.longitude:.6f}",
                    ";".join(row.alternative_names),
                ]
            )
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    """Read the two extracts named on the command line and write the dataset."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("nodes", type=Path, help="Overpass answer for the place nodes")
    parser.add_argument(
        "bounds", type=Path, help="Overpass answer for the municipal boundaries"
    )
    args = parser.parse_args(argv)
    rows = build(
        json.loads(args.nodes.read_text(encoding="utf-8")),
        json.loads(args.bounds.read_text(encoding="utf-8")),
    )
    count = write(rows)
    print(f"wrote {count} areas to {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
