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

**Cape Town** (migration ``0038``, ``alembic/data/0038_areas_cape_town.csv``) is a second extract, built
with ``--output``, for the Cape Town clinics the demo directory added: every place node inside the City of
Cape Town, with the metropolitan boundary as its municipality::

    [out:json][timeout:300];
    area["name"="City of Cape Town"]["boundary"="administrative"]["admin_level"="6"]->.ct;
    node(area.ct)["place"~"^(city|town|suburb|village|quarter|neighbourhood|township)$"]["name"];
    convert place ::id=id(), ::=::, province="Western Cape", lat=lat(), lon=lon();
    out;

    [out:json][timeout:600];
    area["name"="City of Cape Town"]["boundary"="administrative"]["admin_level"="6"]->.ct;
    ( rel["name"="City of Cape Town"]["boundary"="administrative"]["admin_level"="6"];
      rel(area.ct)["boundary"="administrative"]["admin_level"="8"]; );
    out geom;

A :data:`COMMON_NAMES` entry is checked against an extract only when its province is in that extract, so
one list serves both. Two rules shape the Cape Town build, added with it (``0014``'s CSV was built before them
and is frozen with its migration):

* **Only places inside the operating country.** The City of Cape Town's boundary includes the Prince Edward
  Islands, 1,700 km south-east; a search there cannot be answered (:func:`src.commons.geo.within_operating_area`).
* **One place per name in a municipality.** A suggestion is labelled "name, municipality", so two places with
  both the same would be two identical choices. Most are the same place mapped twice. The one kept is the
  larger kind (a town over a suburb, a suburb over a neighbourhood), then the older OSM node.

Usage::

    python -m scripts.db.build_area_dataset nodes.json bounds.json
    python -m scripts.db.build_area_dataset ct_nodes.json ct_bounds.json --output alembic/data/0038_areas_cape_town.csv
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
from src.commons.geo import Coordinates, within_operating_area

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

#: Which of two same-named places in one municipality is kept: the lower rank.
KIND_RANK: Final[dict[AreaKind, int]] = {
    AreaKind.CITY: 0,
    AreaKind.TOWN: 1,
    AreaKind.SUBURB: 2,
    AreaKind.VILLAGE: 3,
    AreaKind.NEIGHBOURHOOD: 4,
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
    ("Cape Town", SaProvince.WESTERN_CAPE): ("CPT", "Kaapstad", "iKapa"),
    ("Elsiesriver", SaProvince.WESTERN_CAPE): ("Elsies River", "Elsies Rivier"),
    ("Guguletu", SaProvince.WESTERN_CAPE): ("Gugulethu",),
    ("Mitchells Plain", SaProvince.WESTERN_CAPE): (
        "Mitchell's Plain",
        "Mitchells Plein",
    ),
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


def _one_per_name(rows: Iterable[AreaRow]) -> dict[str, AreaRow]:
    """Keep one place per ``(name, municipality, province)``: the larger kind, then the older OSM node."""
    kept: dict[tuple[str, str, SaProvince], AreaRow] = {}
    for row in rows:
        key = (row.name.casefold(), row.municipality, row.province)
        other = kept.get(key)
        if other is None or _keep_rank(row) < _keep_rank(other):
            kept[key] = row
    return {row.osm_ref: row for row in kept.values()}


def _keep_rank(row: AreaRow) -> tuple[int, int]:
    """How strongly a place is kept over a same-named one: its kind, then its OSM node id."""
    return (KIND_RANK[row.kind], int(row.osm_ref.split("/")[1]))


def build(nodes: dict[str, Any], bounds: dict[str, Any]) -> list[AreaRow]:
    """Turn the two Overpass answers into dataset rows, sorted by province then name.

    Places outside the operating country are left out, and one place is kept per name in a municipality.

    Raises:
        ValueError: If a :data:`COMMON_NAMES` entry for a province in the extract names a place the extract
            does not contain.
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
        if not within_operating_area(
            Coordinates(latitude=latitude, longitude=longitude)
        ):
            continue
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

    rows = _one_per_name(rows.values())
    by_name = {(row.name, row.province): row for row in rows.values()}
    provinces = {row.province for row in rows.values()}
    for (name, province), extra in COMMON_NAMES.items():
        if province not in provinces:
            continue
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
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT,
        help="Where to write the dataset (default: migration 0014's)",
    )
    args = parser.parse_args(argv)
    rows = build(
        json.loads(args.nodes.read_text(encoding="utf-8")),
        json.loads(args.bounds.read_text(encoding="utf-8")),
    )
    output = args.output.resolve()
    count = write(rows, output)
    shown = (
        output.relative_to(REPO_ROOT) if output.is_relative_to(REPO_ROOT) else output
    )
    print(f"wrote {count} areas to {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
