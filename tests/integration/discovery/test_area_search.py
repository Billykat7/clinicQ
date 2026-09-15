"""Searching from a suburb when there is no GPS, against PostgreSQL + PostGIS + pg_trgm (Issue 34).

Every test runs on a database migrated to ``head``, so the areas are the ones migration ``0014``
actually seeds from OpenStreetMap, not fixtures written to pass. One test per acceptance criterion:

* declining GPS still returns results, via a typed suburb name;
* the typeahead tolerates a **misspelling** ("Soweeto") and common **alternative names**;
* the **same service call** backs the web, USSD and WhatsApp area search;
* the area data is **seeded by migration** with its source documented;
* distances from an area centroid are **labelled as approximate**;
* selecting a **recently used area** takes one interaction.
"""

import csv
import dataclasses
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from starlette import status

from src.commons.enums import AreaKind, DistanceBasis, SaProvince
from src.commons.geo import within_operating_area
from src.database.models import Area, AreaName
from src.modules.discovery import areas
from src.modules.discovery.areas import RECENT_AREAS_KEPT, recent_areas, search_areas
from src.modules.discovery.service import AreaOrigin, find_nearby_sites

pytestmark = pytest.mark.postgres

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION = _ROOT / "alembic" / "versions" / "0014_areas_seed.py"
_DATASET = _ROOT / "alembic" / "data" / "0014_areas.csv"
#: Cape Town's places (Issue 23 follow-up), seeded by their own revision.
_CAPE_TOWN_MIGRATION = _ROOT / "alembic" / "versions" / "0038_areas_cape_town.py"
_CAPE_TOWN_DATASET = _ROOT / "alembic" / "data" / "0038_areas_cape_town.csv"
_AREAS = "/api/v1/clinics/areas"
_NEARBY = "/api/v1/clinics/nearby"


def _names(found: list[areas.AreaSummary]) -> list[str]:
    """The suggested places' own names, in order."""
    return [area.name for area in found]


# --------------------------------------------------------------------------------------
# Misspellings and alternative names
# --------------------------------------------------------------------------------------


def test_a_misspelt_suburb_is_still_found(directory: SimpleNamespace) -> None:
    """The spec's own example: "Soweeto" suggests Soweto first."""
    response = directory.client.get(_AREAS, params={"q": "Soweeto"})
    assert response.status_code == status.HTTP_200_OK, response.text
    first = response.json()["items"][0]
    assert first["name"] == "Soweto"
    assert first["province"] == SaProvince.GAUTENG.value
    assert first["municipality"] == "City of Johannesburg Metropolitan Municipality"


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("Mamelody", "Mamelodi"),
        ("Katlehongg", "Katlehong"),
        ("Pietermaritsburg", "Pietermaritzburg"),
        ("umlazzi", "Umlazi"),
        ("HILBROW", "Hillbrow"),
        ("kwa-thema", "KwaThema"),
    ],
)
def test_common_misspellings_case_and_punctuation_are_forgiven(
    directory: SimpleNamespace, typed: str, expected: str
) -> None:
    """A doubled letter, a swapped consonant, capitals from a USSD handset, a hyphen."""
    with directory.session() as db:
        assert _names(search_areas(db, typed))[0] == expected


@pytest.mark.parametrize(
    ("typed", "place"),
    [
        ("Tembisa", "Thembisa"),
        ("Tokoza", "Thokoza"),
        ("Joburg", "Johannesburg"),
        ("Tshwane", "Pretoria"),
        ("eThekwini", "Durban"),
        ("Maritzburg", "Pietermaritzburg"),
        ("Gugulethu", "Guguletu"),
        ("Kaapstad", "Cape Town"),
    ],
)
def test_alternative_names_find_the_place_and_say_which_name_matched(
    directory: SimpleNamespace, typed: str, place: str
) -> None:
    """An everyday or older name finds the place, and ``matched_name`` says it was an alternative."""
    item = directory.client.get(_AREAS, params={"q": typed}).json()["items"][0]
    assert item["name"] == place
    assert item["matched_name"] == typed


def test_the_typeahead_settles_on_the_place_after_a_few_letters(
    directory: SimpleNamespace,
) -> None:
    """A prefix outranks a fuzzy match, so "sowe" is already Soweto."""
    with directory.session() as db:
        assert _names(search_areas(db, "sowe"))[0] == "Soweto"
        assert search_areas(db, "s") == []
        assert search_areas(db, " - ") == []


def test_same_named_places_are_told_apart_by_municipality(
    directory: SimpleNamespace,
) -> None:
    """Two Riversides: both suggested, each labelled with its own municipality."""
    items = directory.client.get(_AREAS, params={"q": "Riverside", "limit": 20}).json()[
        "items"
    ]
    riversides = [item for item in items if item["name"] == "Riverside"]
    assert len(riversides) >= 2
    assert len({item["label"] for item in riversides}) == len(riversides)
    assert all(item["label"].startswith("Riverside, ") for item in riversides)


def test_a_province_narrows_the_suggestions(directory: SimpleNamespace) -> None:
    """Melville exists in more than one province; asking for KwaZulu-Natal leaves out Johannesburg's."""
    with directory.session() as db:
        everywhere = search_areas(db, "Melville", limit=10)
        kzn = search_areas(db, "Melville", limit=10, province=SaProvince.KWAZULU_NATAL)
    assert {a.province for a in everywhere} >= {
        SaProvince.GAUTENG,
        SaProvince.KWAZULU_NATAL,
    }
    assert kzn and {a.province for a in kzn} == {SaProvince.KWAZULU_NATAL}


# --------------------------------------------------------------------------------------
# Declining GPS still returns results, labelled approximate
# --------------------------------------------------------------------------------------


def test_declining_gps_still_returns_results_via_a_typed_suburb(
    directory: SimpleNamespace,
) -> None:
    """Type a suburb, pick the first suggestion, search: the Soweto clinics, with no position at all."""
    area = directory.client.get(_AREAS, params={"q": "Soweeto"}).json()["items"][0]
    page = directory.client.get(
        _NEARBY, params={"area_id": area["id"], "radius_m": 10_000}
    ).json()

    slugs = [item["slug"] for item in page["items"]]
    assert slugs[:2] == ["mandela-sisulu-clinic", "mofolo-south-clinic"]
    assert page["origin_area"]["name"] == "Soweto"
    assert page["distance_basis"] == DistanceBasis.AREA_CENTROID.value
    assert page["origin"] == area["centroid"]


def test_distances_from_an_area_centroid_are_labelled_approximate_everywhere(
    directory: SimpleNamespace,
) -> None:
    """Every result from an area says so, in the flag and in the words; a GPS search does not."""
    soweto = directory.client.get(_AREAS, params={"q": "Soweto"}).json()["items"][0]
    from_area = directory.client.get(
        _NEARBY, params={"area_id": soweto["id"], "radius_m": 20_000}
    ).json()["items"]
    from_gps = directory.client.get(
        _NEARBY, params={"lat": -26.2350, "lon": 27.9060, "radius_m": 20_000}
    ).json()["items"]

    assert from_area and from_gps
    for item in from_area:
        assert item["distance_is_approximate"] is True
        assert item["distance_label"].startswith("about ")
        assert item["distance_label"].endswith("from the middle of Soweto")
    for item in from_gps:
        assert item["distance_is_approximate"] is False
        assert "about" not in item["distance_label"]

    # The service's own result carries the same label, so a USSD adapter cannot drop it either.
    with directory.session() as db:
        result = find_nearby_sites(db, AreaOrigin(area_id=soweto["id"]))
    assert all(c.distance_basis.approximate for c in result.clinics)
    assert all(c.distance_label.startswith("about ") for c in result.clinics)


def test_a_search_names_exactly_one_origin(directory: SimpleNamespace) -> None:
    """A position and an area together, or neither, is refused; an unknown area is a 404."""
    soweto = directory.client.get(_AREAS, params={"q": "Soweto"}).json()["items"][0]
    both = directory.client.get(
        _NEARBY, params={"lat": -26.2, "lon": 27.9, "area_id": soweto["id"]}
    )
    neither = directory.client.get(_NEARBY)
    half = directory.client.get(_NEARBY, params={"lat": -26.2})
    unknown = directory.client.get(
        _NEARBY, params={"area_id": "0199b0c0-0000-7000-8000-000000000000"}
    )
    assert both.status_code == neither.status_code == half.status_code == 422
    assert unknown.status_code == status.HTTP_404_NOT_FOUND


# --------------------------------------------------------------------------------------
# The same service call on every channel
# --------------------------------------------------------------------------------------


def _ussd_area_menu(db: object, typed: str) -> str:
    """What Issue 73's USSD adapter will do, as a text-only channel does it: no GPS, no HTML."""
    suggestions = search_areas(db, typed, limit=5)  # type: ignore[arg-type]
    return "\n".join(f"{n}. {a.label}" for n, a in enumerate(suggestions, start=1))


def _ussd_clinics_for(db: object, choice: areas.AreaSummary) -> list[str]:
    """The next USSD screen: clinics near the chosen area, with the approximate label."""
    result = find_nearby_sites(db, AreaOrigin(area_id=choice.area_id), limit=5)  # type: ignore[arg-type]
    return [f"{c.name} {c.distance_label}" for c in result.clinics]


def test_the_same_service_call_backs_web_ussd_and_whatsapp(
    directory: SimpleNamespace,
) -> None:
    """The web API and a text-only adapter get the same places and the same clinics, in the same order.

    The adapter calls :func:`search_areas` and :func:`find_nearby_sites` and nothing else, which is
    the whole of what a USSD or WhatsApp menu may do; the source guard in
    ``tests/unit/discovery/test_one_area_search.py`` is what stops a second implementation appearing.
    """
    web_places = directory.client.get(
        _AREAS, params={"q": "Soweeto", "limit": 5}
    ).json()["items"]
    with directory.session() as db:
        menu = _ussd_area_menu(db, "SOWEETO")
        choice = search_areas(db, "SOWEETO", limit=5)[0]
        ussd_clinics = _ussd_clinics_for(db, choice)

    assert menu.splitlines() == [
        f"{n}. {item['label']}" for n, item in enumerate(web_places, start=1)
    ]
    web_clinics = directory.client.get(
        _NEARBY, params={"area_id": web_places[0]["id"], "limit": 5}
    ).json()["items"]
    assert ussd_clinics == [f"{c['name']} {c['distance_label']}" for c in web_clinics]


# --------------------------------------------------------------------------------------
# Seeded by migration, with its source documented
# --------------------------------------------------------------------------------------


def test_area_data_is_seeded_by_migration_with_its_source_documented(
    directory: SimpleNamespace,
) -> None:
    """The migrated database holds every row of both datasets; each migration names source and licence."""
    with _DATASET.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with _CAPE_TOWN_DATASET.open(encoding="utf-8", newline="") as handle:
        cape_town = list(csv.DictReader(handle))
    with directory.session() as db:
        stored = db.execute(select(func.count()).select_from(Area)).scalar_one()
        names = db.execute(select(func.count()).select_from(AreaName)).scalar_one()
        by_province = dict(
            db.execute(
                select(Area.province, func.count()).group_by(Area.province)
            ).all()
        )
        centroids = db.execute(select(Area.centroid)).scalars().all()

    assert len(rows) == 2081 and len(cape_town) == 513
    assert stored == len(rows) + len(cape_town)
    assert names > stored  # alternative names are rows of their own
    assert by_province == {"Gauteng": 1160, "KwaZulu-Natal": 921, "Western Cape": 513}
    assert all(within_operating_area(point) for point in centroids)

    docstring = _MIGRATION.read_text(encoding="utf-8")
    for fact in (
        "OpenStreetMap",
        "Open Database Licence (ODbL) 1.0",
        "https://www.openstreetmap.org/copyright",
        "2,081 places",
        "Overpass API",
        "scripts/db/build_area_dataset.py",
    ):
        assert fact in docstring, fact
    cape_town_docstring = _CAPE_TOWN_MIGRATION.read_text(encoding="utf-8")
    for fact in (
        "OpenStreetMap",
        "Open Database Licence (ODbL) 1.0",
        "513 places",
        "Overpass API",
        "scripts/db/build_area_dataset.py",
    ):
        assert fact in cape_town_docstring, fact
    assert all(row["osm_ref"].startswith("node/") for row in (*rows, *cape_town))
    assert {row["municipality"] for row in cape_town} == {"City of Cape Town"}
    # One place per name: a label repeated in one municipality could not be chosen between.
    assert len({row["name"] for row in cape_town}) == len(cape_town)


def test_a_cape_town_suburb_finds_the_cape_town_clinics_near_it(
    directory: SimpleNamespace,
) -> None:
    """Woodstock's centroid is a few hundred metres from Chapel Street Clinic, the nearest listed clinic."""
    with directory.session() as db:
        woodstock = search_areas(db, "Woodstock", province=SaProvince.WESTERN_CAPE)[0]
    assert (woodstock.name, woodstock.municipality) == (
        "Woodstock",
        "City of Cape Town",
    )
    page = directory.client.get(_NEARBY, params={"area_id": woodstock.area_id}).json()
    assert page["items"][0]["slug"] == "chapel-street-clinic"


def test_every_area_kind_in_the_data_is_a_member_of_the_enum() -> None:
    """The CSVs speak the vocabulary the model reads, so no row can fail ``AreaKind(...)``."""
    kinds: set[str] = set()
    for dataset in (_DATASET, _CAPE_TOWN_DATASET):
        with dataset.open(encoding="utf-8", newline="") as handle:
            kinds |= {row["kind"] for row in csv.DictReader(handle)}
    assert kinds <= {kind.value for kind in AreaKind}
    assert importlib.util.find_spec("scripts.db.build_area_dataset") is not None


# --------------------------------------------------------------------------------------
# Recently used areas, one interaction
# --------------------------------------------------------------------------------------


def test_selecting_a_recently_used_area_takes_one_interaction(
    directory: SimpleNamespace,
) -> None:
    """Remember Soweto, then: one read of the recent list, and its first id is the whole search."""
    client, _ = directory.patient_client()
    soweto = client.get(_AREAS, params={"q": "Soweto"}).json()["items"][0]
    remembered = client.put(f"{_AREAS}/recent/{soweto['id']}")
    assert remembered.status_code == status.HTTP_204_NO_CONTENT, remembered.text

    recent = client.get(f"{_AREAS}/recent").json()["items"]
    assert recent[0]["id"] == soweto["id"]
    # The single interaction: the recent item's id, as it stands, is the search.
    page = client.get(_NEARBY, params={"area_id": recent[0]["id"]}).json()
    assert page["origin_area"]["name"] == "Soweto" and page["items"]


def test_recent_areas_keep_the_latest_few_newest_first_without_repeats(
    directory: SimpleNamespace,
) -> None:
    """Six areas in turn, then the first again: five kept, it is on top, and it appears once."""
    _, patient_id = directory.patient_client()
    with directory.session() as db:
        picks = [
            search_areas(db, typed)[0]
            for typed in (
                "Soweto",
                "Hillbrow",
                "Thembisa",
                "Mamelodi",
                "Durban",
                "Umlazi",
            )
        ]
        for pick in picks:
            areas.remember_area(db, patient_id, pick.area_id)
            db.commit()
        areas.remember_area(db, patient_id, picks[1].area_id)
        db.commit()
        kept = recent_areas(db, patient_id)

    assert len(kept) == RECENT_AREAS_KEPT
    assert [a.name for a in kept] == [
        "Hillbrow",
        "Umlazi",
        "Durban",
        "Mamelodi",
        "Thembisa",
    ]
    assert all(dataclasses.is_dataclass(a) for a in kept)


def test_recent_areas_belong_to_a_signed_in_patient_only(
    directory: SimpleNamespace,
) -> None:
    """No session is a 401; another patient's areas never appear in mine."""
    assert directory.client.get(f"{_AREAS}/recent").status_code == 401
    mine, _ = directory.patient_client()
    theirs, _ = directory.patient_client()
    durban = mine.get(_AREAS, params={"q": "Durban"}).json()["items"][0]
    assert theirs.put(f"{_AREAS}/recent/{durban['id']}").status_code == 204
    assert mine.get(f"{_AREAS}/recent").json()["items"] == []
    assert (
        mine.put(f"{_AREAS}/recent/0199b0c0-0000-7000-8000-000000000000").status_code
        == status.HTTP_404_NOT_FOUND
    )
