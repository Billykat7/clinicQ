"""The discovery list page, tested through the data it renders (Issue 32).

``docs/IDE/RULES/testing-strategy.mdc`` forbids asserting rendered HTML, so the page's decisions are
tested where they are made: :func:`src.web.discover.discover_page` and :func:`results_view` return
dataclasses, and the routes are tested by status code and header. The browser half (the swap
itself, Slow 3G, a keyboard-only run and the accessibility check) is in the pull request.

Runs against PostgreSQL + PostGIS, because the page calls the real discovery service.
"""

from collections.abc import Collection, Mapping
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy.orm import Session
from starlette import status

from src.commons.enums import DiscoverySort, SectorFilter, SiteSector
from src.commons.time import APP_TIMEZONE
from src.database.models import Queue
from src.modules.discovery.areas import search_areas
from src.modules.discovery.service import MAX_RADIUS_M, find_nearby_sites
from src.modules.queues.live import QueueReading
from src.web.discover import (
    HX_PUSH_URL,
    SECTOR_BADGES,
    Origin,
    discover_page,
    page_href,
    results_view,
)
from tests.integration.discovery.conftest import JOHANNESBURG

pytestmark = pytest.mark.postgres

_TUESDAY_10AM = datetime(2026, 9, 15, 10, 0, tzinfo=APP_TIMEZONE)
_HTMX = {"HX-Request": "true"}


def _query(href: str) -> dict[str, list[str]]:
    """The query parameters of an address."""
    return parse_qs(urlsplit(href).query)


# --------------------------------------------------------------------------------------
# Declining location is never a dead end
# --------------------------------------------------------------------------------------


def test_with_nowhere_to_search_from_the_page_offers_location_and_the_suburb_search(
    directory: SimpleNamespace,
) -> None:
    """The first visit: the prompt and the suburb search together, and no list yet."""
    with directory.session() as db:
        page = discover_page(db)
    assert page.offer_location is True
    assert page.results is None and page.problem is None
    assert directory.client.get("/discover").status_code == status.HTTP_200_OK


def test_declining_location_leads_to_the_area_search_and_then_to_clinics(
    directory: SimpleNamespace,
) -> None:
    """The decline path end to end, as data: typed suburb, a suggestion, its link, a list."""
    with directory.session() as db:
        typed = discover_page(db, q="Soweeto")
        soweto = typed.suggestions[0]
        chosen = discover_page(db, area_id=soweto.area_id, moment=_TUESDAY_10AM)

    assert soweto.name == "Soweto"
    # Each suggestion is a plain link to the page with its area: a tap, Enter, or no JavaScript.
    assert (
        directory.client.get(f"/discover?area_id={soweto.area_id}").status_code == 200
    )
    assert chosen.results is not None
    assert chosen.offer_location is False
    assert [card.slug for card in chosen.results.cards][:2] == [
        "mandela-sisulu-clinic",
        "mofolo-south-clinic",
    ]
    assert chosen.results.approximate_note is not None


@pytest.mark.parametrize(
    ("params", "problem"),
    [
        ({"lat": 28.04, "lon": -26.2}, "outside South Africa"),
        ({"area_id": "0199b0c0-0000-7000-8000-000000000000"}, "no longer listed"),
    ],
)
def test_an_unusable_origin_says_why_and_still_offers_the_search(
    directory: SimpleNamespace, params: dict[str, object], problem: str
) -> None:
    """A swapped coordinate or a stale area link is a sentence above the search, not an error page."""
    with directory.session() as db:
        page = discover_page(db, **params)  # type: ignore[arg-type]
    assert page.problem is not None and problem in page.problem
    assert page.results is None and page.offer_location is True
    assert directory.client.get("/discover", params=params).status_code == 200


def test_a_position_is_rounded_before_anything_uses_it(
    directory: SimpleNamespace,
) -> None:
    """Three decimal places, about 100 m: the URLs the page builds never carry a precise fix."""
    with directory.session() as db:
        page = discover_page(db, lat=-26.2051234, lon=28.0409876)
    assert page.origin.latitude == -26.205 and page.origin.longitude == 28.041
    assert page.results is not None
    assert _query(page.results.href()) == {"lat": ["-26.205"], "lon": ["28.041"]}


# --------------------------------------------------------------------------------------
# The toggle re-renders the list without a page load
# --------------------------------------------------------------------------------------


def test_the_toggle_swaps_a_fragment_and_keeps_the_address_bar_true(
    directory: SimpleNamespace,
) -> None:
    """htmx gets the fragment and ``HX-Push-Url``; a browser without htmx gets the full page."""
    params = {
        "lat": JOHANNESBURG.latitude,
        "lon": JOHANNESBURG.longitude,
        "sector": "public",
    }
    swapped = directory.client.get("/discover/results", params=params, headers=_HTMX)
    plain = directory.client.get(
        "/discover/results", params=params, follow_redirects=False
    )

    assert swapped.status_code == status.HTTP_200_OK
    assert swapped.headers["content-type"].startswith("text/html")
    pushed = swapped.headers[HX_PUSH_URL]
    assert urlsplit(pushed).path == "/discover"
    assert _query(pushed)["sector"] == ["public"]
    assert plain.status_code == status.HTTP_303_SEE_OTHER
    assert plain.headers["location"] == pushed


def test_the_suburb_typeahead_answers_htmx_and_falls_back_to_the_page(
    directory: SimpleNamespace,
) -> None:
    """Suggestions as a fragment for htmx; the full page with them on it otherwise."""
    swapped = directory.client.get(
        "/discover/areas", params={"q": "sowe"}, headers=_HTMX
    )
    plain = directory.client.get(
        "/discover/areas", params={"q": "sowe"}, follow_redirects=False
    )
    assert swapped.status_code == 200
    assert plain.status_code == status.HTTP_303_SEE_OTHER
    assert plain.headers["location"] == "/discover?q=sowe"


@pytest.mark.parametrize("sector", list(SectorFilter))
def test_each_sector_position_shows_only_its_own_clinics(
    directory: SimpleNamespace, sector: SectorFilter
) -> None:
    """The cards behind Public, Private and All carry only the matching badges."""
    with directory.session() as db:
        page = discover_page(db, lat=-26.2, lon=28.02, sector=sector, radius_m=20_000)
    assert page.results is not None and page.results.cards
    shown = {card.badge for card in page.results.cards}
    if sector.sector is None:
        assert shown == set(SECTOR_BADGES.values())
    else:
        assert shown == {SECTOR_BADGES[sector.sector]}
    assert [c.selected for c in page.sector_choices].count(True) == 1


def test_the_filter_bar_carries_sector_radius_and_sort(
    directory: SimpleNamespace,
) -> None:
    """Every group, each with exactly one option chosen, matching the request (the list/map view
    joined them in Issue 33)."""
    with directory.session() as db:
        page = discover_page(
            db, lat=-26.2, lon=28.02, radius_m=5_000, sort=DiscoverySort.SHORTEST_QUEUE
        )
    groups = {group.name: group for group in page.filter_groups}
    assert list(groups) == ["sector", "radius_m", "sort", "view"]
    chosen = {
        name: [c.value for c in group.choices if c.selected]
        for name, group in groups.items()
    }
    assert chosen == {
        "sector": ["all"],
        "radius_m": ["5000"],
        "sort": ["shortest_queue"],
        "view": ["list"],
    }


# --------------------------------------------------------------------------------------
# Designed empty and error states
# --------------------------------------------------------------------------------------


def test_an_empty_list_names_the_radius_and_offers_the_next_one(
    directory: SimpleNamespace,
) -> None:
    """Nothing private within 2 km of Soweto: say so, and link to 5 km."""
    with directory.session() as db:
        soweto = search_areas(db, "Soweto")[0]
        page = discover_page(
            db, area_id=soweto.area_id, sector=SectorFilter.PRIVATE, radius_m=2_000
        )
    assert page.results is not None and page.results.cards == ()
    empty = page.results.empty
    assert empty is not None
    assert empty.title == "No private clinics within 2 km"
    assert empty.body == "Try a wider radius."
    assert empty.action_label == "Search within 5 km"
    assert _query(empty.action_href or "")["radius_m"] == ["5000"]


def test_at_the_widest_radius_the_empty_state_suggests_something_else(
    directory: SimpleNamespace,
) -> None:
    """Nothing private within 50 km of Pietermaritzburg: no wider radius to offer, so say what to try."""
    with directory.session() as db:
        pmb = search_areas(db, "Pietermaritzburg")[0]
        page = discover_page(
            db, area_id=pmb.area_id, sector=SectorFilter.PRIVATE, radius_m=MAX_RADIUS_M
        )
    assert page.results is not None and page.results.cards == ()
    empty = page.results.empty
    assert empty is not None
    assert empty.title == "No private clinics within 50 km"
    assert empty.action_href is None
    assert "Try another suburb" in empty.body


def test_a_swap_with_nowhere_to_search_from_is_refused_so_htmx_keeps_the_old_list(
    directory: SimpleNamespace,
) -> None:
    """A 422 is not swapped by htmx, and discover.js shows the error state with Try again."""
    refused = directory.client.get("/discover/results", headers=_HTMX)
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert HX_PUSH_URL not in refused.headers


# --------------------------------------------------------------------------------------
# What each card says, and the sort
# --------------------------------------------------------------------------------------


def test_each_card_leads_with_distance_queue_and_open_state(
    directory: SimpleNamespace,
) -> None:
    """Hillbrow on a Tuesday morning: open, distance exact from a position, queue counted (empty)."""
    with directory.session() as db:
        page = discover_page(db, lat=-26.205, lon=28.04, moment=_TUESDAY_10AM)
    assert page.results is not None
    card = page.results.cards[0]
    assert card.slug == "hillbrow-chc"
    assert card.badge == SECTOR_BADGES[SiteSector.PUBLIC]
    assert card.distance_label == "1.4 km" and card.distance_is_approximate is False
    assert card.is_open is True and card.open_label == "Open now"
    # A real count since Issue 39: nobody has a ticket, so nobody is waiting, with the count's age.
    assert card.queue_label.startswith("No one waiting, counted ")
    assert card.travel_label.startswith("About ")
    assert (
        page.results.summary == "5 clinics within 10 km"
        or page.results.summary.endswith("within 10 km")
    )


def test_the_shortest_queue_sort_puts_unmeasured_queues_last(
    directory: SimpleNamespace,
) -> None:
    """Measured lengths ascending, then every clinic nobody counted, nearest first among those."""
    with directory.session() as db:
        ids = directory.ids

    def reader(db: Session, queues: Collection[Queue]) -> Mapping[str, QueueReading]:
        lengths = {ids["medicross-randburg"]: 1, ids["mandela-sisulu-clinic"]: 4}
        return {queue.id: QueueReading(lengths.get(queue.site_id)) for queue in queues}

    with directory.session() as db:
        result = find_nearby_sites(
            db,
            JOHANNESBURG,
            radius_m=20_000,
            sort=DiscoverySort.SHORTEST_QUEUE,
            reader=reader,
        )
    slugs = [clinic.slug for clinic in result.clinics]
    assert slugs[:2] == ["medicross-randburg", "mandela-sisulu-clinic"]
    unmeasured = slugs[2:]
    distances = {c.slug: c.distance_m for c in result.clinics}
    assert [distances[s] for s in unmeasured] == sorted(
        distances[s] for s in unmeasured
    )


def test_show_more_pages_through_the_same_search(directory: SimpleNamespace) -> None:
    """The next page's address is the same search at the next offset, until there is none."""
    with directory.session() as db:
        first = find_nearby_sites(db, JOHANNESBURG, radius_m=20_000, limit=3)
        rest = find_nearby_sites(db, JOHANNESBURG, radius_m=20_000, limit=3, offset=3)
    origin = Origin(latitude=JOHANNESBURG.latitude, longitude=JOHANNESBURG.longitude)
    first_view, rest_view = results_view(first, origin), results_view(rest, origin)
    assert first_view.more_href is not None
    assert _query(first_view.more_href)["offset"] == ["3"]
    assert rest_view.more_href is None
    assert page_href(origin, SectorFilter.ALL, DiscoverySort.NEAREST, 10_000) == (
        f"/discover?lat={JOHANNESBURG.latitude}&lon={JOHANNESBURG.longitude}"
    )
