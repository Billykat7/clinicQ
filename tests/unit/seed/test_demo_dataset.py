"""The demo world is plausible, varied and deterministic (Issue 8).

Pure data, so unit tests. The coordinate checks are coarse on purpose: they catch a swapped
latitude and longitude, a sign error or a clinic filed under the wrong province, which is what a
map (Issue 33) would show wrong. That the points are the actual buildings is recorded in the
dataset (each clinic names its OpenStreetMap element), not re-derived here.
"""

from collections import Counter
from datetime import date, datetime, time, timedelta
from statistics import pstdev

import pytest

from scripts.db.demo_dataset import (
    CLINICS,
    Province,
    SiteStub,
    queues_for,
    ticket_history,
)
from src.commons.enums import SITE_DEFAULT_DISPLAY_MODE, SiteSector, TicketStatus
from src.commons.time import APP_TIMEZONE, business_day_bounds

DAY = date(2026, 9, 11)
NOW = datetime(2026, 9, 11, 11, 30, tzinfo=APP_TIMEZONE)

#: Generous boxes around each province: wrong-hemisphere or swapped coordinates fall far outside.
PROVINCE_BOUNDS = {
    Province.GAUTENG: ((-27.3, -25.1), (27.0, 29.1)),
    Province.KWAZULU_NATAL: ((-31.2, -26.8), (28.8, 33.0)),
    Province.WESTERN_CAPE: ((-34.9, -30.4), (17.7, 23.5)),
}


def test_there_are_at_least_eight_clinics_across_the_provinces_and_sectors() -> None:
    """The spec's minimum, in Gauteng and KwaZulu-Natal, public and private, and Cape Town's clinics."""
    assert len(CLINICS) >= 8
    assert set(Counter(c.province for c in CLINICS)) == {
        Province.GAUTENG,
        Province.KWAZULU_NATAL,
        Province.WESTERN_CAPE,
    }
    assert set(Counter(c.sector for c in CLINICS)) == {
        SiteSector.PUBLIC,
        SiteSector.PRIVATE,
    }


@pytest.mark.parametrize("clinic", CLINICS, ids=lambda c: c.slug)
def test_each_clinic_sits_inside_its_province(clinic: SiteStub) -> None:
    """Latitude and longitude fall in the province the clinic claims."""
    (lat_min, lat_max), (lon_min, lon_max) = PROVINCE_BOUNDS[clinic.province]
    assert lat_min < clinic.latitude < lat_max
    assert lon_min < clinic.longitude < lon_max


def test_clinics_are_distinct_and_traceable() -> None:
    """Unique slugs, names, positions and OpenStreetMap elements; every one starts number-only."""
    for attribute in ("slug", "name", "osm"):
        values = [getattr(c, attribute) for c in CLINICS]
        assert len(values) == len(set(values)), attribute
    assert len({(c.latitude, c.longitude) for c in CLINICS}) == len(CLINICS)
    assert all(c.osm.split("/")[0] in {"node", "way", "relation"} for c in CLINICS)
    assert {c.display_mode for c in CLINICS} == {SITE_DEFAULT_DISPLAY_MODE}


@pytest.mark.parametrize("clinic", CLINICS, ids=lambda c: c.slug)
def test_each_clinic_runs_two_to_four_queues(clinic: SiteStub) -> None:
    """The spec's range, with unique slugs and ticket prefixes within the clinic."""
    queues = queues_for(clinic)
    assert 2 <= len(queues) <= 4
    assert (
        len({q.slug for q in queues}) == len({q.prefix for q in queues}) == len(queues)
    )


def test_ticket_history_is_deterministic() -> None:
    """Same clinic, queue and day: the same tickets, so a demo and a test agree."""
    clinic = CLINICS[0]
    queue = queues_for(clinic)[1]
    assert ticket_history(clinic, queue, DAY, NOW) == ticket_history(
        clinic, queue, DAY, NOW
    )


@pytest.mark.parametrize("clinic", CLINICS, ids=lambda c: c.slug)
def test_every_ticket_is_well_formed(clinic: SiteStub) -> None:
    """Sequenced from 1, SAST-aware, on the day, before now, and called in sequence order."""
    start, end = business_day_bounds(DAY)
    for queue in queues_for(clinic):
        tickets = ticket_history(clinic, queue, DAY, NOW)
        assert [t.sequence for t in tickets] == list(range(1, len(tickets) + 1))
        assert all(t.number.startswith(queue.prefix) for t in tickets)
        for t in tickets:
            assert t.joined_at.tzinfo is APP_TIMEZONE
            assert start <= t.joined_at <= NOW < end
            if t.called_at is not None:
                assert t.called_at >= t.joined_at
            if t.status is TicketStatus.DONE:
                assert t.completed_at is not None and t.completed_at <= NOW
        called = [t.called_at for t in tickets if t.called_at is not None]
        assert called == sorted(called)


def test_the_day_uses_every_outcome() -> None:
    """Done, no-show, cancelled and still waiting all occur, so every screen has a case to show."""
    statuses = Counter(
        t.status
        for clinic in CLINICS
        for queue in queues_for(clinic)
        for t in ticket_history(clinic, queue, DAY, NOW)
    )
    for status in (
        TicketStatus.DONE,
        TicketStatus.NO_SHOW,
        TicketStatus.CANCELLED,
        TicketStatus.WAITING,
    ):
        assert statuses[status] > 0, status


def test_one_clinic_has_enough_history_for_a_non_trivial_wait_estimate() -> None:
    """At least one queue has 30 or more finished tickets whose waits genuinely vary."""
    best = max(
        (
            [
                t.wait_minutes
                for t in ticket_history(c, q, DAY, NOW)
                if t.status is TicketStatus.DONE
            ]
            for c in CLINICS
            for q in queues_for(c)
        ),
        key=len,
    )
    waits = [w for w in best if w is not None]
    assert len(waits) >= 30
    assert pstdev(waits) > 5  # minutes: a flat line would make an estimator trivial


def test_arrivals_follow_the_day() -> None:
    """The first two hours after opening are busier than any two hours after them."""
    clinic = CLINICS[0]
    opens = datetime.combine(DAY, clinic.opens, tzinfo=APP_TIMEZONE)
    late = datetime.combine(DAY, time(17, 0), tzinfo=APP_TIMEZONE)
    joined = [
        t.joined_at
        for q in queues_for(clinic)
        for t in ticket_history(clinic, q, DAY, late)
    ]
    rush = sum(opens <= j < opens + timedelta(hours=2) for j in joined)
    afternoon = sum(
        opens + timedelta(hours=6) <= j < opens + timedelta(hours=8) for j in joined
    )
    assert rush > 1.5 * afternoon


def test_cape_town_has_public_and_private_clinics_across_the_metro() -> None:
    """A tester in the Western Cape finds clinics near them: public and private, city centre to Khayelitsha."""
    cape_town = [c for c in CLINICS if c.city == "Cape Town"]
    assert len(cape_town) >= 10
    assert {c.province for c in cape_town} == {Province.WESTERN_CAPE}
    assert {c.sector for c in cape_town} == {SiteSector.PUBLIC, SiteSector.PRIVATE}
    assert {"Woodstock", "Khayelitsha", "Delft"} <= {c.suburb for c in cape_town}
