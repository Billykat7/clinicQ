"""The clinic detail page and its API, tested through their data (Issue 35).

One test per acceptance criterion that can be proven without a browser, plus the refusals. The page's
decisions are made in :func:`src.modules.discovery.profile.clinic_profile` (plain data every channel
reads) and :func:`src.web.discover.detail_view` (the words), so both are asserted directly; the
routes are asserted by status code and header. The 320 px screenshot, the live refresh in a real
browser and the accessibility check are in the pull request.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import update
from starlette import status

from src.commons.enums import SiteStatus
from src.commons.time import APP_TIMEZONE
from src.core.config import Settings
from src.database.models import Queue, Site, SiteClosure
from src.modules.discovery.profile import NO_QUEUES, WALK_IN_ONLY, clinic_profile
from src.web import discover
from src.web.discover import (
    CLINIC_REPORTED_NOTICE,
    JOIN_NOT_SWITCHED_ON,
    LIVE_REFRESH_SECONDS,
    PAYMENT_NOT_LISTED,
    WAIT_NOT_AVAILABLE,
    detail_view,
    live_view,
)

pytestmark = pytest.mark.postgres

_TUESDAY_10AM = datetime(2026, 9, 15, 10, 0, tzinfo=APP_TIMEZONE)
_SATURDAY_10AM = _TUESDAY_10AM + timedelta(days=4)
_HTMX = {"HX-Request": "true"}


# --------------------------------------------------------------------------------------
# The join action: disabled with a reason, never hidden
# --------------------------------------------------------------------------------------


def test_an_open_clinic_can_be_joined_and_the_button_says_why_it_waits_for_the_flag(
    directory: SimpleNamespace,
) -> None:
    """The service says yes; the page enables the action only once joining is switched on."""
    with directory.session() as db:
        profile = clinic_profile(db, "hillbrow-chc", moment=_TUESDAY_10AM)
    assert profile is not None
    assert profile.join.allowed is True and profile.join.reason is None
    assert profile.join.remote_queue_count >= 1

    off = live_view(profile, join_enabled=False).join
    on = live_view(profile, join_enabled=True).join
    assert (off.enabled, off.reason, off.href) == (False, JOIN_NOT_SWITCHED_ON, None)
    assert (on.enabled, on.reason) == (True, None)
    assert on.href == "/discover/clinics/hillbrow-chc/join"
    assert off.label == on.label == "Join the queue"


def test_a_closed_clinic_disables_the_join_and_says_when_it_opens(
    directory: SimpleNamespace,
) -> None:
    """Saturday: closed, and the reason ends with Monday's opening time."""
    with directory.session() as db:
        profile = clinic_profile(db, "hillbrow-chc", moment=_SATURDAY_10AM)
    assert profile is not None and profile.join.allowed is False
    button = live_view(profile, join_enabled=True).join
    assert button.enabled is False and button.href is None
    assert button.reason == (
        "Hillbrow Community Health Centre is closed at the moment. Opens Mon 21 Sep at 07:00."
    )


def test_a_closure_gives_its_own_reason(directory: SimpleNamespace) -> None:
    """The manager's words, then when it reopens."""
    with directory.session() as db:
        db.add(
            SiteClosure(
                site_id=directory.ids["hillbrow-chc"],
                reason="The water is off",
                starts_at=_TUESDAY_10AM - timedelta(hours=1),
                ends_at=_TUESDAY_10AM + timedelta(hours=3),
            )
        )
        db.commit()
        profile = clinic_profile(db, "hillbrow-chc", moment=_TUESDAY_10AM)
    assert profile is not None
    view = live_view(profile, join_enabled=True)
    assert view.open_label == "Closed: The water is off. Opens today at 13:00."
    assert view.join.enabled is False
    assert view.join.reason == (
        "Hillbrow Community Health Centre is closed: The water is off. Opens today at 13:00."
    )


def test_walk_in_only_queues_and_no_queues_each_explain_themselves(
    directory: SimpleNamespace,
) -> None:
    """Open, but a phone cannot join: every queue walk-in only, or no queue set up at all."""
    site_id = directory.ids["hillbrow-chc"]
    with directory.session() as db:
        db.execute(
            update(Queue)
            .where(Queue.site_id == site_id)
            .values(allows_remote_join=False)
        )
        db.commit()
        walk_in = clinic_profile(db, "hillbrow-chc", moment=_TUESDAY_10AM)
        db.execute(
            update(Queue).where(Queue.site_id == site_id).values(is_active=False)
        )
        db.commit()
        none_left = clinic_profile(db, "hillbrow-chc", moment=_TUESDAY_10AM)
    assert walk_in is not None and none_left is not None
    assert (walk_in.join.allowed, walk_in.join.reason) == (False, WALK_IN_ONLY)
    assert all(row.walk_in_only for row in live_view(walk_in, join_enabled=True).queues)
    assert (none_left.join.allowed, none_left.join.reason) == (False, NO_QUEUES)


# --------------------------------------------------------------------------------------
# What the page shows
# --------------------------------------------------------------------------------------


def test_the_page_answers_open_hours_queues_services_and_contact(
    directory: SimpleNamespace,
) -> None:
    """Everything on the page for a public clinic on a Tuesday morning, as data."""
    with directory.session() as db:
        db.execute(
            update(Site)
            .where(Site.slug == "hillbrow-chc")
            .values(phone_e164="+27105550100")
        )
        db.commit()
        profile = clinic_profile(db, "hillbrow-chc", moment=_TUESDAY_10AM)
    assert profile is not None
    view = detail_view(profile, join_enabled=False)

    assert view.live.open_label == "Open now"
    assert view.live.today_label == "Today: 07:00–16:00"
    assert [(row.day, row.hours) for row in view.week][:2] == [
        ("Monday", "07:00–16:00"),
        ("Tuesday", "07:00–16:00"),
    ]
    assert [row.day for row in view.week if row.is_today] == ["Tuesday"]
    assert view.week[6].hours == "Closed"
    assert [row.name for row in view.live.queues] == [q.name for q in profile.queues]
    assert view.phone_label == "+27 10 555 0100" and view.tel_href == "tel:+27105550100"
    assert view.directions_href.startswith(
        "https://www.google.com/maps/dir/?api=1&destination="
    )
    assert view.area_line == "Hillbrow, Johannesburg, Gauteng"


def test_wait_times_are_a_range_or_not_shown_never_a_single_number(
    directory: SimpleNamespace,
) -> None:
    """No estimator yet (Issue 42): every queue says so, and the queue length is not invented either."""
    with directory.session() as db:
        profile = clinic_profile(db, "hillbrow-chc", moment=_TUESDAY_10AM)
    assert profile is not None and profile.queues
    rows = live_view(profile, join_enabled=False).queues
    assert all(row.wait_label == WAIT_NOT_AVAILABLE for row in rows)
    assert all(queue.wait_range is None for queue in profile.queues)
    assert all(not any(ch.isdigit() for ch in row.waiting_label) for row in rows)


def test_payment_information_is_only_for_private_clinics_and_carries_the_notice(
    directory: SimpleNamespace,
) -> None:
    """A private clinic gets the section, saying nothing is listed yet; a public one has none."""
    with directory.session() as db:
        private = clinic_profile(db, "medicross-meldene", moment=_TUESDAY_10AM)
        public = clinic_profile(db, "hillbrow-chc", moment=_TUESDAY_10AM)
    assert private is not None and public is not None
    assert detail_view(private, join_enabled=False).payment_note == PAYMENT_NOT_LISTED
    assert detail_view(public, join_enabled=False).payment_note is None
    assert (
        detail_view(private, join_enabled=False).reported_notice
        == CLINIC_REPORTED_NOTICE
    )
    assert "confirm with the clinic" in CLINIC_REPORTED_NOTICE


# --------------------------------------------------------------------------------------
# Routes: the page, the live refresh, the API, and the refusals
# --------------------------------------------------------------------------------------


def test_live_figures_refresh_through_htmx_without_a_reload(
    directory: SimpleNamespace,
) -> None:
    """The fragment answers htmx; a plain browser is sent to the page; the interval is 30 s or less."""
    live = directory.client.get("/discover/clinics/hillbrow-chc/live", headers=_HTMX)
    plain = directory.client.get(
        "/discover/clinics/hillbrow-chc/live", follow_redirects=False
    )
    assert live.status_code == status.HTTP_200_OK
    assert plain.status_code == status.HTTP_303_SEE_OTHER
    assert plain.headers["location"] == "/discover/clinics/hillbrow-chc"
    assert LIVE_REFRESH_SECONDS <= 30
    with directory.session() as db:
        profile = clinic_profile(db, "hillbrow-chc")
    assert profile is not None
    assert (
        live_view(profile, join_enabled=False).live_href
        == "/discover/clinics/hillbrow-chc/live"
    )


@pytest.mark.parametrize(
    "state",
    [SiteStatus.PENDING_VERIFICATION, SiteStatus.SUSPENDED, SiteStatus.DRAFT],
)
def test_a_clinic_a_patient_may_not_see_is_the_same_404_as_a_missing_one(
    directory: SimpleNamespace, state: SiteStatus
) -> None:
    """Page, live fragment and API alike, so an address cannot confirm an unchecked clinic exists."""
    with directory.session() as db:
        db.execute(
            update(Site).where(Site.slug == "hillbrow-chc").values(status=state.value)
        )
        db.commit()
    for path in (
        "/discover/clinics/hillbrow-chc",
        "/discover/clinics/no-such-clinic",
        "/api/v1/clinics/hillbrow-chc",
        "/api/v1/clinics/no-such-clinic",
    ):
        assert directory.client.get(path).status_code == status.HTTP_404_NOT_FOUND, path
    assert (
        directory.client.get(
            "/discover/clinics/hillbrow-chc/live", headers=_HTMX
        ).status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_the_profile_api_serves_the_same_data_to_other_channels(
    directory: SimpleNamespace,
) -> None:
    """``GET /api/v1/clinics/{slug}``: the join answer, queues, week and services as JSON."""
    body = directory.client.get("/api/v1/clinics/hillbrow-chc").json()
    assert body["slug"] == "hillbrow-chc" and body["sector"] == "public"
    assert set(body["join"]) == {
        "allowed",
        "reason",
        "next_open_at",
        "remote_queue_count",
    }
    assert (body["join"]["reason"] is None) == body["join"]["allowed"]
    assert body["week"][0]["name"] == "Monday"
    assert body["queues"] and all(q["wait_range"] is None for q in body["queues"])
    assert body["services"]
    assert directory.client.get("/api/v1/clinics/Not_A_Slug").status_code == 422


def test_the_join_flag_is_read_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off by default; turning ``PATIENT_JOIN_ENABLED`` on is configuration, not a code change."""
    secret = "m5-discovery-test-secret-min-32-characters"
    assert Settings(_env_file=None, jwt_secret=secret).patient_join_enabled is False  # type: ignore[call-arg]
    on = Settings(_env_file=None, jwt_secret=secret, patient_join_enabled=True)  # type: ignore[call-arg]
    monkeypatch.setattr(discover, "get_settings", lambda: on)
    assert discover._join_enabled() is True
