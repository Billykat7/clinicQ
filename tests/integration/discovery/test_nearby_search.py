"""``find_nearby_sites`` and ``GET /api/v1/clinics/nearby`` against PostgreSQL 18 + PostGIS (Issue 31).

Each acceptance criterion has a test named after it:

* a search from a coordinate returns the clinics inside the radius, **nearest first**;
* the query uses the **GiST index**, read from ``EXPLAIN`` of the statement the service builds;
* it completes in **under 200 ms with 500 seeded clinics**, timed;
* only **verified** clinics come back, whatever else is near;
* the service returns **plain data**, never rendered HTML, so a channel adapter can reuse it;
* the **radius is capped** on the server.

Behaviour is asserted from the service's dataclasses and the API's JSON, never from markup
(``.cursor/rules/testing-strategy.mdc``).
"""

import dataclasses
import json
import random
import statistics
import time as clock
from collections.abc import Collection, Iterator, Mapping
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from starlette import status

from src.commons.enums import SaProvince, SectorFilter, SiteSector, SiteStatus
from src.commons.geo import CoordinateOutOfRangeError, Coordinates
from src.commons.time import APP_TIMEZONE
from src.database.models import Queue, Site, SiteClosure
from src.modules.discovery.service import (
    MAX_RADIUS_M,
    NearbyResult,
    find_nearby_sites,
    nearby_statement,
)
from src.modules.queue.snapshot import NoSnapshotCache, set_snapshot_cache
from src.modules.queues.live import LiveQueue, QueueReading
from tests.factories import QueueFactory
from tests.integration.discovery.conftest import JOHANNESBURG, add_verified_clinic

pytestmark = pytest.mark.postgres

_GIST_INDEX = "ix_clinicq_site_location_gist"
#: A Tuesday morning in Johannesburg, inside every demo clinic's weekday hours.
_TUESDAY_10AM = datetime(2026, 9, 15, 10, 0, tzinfo=APP_TIMEZONE)
_NEARBY = "/api/v1/clinics/nearby"


def _params(point: Coordinates = JOHANNESBURG, **extra: object) -> dict[str, object]:
    """Query parameters for a search from ``point``."""
    return {"lat": point.latitude, "lon": point.longitude, **extra}


# --------------------------------------------------------------------------------------
# Within the radius, nearest first
# --------------------------------------------------------------------------------------


def test_a_search_returns_the_clinics_inside_the_radius_nearest_first(
    directory: SimpleNamespace,
) -> None:
    """20 km from the city centre: Hillbrow, Melville, Randburg and Soweto; not Pretoria or Durban."""
    response = directory.client.get(_NEARBY, params=_params(radius_m=20_000))
    assert response.status_code == status.HTTP_200_OK, response.text
    page = response.json()

    slugs = [item["slug"] for item in page["items"]]
    assert slugs == [
        "hillbrow-chc",
        "medicross-meldene",
        "medicross-randburg",
        "mandela-sisulu-clinic",
        "mofolo-south-clinic",
    ]
    distances = [item["distance_m"] for item in page["items"]]
    assert distances == sorted(distances)
    assert all(distance <= 20_000 for distance in distances)
    assert page["total"] == 5
    # Hillbrow CHC is about 1.4 km from the centre; the distance is metres on the spheroid.
    assert 1_300 < distances[0] < 1_500


def test_widening_the_radius_adds_exactly_the_clinics_it_should(
    directory: SimpleNamespace,
) -> None:
    """5 km holds two clinics; 60 km adds Soweto, Randburg and Pretoria, still not Durban."""
    with directory.session() as db:
        close = find_nearby_sites(db, JOHANNESBURG, radius_m=5_100)
        wide = find_nearby_sites(db, JOHANNESBURG, radius_m=MAX_RADIUS_M)
    assert [c.slug for c in close.clinics] == ["hillbrow-chc", "medicross-meldene"]
    assert {c.slug for c in wide.clinics} >= {"laudium-chc", "mofolo-south-clinic"}
    assert not {c.city for c in wide.clinics} & {
        "Durban",
        "Pinetown",
        "Pietermaritzburg",
    }


def test_each_result_carries_distance_travel_time_open_status_and_queues(
    directory: SimpleNamespace,
) -> None:
    """What the list, the map and the menus render, from one call."""
    with directory.session() as db:
        result = find_nearby_sites(db, JOHANNESBURG, moment=_TUESDAY_10AM)
    hillbrow = result.clinics[0]
    assert hillbrow.slug == "hillbrow-chc"
    assert hillbrow.sector is SiteSector.PUBLIC
    assert hillbrow.province is SaProvince.GAUTENG
    # 1.4 km in a straight line, 1.8 km by road: about 24 minutes on foot and 5 by car.
    assert 20 <= hillbrow.travel.walking_minutes <= 30
    assert 3 <= hillbrow.travel.driving_minutes <= 6
    assert hillbrow.open_status.is_open is True
    assert hillbrow.open_status.next_open_at == _TUESDAY_10AM
    assert [q.name for q in hillbrow.queues] == [
        q.name for q in sorted(hillbrow.queues, key=lambda q: q.display_order)
    ]
    assert len(hillbrow.queues) >= 3


def test_the_sector_filter_narrows_to_public_or_private(
    directory: SimpleNamespace,
) -> None:
    """The toggle's three positions, over the API."""
    by_sector = {
        sector: [
            item["sector"]
            for item in directory.client.get(
                _NEARBY, params=_params(radius_m=20_000, sector=sector.value)
            ).json()["items"]
        ]
        for sector in SectorFilter
    }
    assert set(by_sector[SectorFilter.PUBLIC]) == {SiteSector.PUBLIC.value}
    assert set(by_sector[SectorFilter.PRIVATE]) == {SiteSector.PRIVATE.value}
    assert len(by_sector[SectorFilter.ALL]) == len(
        by_sector[SectorFilter.PUBLIC]
    ) + len(by_sector[SectorFilter.PRIVATE])


def test_open_now_keeps_only_the_clinics_open_at_that_moment(
    directory: SimpleNamespace,
) -> None:
    """Issue 24's rules decide: a closure shuts Hillbrow, and Saturday shuts everyone."""
    with directory.session() as db:
        db.add(
            SiteClosure(
                site_id=directory.ids["hillbrow-chc"],
                reason="The water is off.",
                starts_at=_TUESDAY_10AM - timedelta(hours=2),
                ends_at=_TUESDAY_10AM + timedelta(hours=4),
            )
        )
        db.commit()
        everyone = find_nearby_sites(
            db, JOHANNESBURG, radius_m=20_000, moment=_TUESDAY_10AM
        )
        open_only = find_nearby_sites(
            db, JOHANNESBURG, radius_m=20_000, open_now=True, moment=_TUESDAY_10AM
        )
        saturday = find_nearby_sites(
            db,
            JOHANNESBURG,
            radius_m=20_000,
            open_now=True,
            moment=_TUESDAY_10AM + timedelta(days=4),
        )

    closed = next(c for c in everyone.clinics if c.slug == "hillbrow-chc")
    assert closed.open_status.is_open is False
    assert closed.open_status.closure_reason == "The water is off."
    assert closed.open_status.next_open_at == _TUESDAY_10AM.replace(hour=14)
    assert "hillbrow-chc" not in {c.slug for c in open_only.clinics}
    assert open_only.total == everyone.total - 1
    assert saturday.clinics == () and saturday.total == 0


def test_pagination_pages_through_the_same_ordering(directory: SimpleNamespace) -> None:
    """Two pages of two join up into the first page of four, and ``total`` is the whole count."""
    first = directory.client.get(
        _NEARBY, params=_params(radius_m=20_000, limit=2)
    ).json()
    second = directory.client.get(
        _NEARBY, params=_params(radius_m=20_000, limit=2, offset=2)
    ).json()
    whole = directory.client.get(
        _NEARBY, params=_params(radius_m=20_000, limit=4)
    ).json()
    assert [i["slug"] for i in first["items"] + second["items"]] == [
        i["slug"] for i in whole["items"]
    ]
    assert first["total"] == second["total"] == whole["total"] == 5
    beyond = directory.client.get(
        _NEARBY, params=_params(radius_m=20_000, limit=2, offset=50)
    ).json()
    assert beyond["items"] == [] and beyond["total"] == 5


def test_a_coordinate_outside_the_operating_country_is_refused_by_name(
    directory: SimpleNamespace,
) -> None:
    """A swapped pair lands in the Indian Ocean; the 422 says so rather than returning nothing."""
    swapped = directory.client.get(_NEARBY, params={"lat": 28.04, "lon": -26.205})
    assert swapped.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "outside South Africa" in swapped.json()["detail"]
    with directory.session() as db, pytest.raises(CoordinateOutOfRangeError):
        find_nearby_sites(db, Coordinates(latitude=0.0, longitude=0.0))


# --------------------------------------------------------------------------------------
# Only verified clinics
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hidden",
    [
        {"status": SiteStatus.DRAFT},
        {"status": SiteStatus.PENDING_VERIFICATION},
        {"status": SiteStatus.SUSPENDED},
        {"is_active": False},
        {"is_deleted": True},
    ],
    ids=["draft", "pending", "suspended", "switched-off", "deleted"],
)
def test_only_verified_sites_are_returned(
    directory: SimpleNamespace, hidden: dict[str, object]
) -> None:
    """A clinic 100 m from the patient that a patient may not see is not in the answer, at all.

    Its queues are not either: :func:`published_select` refuses them even if the id were passed.
    """
    with directory.session() as db:
        site_id = add_verified_clinic(
            db,
            slug="right-next-door",
            name="Right Next Door",
            location=Coordinates(latitude=-26.20550, longitude=28.04050),
        )
        QueueFactory.create(db, site_id=site_id)
        site = db.get(Site, site_id)
        assert site is not None
        for column, value in hidden.items():
            setattr(
                site, column, value.value if isinstance(value, SiteStatus) else value
            )
        db.commit()

    page = directory.client.get(_NEARBY, params=_params(radius_m=2_000)).json()
    assert [item["slug"] for item in page["items"]] == ["hillbrow-chc"]
    assert page["total"] == 1


# --------------------------------------------------------------------------------------
# Plain data every channel can reuse
# --------------------------------------------------------------------------------------


def test_the_service_returns_data_structures_never_rendered_html(
    directory: SimpleNamespace,
) -> None:
    """Frozen dataclasses all the way down, which a USSD menu renders without a browser.

    The proof is that a text-only adapter (the shape Issue 73's USSD menu will have) can build its
    menu from the result with nothing but attribute access, and that the whole result serialises to
    JSON with no markup anywhere in it.
    """
    with directory.session() as db:
        result = find_nearby_sites(db, JOHANNESBURG, moment=_TUESDAY_10AM)

    assert isinstance(result, NearbyResult)
    assert dataclasses.is_dataclass(result)
    assert all(dataclasses.is_dataclass(clinic) for clinic in result.clinics)
    as_json = json.dumps(dataclasses.asdict(result), default=str)
    assert "<" not in as_json and ">" not in as_json

    ussd_menu = "\n".join(
        f"{n}. {clinic.name} {clinic.distance_m / 1000:.1f}km"
        for n, clinic in enumerate(result.clinics, start=1)
    )
    assert ussd_menu.startswith("1. Hillbrow Community Health Centre 1.4km")


def test_queue_length_is_not_measured_until_tickets_exist_and_is_never_shown_as_zero(
    directory: SimpleNamespace,
) -> None:
    """No ticket table yet (Issue 39): every length is ``null`` over the API, not ``0``."""
    page = directory.client.get(_NEARBY, params=_params(radius_m=2_000)).json()
    hillbrow = page["items"][0]
    assert hillbrow["total_waiting"] is None
    assert hillbrow["queues"] and all(q["waiting"] is None for q in hillbrow["queues"])
    assert all(q["wait_range"] is None for q in hillbrow["queues"])


def test_a_measured_queue_length_travels_intact_to_the_result(
    directory: SimpleNamespace,
) -> None:
    """The reader seam Issues 36 and 39 plug into: counts in, per-queue and clinic totals out."""

    def reader(db: Session, queues: Collection[Queue]) -> Mapping[str, QueueReading]:
        return {queue.id: QueueReading(queue.display_order + 2) for queue in queues}

    with directory.session() as db:
        result = find_nearby_sites(db, JOHANNESBURG, radius_m=2_000, reader=reader)
    hillbrow = result.clinics[0]
    assert [q.waiting for q in hillbrow.queues] == [
        q.display_order + 2 for q in hillbrow.queues
    ]
    assert hillbrow.total_waiting == sum(q.display_order + 2 for q in hillbrow.queues)
    assert isinstance(hillbrow.queues[0], LiveQueue)


# --------------------------------------------------------------------------------------
# The radius cap
# --------------------------------------------------------------------------------------


def test_the_radius_is_capped_server_side(directory: SimpleNamespace) -> None:
    """Asking for 5,000 km searches 50 km, says so, and does not return Durban."""
    response = directory.client.get(
        _NEARBY, params=_params(radius_m=5_000_000, limit=50)
    )
    assert response.status_code == status.HTTP_200_OK
    page = response.json()
    assert page["radius"] == {
        "requested_m": 5_000_000,
        "applied_m": MAX_RADIUS_M,
        "capped": True,
    }
    assert all(item["distance_m"] <= MAX_RADIUS_M for item in page["items"])
    assert not {"red-hill-clinic", "glen-earle-clinic", "medicross-pinetown"} & {
        item["slug"] for item in page["items"]
    }
    # The cap is the service's, not the route's: a channel calling the function directly is capped too.
    with directory.session() as db:
        direct = find_nearby_sites(db, JOHANNESBURG, radius_m=5_000_000)
    assert direct.radius.applied_m == MAX_RADIUS_M and direct.radius.capped


# --------------------------------------------------------------------------------------
# The GiST index and the 200 ms budget, against 500 clinics
# --------------------------------------------------------------------------------------

#: Seeded for repeatability: the same 500 positions every run.
_PERF_SEED = 31
_PERF_CLINICS = 500
#: A box around greater Johannesburg: roughly 55 km by 50 km, dense enough that a 10 km search has
#: more than one page of results, which is the case the budget has to hold for.
_BOX = ((-26.45, -25.95), (27.80, 28.30))


@pytest.fixture
def five_hundred_clinics(migrated_engine: Engine) -> Iterator[Engine]:
    """500 verified clinics spread over Gauteng, each with three queues and weekday hours.

    The queue snapshot runs with no cache, the slow path (Issue 36): every length comes from the
    table or a recount, so the budget holds without Redis's help.
    """
    set_snapshot_cache(NoSnapshotCache())
    rng = random.Random(_PERF_SEED)
    (lat_lo, lat_hi), (lon_lo, lon_hi) = _BOX
    with Session(migrated_engine) as db:
        for n in range(_PERF_CLINICS):
            site_id = add_verified_clinic(
                db,
                slug=f"perf-clinic-{n}",
                name=f"Perf Clinic {n}",
                sector=SiteSector.PRIVATE if n % 4 == 0 else SiteSector.PUBLIC,
                location=Coordinates(
                    latitude=rng.uniform(lat_lo, lat_hi),
                    longitude=rng.uniform(lon_lo, lon_hi),
                ),
            )
            for order in range(3):
                QueueFactory.create(db, site_id=site_id, display_order=order)
        db.commit()
    with migrated_engine.begin() as conn:
        conn.execute(text("ANALYZE clinicq.site"))
        conn.execute(text("ANALYZE clinicq.queue"))
        conn.execute(text("ANALYZE clinicq.site_opening_hours"))
    yield migrated_engine
    set_snapshot_cache(None)


def test_the_query_uses_the_gist_index(five_hundred_clinics: Engine) -> None:
    """``EXPLAIN`` of the statement the service builds names the GiST index.

    Unlike Issue 23's test over eleven clinics, the planner is **not** nudged here: with 500
    analysed rows and the spec's 5 km radius the index is the cheaper plan, and it is chosen on its
    merits.
    The SQL is compiled from :func:`nearby_statement`, so a change to the service cannot leave this
    test reading a query the service no longer runs.
    """
    compiled = nearby_statement(JOHANNESBURG, 5_000, SectorFilter.ALL).compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    with five_hundred_clinics.connect() as conn:
        plan = "\n".join(row[0] for row in conn.execute(text(f"EXPLAIN {compiled}")))
    assert _GIST_INDEX in plan, plan
    assert "Index Scan" in plan or "Bitmap Index Scan" in plan, plan
    assert "Seq Scan on site" not in plan, plan


def test_search_completes_in_under_200_ms_with_500_seeded_clinics(
    five_hundred_clinics: Engine,
) -> None:
    """The whole service call (clinics, schedules, queues, travel and open status) is timed.

    One warm-up call first, so the number measured is a search rather than a connection being
    opened. The median of seven runs is asserted, and the slowest is reported, so one scheduler
    hiccup on a shared CI runner does not fail a query that is comfortably inside the budget.
    """
    timings: list[float] = []
    with Session(five_hundred_clinics) as db:
        find_nearby_sites(db, JOHANNESBURG, moment=_TUESDAY_10AM)
        for _ in range(7):
            started = clock.perf_counter()
            result = find_nearby_sites(db, JOHANNESBURG, moment=_TUESDAY_10AM)
            timings.append(clock.perf_counter() - started)

    assert len(result.clinics) == 20 and result.total > 20
    median_ms = statistics.median(timings) * 1000
    print(  # noqa: T201 — the figure the PR description quotes
        f"\n500 clinics, {result.total} within 10 km: median {median_ms:.1f} ms, "
        f"slowest {max(timings) * 1000:.1f} ms"
    )
    assert median_ms < 200, timings


def test_an_open_now_search_also_stays_inside_the_budget(
    five_hundred_clinics: Engine,
) -> None:
    """``open_now`` evaluates hours over every candidate in the radius, so it is timed separately.

    Timed the way the main budget test is, as the median of seven runs after a warm-up: a single
    measurement on a shared CI runner came in at 226 ms once while the median stays near 60 ms.
    """
    timings: list[float] = []
    with Session(five_hundred_clinics) as db:
        find_nearby_sites(db, JOHANNESBURG, radius_m=MAX_RADIUS_M, open_now=True)
        for _ in range(7):
            started = clock.perf_counter()
            result = find_nearby_sites(
                db,
                JOHANNESBURG,
                radius_m=MAX_RADIUS_M,
                open_now=True,
                moment=_TUESDAY_10AM,
            )
            timings.append(clock.perf_counter() - started)
    median_ms = statistics.median(timings) * 1000
    print(  # noqa: T201
        f"\nopen_now over {result.total} candidates: median {median_ms:.1f} ms, "
        f"slowest {max(timings) * 1000:.1f} ms"
    )
    assert result.total > 100
    assert median_ms < 200, timings


def test_opening_hours_that_exclude_the_moment_are_respected_at_scale(
    five_hundred_clinics: Engine,
) -> None:
    """At 06:00 on a Tuesday, before every clinic's 07:00 opening, nothing is open."""
    with Session(five_hundred_clinics) as db:
        early = find_nearby_sites(
            db,
            JOHANNESBURG,
            radius_m=MAX_RADIUS_M,
            open_now=True,
            moment=_TUESDAY_10AM.replace(hour=6),
        )
    assert early.total == 0
