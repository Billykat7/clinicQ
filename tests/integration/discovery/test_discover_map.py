"""The map view's data: the same search, the same cards, with somewhere to put each pin (Issue 33).

The map itself is JavaScript over Leaflet, and ``docs/IDE/RULES/testing-strategy.mdc`` keeps rendered
output out of the suite, so what is tested here is the contract the map is built on: the map view
is the **same page and the same search** as the list, every card carries its clinic's coordinates
and a directions hand-off, the address bar keeps the view with the filters, and the map knows where
the search started. The browser half (pins equal cards, the preview, a blocked tile host) is in the
pull request.
"""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette import status

from src.commons.enums import SectorFilter
from src.modules.discovery.areas import search_areas
from src.modules.discovery.service import find_nearby_sites
from src.web.discover import HX_PUSH_URL, DiscoverView, discover_page
from tests.integration.discovery.conftest import JOHANNESBURG

pytestmark = pytest.mark.postgres


def test_the_map_view_is_the_list_view_s_search_with_coordinates_on_every_card(
    directory: SimpleNamespace,
) -> None:
    """Same filters, same clinics, same order; each card says where its pin goes."""
    common = {
        "lat": -26.2,
        "lon": 28.02,
        "radius_m": 20_000,
        "sector": SectorFilter.PRIVATE,
    }
    with directory.session() as db:
        listed = discover_page(db, **common)  # type: ignore[arg-type]
        mapped = discover_page(db, view=DiscoverView.MAP, **common)  # type: ignore[arg-type]
        truth = find_nearby_sites(
            db, JOHANNESBURG, radius_m=20_000, sector=SectorFilter.PRIVATE
        )
    assert listed.results is not None and mapped.results is not None
    assert [c.slug for c in mapped.results.cards] == [
        c.slug for c in listed.results.cards
    ]
    by_slug = {clinic.slug: clinic for clinic in truth.clinics}
    for card in mapped.results.cards:
        clinic = by_slug[card.slug]
        assert (card.latitude, card.longitude) == (
            clinic.location.latitude,
            clinic.location.longitude,
        )
        assert card.directions_href.endswith(
            f"destination={clinic.location.latitude}%2C{clinic.location.longitude}"
        )
    assert [c.value for c in mapped.view_choices if c.selected] == ["map"]
    assert {group.name for group in mapped.filter_groups} >= {"sector", "view"}


def test_switching_view_keeps_the_filters_in_the_address(
    directory: SimpleNamespace,
) -> None:
    """``view=map`` rides along with the sector; the list is the default and is left out."""
    params = {"lat": -26.2, "lon": 28.02, "sector": "private", "radius_m": 20_000}
    to_map = directory.client.get(
        "/discover/results",
        params={**params, "view": "map"},
        headers={"HX-Request": "true"},
    )
    to_list = directory.client.get(
        "/discover/results",
        params={**params, "view": "list"},
        headers={"HX-Request": "true"},
    )
    assert to_map.status_code == to_list.status_code == status.HTTP_200_OK
    on_map = parse_qs(urlsplit(to_map.headers[HX_PUSH_URL]).query)
    assert on_map["view"] == ["map"] and on_map["sector"] == ["private"]
    assert "view" not in parse_qs(urlsplit(to_list.headers[HX_PUSH_URL]).query)


def test_the_map_knows_where_the_search_started(directory: SimpleNamespace) -> None:
    """A position is "You"; an area is its middle, at the area's centroid."""
    with directory.session() as db:
        from_position = discover_page(db, lat=-26.205, lon=28.04, view=DiscoverView.MAP)
        soweto = search_areas(db, "Soweto")[0]
        from_area = discover_page(db, area_id=soweto.area_id, view=DiscoverView.MAP)
    assert (from_position.origin_label, from_position.origin_point) == (
        "You",
        (-26.205, 28.04),
    )
    assert from_area.origin_label == "The middle of Soweto"
    assert from_area.origin_point == (
        soweto.centroid.latitude,
        soweto.centroid.longitude,
    )


def test_the_map_says_when_the_list_has_more_than_it_has_loaded(
    directory: SimpleNamespace,
) -> None:
    """Twenty nearest of more: the map names the gap instead of implying it shows every clinic."""
    from src.web.discover import Origin, results_view

    with directory.session() as db:
        page_of_two = find_nearby_sites(db, JOHANNESBURG, radius_m=50_000, limit=2)
        everything = find_nearby_sites(db, JOHANNESBURG, radius_m=50_000, limit=50)
    origin = Origin(latitude=JOHANNESBURG.latitude, longitude=JOHANNESBURG.longitude)
    partial = results_view(page_of_two, origin, view=DiscoverView.MAP)
    complete = results_view(everything, origin, view=DiscoverView.MAP)
    assert partial.map_note == (
        f"The map shows the 2 nearest of {page_of_two.total} clinics, the same ones as the list. "
        "Switch to the list to load more."
    )
    assert complete.map_note is None
