"""Discovery analytics and the search rate limit, against PostGIS (Issue 38).

What is proved here, each by reading what was actually stored or returned:

* a search, a clinic view and a join are three rows that carry **no identifier beyond a rotating
  session reference**: no phone number, no coordinates, no account, not even the cookie itself;
* the reference groups one browser's events on one day and is not the token it came from;
* a clinic that opts out, or the whole feature switched off, records nothing;
* the conversion numbers count views and joins per clinic;
* hammering the public search is refused with a ``429``, per address and per session.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text, update
from starlette import status

from src.commons.enums import DiscoveryChannel, DiscoveryEventKind
from src.commons.time import now_sast
from src.core.config import Settings, get_settings
from src.core.rate_limit_deps import DISCOVERY_SEARCH_LIMITED, DISCOVERY_SESSION_COOKIE
from src.database.models import DiscoveryEvent, Patient, Site
from src.modules.discovery import analytics
from tests.integration.discovery.conftest import JOHANNESBURG

pytestmark = pytest.mark.postgres

_NEARBY = "/api/v1/clinics/nearby"
_POSITION = {"lat": JOHANNESBURG.latitude, "lon": JOHANNESBURG.longitude}
_HILLBROW = "hillbrow-chc"

#: Every column the table has. A new column fails here, so whoever adds one has to read the rule.
_COLUMNS = {
    "id",
    "kind",
    "channel",
    "occurred_at",
    "service_day",
    "session_ref",
    "site_id",
    "sector",
    "origin_basis",
    "radius_m",
    "result_count",
}


def _events(directory: SimpleNamespace) -> list[dict[str, object]]:
    """The stored rows, read with plain SQL so nothing in the model can hide a column."""
    with directory.session() as db:
        rows = db.execute(text("SELECT * FROM discovery_event ORDER BY occurred_at"))
        return [dict(row._mapping) for row in rows]


def _settings(directory: SimpleNamespace, **changes: object) -> Settings:
    """The app's settings with ``changes``, applied to both the dependency and direct reads."""
    current: Settings = directory.app.dependency_overrides[get_settings]()
    changed = current.model_copy(update=changes)
    directory.app.dependency_overrides[get_settings] = lambda: changed
    return changed


def test_a_search_a_view_and_a_join_store_nothing_that_identifies_the_patient(
    directory: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance criterion, proved on the stored rows rather than on the code that wrote them."""
    settings = _settings(directory)
    monkeypatch.setattr(analytics, "get_settings", lambda: settings)
    client, patient_id = directory.patient_client()
    with directory.session() as db:
        phone = db.get(Patient, patient_id).phone_e164  # type: ignore[union-attr]

    searched = client.get("/discover", params=_POSITION)
    token = searched.cookies.get(DISCOVERY_SESSION_COOKIE)
    assert searched.status_code == status.HTTP_200_OK
    assert token, "the first page sets the discovery session cookie"
    cookie = {DISCOVERY_SESSION_COOKIE: token}
    viewed = client.get(f"/discover/clinics/{_HILLBROW}", cookies=cookie)
    assert viewed.status_code == status.HTTP_200_OK
    # The join flow is Issue 40's; it calls the recorder with the same session token.
    with directory.session() as db:
        analytics.record_join_completed(
            db,
            directory.ids[_HILLBROW],
            channel=DiscoveryChannel.WEB,
            session_token=token,
        )

    events = _events(directory)
    assert [event["kind"] for event in events] == [
        DiscoveryEventKind.SEARCH_PERFORMED.value,
        DiscoveryEventKind.CLINIC_VIEWED.value,
        DiscoveryEventKind.JOIN_COMPLETED.value,
    ]
    assert set(events[0]) == _COLUMNS
    stored = " ".join(str(value) for event in events for value in event.values())
    for secret in (
        phone,
        patient_id,
        token,
        f"{JOHANNESBURG.latitude:.3f}",
        f"{JOHANNESBURG.longitude:.3f}",
        f"{JOHANNESBURG.latitude:.2f}",
    ):
        assert secret not in stored, f"{secret!r} was stored"
    # One visit on one day is one reference, and it is not the cookie.
    refs = {event["session_ref"] for event in events}
    assert len(refs) == 1
    assert refs != {token}
    assert refs == {analytics.session_ref(token, now_sast().date(), settings)}


def test_the_reference_rotates_at_midnight_without_the_cookie_changing(
    directory: SimpleNamespace,
) -> None:
    """Monday's and Tuesday's events from one browser cannot be linked."""
    token = analytics.new_session_token()
    monday = now_sast().replace(hour=10)
    with directory.session() as db:
        for moment in (monday, monday + timedelta(days=1)):
            analytics.record_clinic_viewed(
                db,
                directory.ids[_HILLBROW],
                channel=DiscoveryChannel.API,
                session_token=token,
                moment=moment,
            )
    first, second = (event["session_ref"] for event in _events(directory))
    assert first and second and first != second


def test_a_later_page_of_the_same_search_is_not_another_search(
    directory: SimpleNamespace,
) -> None:
    """``Show more`` is the same search: only the first page counts."""
    first = directory.client.get("/discover", params={**_POSITION, "radius_m": 50000})
    cookie = {DISCOVERY_SESSION_COOKIE: first.cookies[DISCOVERY_SESSION_COOKIE]}
    directory.client.get(
        "/discover/results",
        params={**_POSITION, "radius_m": 50000, "offset": 20},
        headers={"HX-Request": "true"},
        cookies=cookie,
    )
    assert len(_events(directory)) == 1


def test_a_clinic_that_opts_out_is_never_counted(directory: SimpleNamespace) -> None:
    """The switch is the clinic's: its views are not recorded, other clinics' still are."""
    opted_out = directory.ids[_HILLBROW]
    with directory.session() as db:
        db.execute(
            update(Site).where(Site.id == opted_out).values(analytics_enabled=False)
        )
        db.commit()

    directory.client.get(f"/api/v1/clinics/{_HILLBROW}")
    directory.client.get("/api/v1/clinics/medicross-meldene")

    viewed = [event["site_id"] for event in _events(directory)]
    assert viewed == [directory.ids["medicross-meldene"]]


def test_analytics_switched_off_records_nothing_and_changes_no_answer(
    directory: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``DISCOVERY_ANALYTICS_ENABLED=false``: the pages and the API behave exactly the same."""
    settings = _settings(directory, discovery_analytics_enabled=False)
    monkeypatch.setattr(analytics, "get_settings", lambda: settings)

    assert directory.client.get(_NEARBY, params=_POSITION).status_code == 200
    assert directory.client.get("/discover", params=_POSITION).status_code == 200
    assert directory.client.get(f"/api/v1/clinics/{_HILLBROW}").status_code == 200

    assert _events(directory) == []


def test_the_conversion_numbers_count_views_and_joins_per_clinic(
    directory: SimpleNamespace,
) -> None:
    """Four views and one join at Hillbrow, one view at Meldene, and nothing outside the range."""
    hillbrow, meldene = directory.ids[_HILLBROW], directory.ids["medicross-meldene"]
    today = now_sast()
    with directory.session() as db:
        for _ in range(4):
            analytics.record_clinic_viewed(
                db, hillbrow, channel=DiscoveryChannel.WEB, session_token=None
            )
        analytics.record_join_started(
            db, hillbrow, channel=DiscoveryChannel.WEB, session_token=None
        )
        analytics.record_join_completed(
            db, hillbrow, channel=DiscoveryChannel.WEB, session_token=None
        )
        analytics.record_clinic_viewed(
            db, meldene, channel=DiscoveryChannel.USSD, session_token=None
        )
        analytics.record_clinic_viewed(
            db,
            meldene,
            channel=DiscoveryChannel.USSD,
            session_token=None,
            moment=today - timedelta(days=40),
        )
        numbers = {
            row.site_id: row
            for row in analytics.conversion_by_site(
                db, start=today.date() - timedelta(days=7), end=today.date()
            )
        }

    assert numbers[hillbrow].views == 4
    assert numbers[hillbrow].joins_started == 1
    assert numbers[hillbrow].joins_completed == 1
    assert numbers[hillbrow].conversion_rate == 0.25
    assert numbers[meldene].views == 1
    assert numbers[meldene].conversion_rate == 0.0


def test_hammering_the_search_from_one_address_is_refused(
    directory: SimpleNamespace,
) -> None:
    """Past the per-address budget, ``429`` with ``Retry-After``, and the refusal is not recorded."""
    _settings(
        directory,
        discovery_search_rate_limit_per_ip=5,
        discovery_search_rate_limit_per_session=100,
    )
    answers = [
        directory.client.get(_NEARBY, params=_POSITION).status_code for _ in range(8)
    ]

    assert answers == [200] * 5 + [429] * 3
    refused = directory.client.get("/api/v1/clinics/areas", params={"q": "soweto"})
    assert refused.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert refused.json()["detail"] == DISCOVERY_SEARCH_LIMITED
    assert int(refused.headers["Retry-After"]) > 0
    assert len(_events(directory)) == 5


def test_one_session_has_its_own_smaller_budget(directory: SimpleNamespace) -> None:
    """A session hammering the search through many addresses still runs out."""
    _settings(
        directory,
        discovery_search_rate_limit_per_ip=100,
        discovery_search_rate_limit_per_session=3,
    )
    cookie = {DISCOVERY_SESSION_COOKIE: analytics.new_session_token()}
    with_session = [
        directory.client.get(_NEARBY, params=_POSITION, cookies=cookie).status_code
        for _ in range(4)
    ]
    another = {DISCOVERY_SESSION_COOKIE: analytics.new_session_token()}

    assert with_session == [200, 200, 200, 429]
    assert (
        directory.client.get(_NEARBY, params=_POSITION, cookies=another).status_code
        == status.HTTP_200_OK
    )


def test_the_web_search_pages_share_the_budget(directory: SimpleNamespace) -> None:
    """The HTML pages are the same scraping risk as the API, so they are limited the same way."""
    _settings(directory, discovery_search_rate_limit_per_ip=2)
    assert directory.client.get("/discover", params=_POSITION).status_code == 200
    assert (
        directory.client.get(
            "/discover/areas", params={"q": "sow"}, headers={"HX-Request": "true"}
        ).status_code
        == 200
    )
    assert (
        directory.client.get("/discover", params=_POSITION).status_code
        == status.HTTP_429_TOO_MANY_REQUESTS
    )


def test_the_stored_day_is_johannesburgs(directory: SimpleNamespace) -> None:
    """``service_day`` is the local date the reports group by, not UTC's."""
    late = now_sast().replace(hour=23, minute=30)
    with directory.session() as db:
        analytics.record_clinic_viewed(
            db,
            directory.ids[_HILLBROW],
            channel=DiscoveryChannel.WEB,
            session_token=None,
            moment=late,
        )
        stored = db.execute(select(DiscoveryEvent.service_day)).scalar_one()
    assert stored == late.date()
    assert isinstance(stored, date)
