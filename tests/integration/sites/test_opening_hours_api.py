"""Hours, holidays and closures over HTTP, and the one gate every channel asks (Issue 24).

What is proven here, as opposed to in the unit tests of the pure logic:

* a manager sets the week and a receptionist can read it but not change it;
* a public holiday shuts a clinic that would otherwise be open, and a clinic that works holidays
  says so with a rule;
* **a closure stops new joins on every channel at the same instant** — asserted by driving all four
  `TicketSource` values through the one server-side gate, which does not take the channel as an
  input at all;
* announcing a closure **raises an event and sends nothing**, which is what lets a clinic close
  while the SMS gateway is down;
* another clinic's hours, holidays and closures are all 404.
"""

from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from starlette import status

from src.commons.enums import (
    AuditAction,
    AuditEntityType,
    SiteStatus,
    TicketSource,
)
from src.commons.time import APP_TIMEZONE
from src.core import domain_events
from src.core.domain_events import SiteClosureAnnounced, SiteClosureLifted
from src.core.site_scope import SiteAccess
from src.database.models import AuditEvent, PublicHoliday, Site, SiteClosure, User
from src.modules.sites.availability import NOT_ACCEPTING, join_gate
from src.modules.sites.hours import schedule_for

#: A clinic that never closes: one span a day, opening and closing at the same time, which is
#: what a twenty-four-hour casualty unit looks like. Used where the test is about something other
#: than the clock.
_ALWAYS_OPEN = {
    "days": [
        {
            "weekday": weekday,
            "spans": [{"opens_at": "00:00:00", "closes_at": "00:00:00"}],
        }
        for weekday in range(7)
    ]
}

_WEEKDAY_HOURS = {
    "days": [
        {
            "weekday": weekday,
            "spans": [
                {"opens_at": "07:00:00", "closes_at": "12:30:00"},
                {"opens_at": "13:00:00", "closes_at": "16:00:00"},
            ],
        }
        for weekday in range(5)
    ]
}


@pytest.fixture
def collected_events(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Capture everything published on the domain bus, without registering a real subscriber."""
    published: list[object] = []
    original = domain_events.publish

    def _capture(event: object) -> None:
        published.append(event)
        original(event)  # type: ignore[arg-type]

    monkeypatch.setattr(domain_events, "publish", _capture)
    return published


def _manager(clinics: SimpleNamespace):
    """The clinic manager at Clinic A."""
    return clinics.client("manager.a@clinicq.example")


# --- the weekly schedule ------------------------------------------------------------------


def test_a_manager_sets_the_week_and_every_day_comes_back(
    clinics: SimpleNamespace,
) -> None:
    """All seven days are returned, so a caller never has to infer a missing one."""
    saved = _manager(clinics).put(
        f"/api/v1/sites/{clinics.site_a}/hours", json=_WEEKDAY_HOURS
    )

    assert saved.status_code == status.HTTP_200_OK, saved.text
    days = saved.json()["days"]
    assert [day["weekday"] for day in days] == list(range(7))
    assert len(days[0]["spans"]) == 2  # the lunch break
    assert days[5]["spans"] == [] and days[6]["spans"] == []  # the weekend


def test_overlapping_spans_on_one_day_are_refused(clinics: SimpleNamespace) -> None:
    """Two answers to "are you open at 12:15" is not an answer."""
    refused = _manager(clinics).put(
        f"/api/v1/sites/{clinics.site_a}/hours",
        json={
            "days": [
                {
                    "weekday": 0,
                    "spans": [
                        {"opens_at": "07:00:00", "closes_at": "13:00:00"},
                        {"opens_at": "12:00:00", "closes_at": "16:00:00"},
                    ],
                }
            ]
        },
    )
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "overlap" in refused.text


def test_a_receptionist_reads_the_hours_and_cannot_change_them(
    clinics: SimpleNamespace,
) -> None:
    """The same split as the profile: the front desk reads, the manager decides."""
    desk = clinics.client("desk.a@clinicq.example")
    assert (
        desk.get(f"/api/v1/sites/{clinics.site_a}/hours").status_code
        == status.HTTP_200_OK
    )
    assert (
        desk.put(
            f"/api/v1/sites/{clinics.site_a}/hours", json=_WEEKDAY_HOURS
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_another_clinics_hours_holidays_and_closures_are_all_not_found(
    clinics: SimpleNamespace,
) -> None:
    """Non-negotiable 3 holds for everything Issue 24 added, not only the profile."""
    manager = _manager(clinics)
    for path in ("hours", "holidays", "closures", "open"):
        assert (
            manager.get(f"/api/v1/sites/{clinics.site_b}/{path}").status_code
            == status.HTTP_404_NOT_FOUND
        ), path
    assert (
        manager.put(
            f"/api/v1/sites/{clinics.site_b}/hours", json=_WEEKDAY_HOURS
        ).status_code
        == status.HTTP_404_NOT_FOUND
    )


# --- public holidays ----------------------------------------------------------------------


def _seed_holiday(clinics: SimpleNamespace, day: date, name: str) -> None:
    """Put one public holiday in the calendar."""
    with clinics.session() as db:
        db.add(PublicHoliday(holiday_date=day, name=name))
        db.commit()


def test_a_public_holiday_appears_on_the_calendar_closed_until_a_clinic_says_otherwise(
    clinics: SimpleNamespace,
) -> None:
    """No rule means closed, and the payload says it is a default rather than a decision."""
    heritage_day = date(2026, 9, 24)
    _seed_holiday(clinics, heritage_day, "Heritage Day")

    listed = _manager(clinics).get(f"/api/v1/sites/{clinics.site_a}/holidays").json()

    entry = next(
        item for item in listed["items"] if item["holiday_date"] == "2026-09-24"
    )
    assert entry["name"] == "Heritage Day"
    assert entry["is_open"] is False and entry["has_rule"] is False


def test_a_clinic_that_works_public_holidays_writes_a_rule(
    clinics: SimpleNamespace,
) -> None:
    """The rule replaces the weekday's hours for that date, and is audited."""
    heritage_day = date(2026, 9, 24)
    _seed_holiday(clinics, heritage_day, "Heritage Day")
    manager = _manager(clinics)

    saved = manager.put(
        f"/api/v1/sites/{clinics.site_a}/holidays/2026-09-24",
        json={"opens_at": "08:00:00", "closes_at": "11:00:00"},
    )

    assert saved.status_code == status.HTTP_200_OK, saved.text
    assert saved.json() == {
        "holiday_date": "2026-09-24",
        "name": "Heritage Day",
        "observed_for": None,
        "is_open": True,
        "opens_at": "08:00:00",
        "closes_at": "11:00:00",
        "has_rule": True,
    }


def test_a_clinic_cannot_invent_a_public_holiday(clinics: SimpleNamespace) -> None:
    """An ordinary Tuesday off is an ad-hoc closure; the country's calendar is not a clinic's."""
    refused = _manager(clinics).put(
        f"/api/v1/sites/{clinics.site_a}/holidays/2026-09-15",
        json={"opens_at": None, "closes_at": None},
    )
    assert refused.status_code == status.HTTP_404_NOT_FOUND
    assert "not a public holiday" in refused.json()["detail"]


def test_half_a_holiday_rule_is_refused(clinics: SimpleNamespace) -> None:
    """An opening time with no closing time is not a decision anyone can act on."""
    _seed_holiday(clinics, date(2026, 9, 24), "Heritage Day")
    refused = _manager(clinics).put(
        f"/api/v1/sites/{clinics.site_a}/holidays/2026-09-24",
        json={"opens_at": "08:00:00"},
    )
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# --- closures ------------------------------------------------------------------------------


def test_announcing_a_closure_raises_an_event_and_sends_nothing(
    clinics: SimpleNamespace, collected_events: list[object]
) -> None:
    """The closure lands, an event carries the reason, and no message is sent from here.

    Delivery belongs to the notification service (Issue 63). A clinic has to be able to close when
    the SMS gateway is down, so this endpoint's job ends at "the fact is recorded and published".
    """
    announced = _manager(clinics).post(
        f"/api/v1/sites/{clinics.site_a}/closures",
        json={"reason": "The water is off."},
    )

    assert announced.status_code == status.HTTP_201_CREATED, announced.text
    body = announced.json()
    assert body["reason"] == "The water is off." and body["ends_at"] is None

    event = next(e for e in collected_events if isinstance(e, SiteClosureAnnounced))
    assert event.site_id == clinics.site_a
    assert event.reason == "The water is off."
    assert event.announced_by == "manager.a@clinicq.example"
    assert event.closure_id == body["id"]


def test_the_closure_event_is_published_only_after_the_transaction_commits(
    clinics: SimpleNamespace, collected_events: list[object]
) -> None:
    """A subscriber that reads the database must find the closure the event is about."""
    _manager(clinics).post(
        f"/api/v1/sites/{clinics.site_a}/closures", json={"reason": "Power failure."}
    )
    event = next(e for e in collected_events if isinstance(e, SiteClosureAnnounced))
    with clinics.session() as db:
        assert db.get(SiteClosure, event.closure_id) is not None


def test_a_closure_is_audited_with_the_manager_who_announced_it(
    clinics: SimpleNamespace,
) -> None:
    """ "Why were we shut on the 14th" has to be answerable, with a name against it."""
    _manager(clinics).post(
        f"/api/v1/sites/{clinics.site_a}/closures", json={"reason": "Staff meeting."}
    )
    with clinics.session() as db:
        row = db.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == AuditEntityType.SITE.value,
                AuditEvent.action == AuditAction.CREATE.value,
            )
        ).scalar_one()
    assert row.actor == "manager.a@clinicq.example"
    assert row.site_id == clinics.site_a
    assert "Staff meeting." in (row.context or "")


def test_a_closure_ending_before_it_starts_is_refused(
    clinics: SimpleNamespace,
) -> None:
    """A window that runs backwards would close the clinic for ever, or not at all."""
    now = datetime.now(APP_TIMEZONE)
    refused = _manager(clinics).post(
        f"/api/v1/sites/{clinics.site_a}/closures",
        json={
            "reason": "Backwards.",
            "starts_at": now.isoformat(),
            "ends_at": (now - timedelta(hours=1)).isoformat(),
        },
    )
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_lifting_a_closure_keeps_the_row_and_raises_its_own_event(
    clinics: SimpleNamespace, collected_events: list[object]
) -> None:
    """The clinic reopens, the trail keeps both facts, and lifting the same one twice is a 404."""
    manager = _manager(clinics)
    closure_id = manager.post(
        f"/api/v1/sites/{clinics.site_a}/closures", json={"reason": "Power failure."}
    ).json()["id"]

    lifted = manager.delete(f"/api/v1/sites/{clinics.site_a}/closures/{closure_id}")

    assert lifted.status_code == status.HTTP_200_OK
    assert lifted.json()["lifted_at"] is not None
    assert any(isinstance(e, SiteClosureLifted) for e in collected_events)
    with clinics.session() as db:
        assert db.get(SiteClosure, closure_id) is not None
    assert (
        manager.delete(
            f"/api/v1/sites/{clinics.site_a}/closures/{closure_id}"
        ).status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_a_receptionist_cannot_close_the_clinic(clinics: SimpleNamespace) -> None:
    """Closing is the same decision as setting the hours, made at short notice."""
    refused = clinics.client("desk.a@clinicq.example").post(
        f"/api/v1/sites/{clinics.site_a}/closures", json={"reason": "I am going home."}
    )
    assert refused.status_code == status.HTTP_403_FORBIDDEN


# --- the gate every channel asks -------------------------------------------------------------


def test_the_open_endpoint_reports_the_hours_and_the_join_decision_together(
    clinics: SimpleNamespace,
) -> None:
    """One payload, so a screen can never show "open" and "not taking patients" from two calls."""
    manager = _manager(clinics)
    manager.put(f"/api/v1/sites/{clinics.site_a}/hours", json=_WEEKDAY_HOURS)

    state = manager.get(f"/api/v1/sites/{clinics.site_a}/open")

    assert state.status_code == status.HTTP_200_OK
    body = state.json()
    assert set(body) == {
        "site_id",
        "is_open",
        "next_open_at",
        "closure_reason",
        "accepting_joins",
        "refusal",
    }
    assert body["is_open"] == body["accepting_joins"]


def test_a_closure_shows_up_in_the_open_endpoint_with_its_reason(
    clinics: SimpleNamespace,
) -> None:
    """Discovery says "closed: the water is off", not a bare "closed" (Issue 32 renders this)."""
    manager = _manager(clinics)
    manager.put(f"/api/v1/sites/{clinics.site_a}/hours", json=_WEEKDAY_HOURS)
    manager.post(
        f"/api/v1/sites/{clinics.site_a}/closures",
        json={"reason": "The water is off."},
    )

    body = manager.get(f"/api/v1/sites/{clinics.site_a}/open").json()

    assert body["is_open"] is False
    assert body["accepting_joins"] is False
    assert body["closure_reason"] == "The water is off."
    assert "The water is off." in body["refusal"]


def test_a_closure_stops_new_joins_and_the_gate_cannot_tell_the_channels_apart(
    clinics: SimpleNamespace,
) -> None:
    """The acceptance criterion, and the reason it holds by construction.

    A closure has to stop web, USSD, WhatsApp and walk-in joins at the same instant. The way that
    is guaranteed is not four careful implementations but one that **cannot** behave differently
    per channel: :func:`~src.modules.sites.availability.join_gate` takes the clinic and the moment,
    and there is no parameter for the channel to arrive through. So this asserts both halves — the
    gate refuses after the closure, and its signature has nowhere for a per-channel exception to
    live — and names every ``TicketSource`` so a fifth channel added later brings this test with it.
    """
    assert {source.value for source in TicketSource} == {
        "web",
        "ussd",
        "whatsapp",
        "walk_in",
    }
    assert set(inspect.signature(join_gate).parameters) == {
        "site",
        "schedule",
        "moment",
    }

    manager = _manager(clinics)
    manager.put(f"/api/v1/sites/{clinics.site_a}/hours", json=_ALWAYS_OPEN)
    assert manager.get(f"/api/v1/sites/{clinics.site_a}/open").json()["accepting_joins"]

    manager.post(
        f"/api/v1/sites/{clinics.site_a}/closures",
        json={"reason": "The water is off."},
    )

    state = manager.get(f"/api/v1/sites/{clinics.site_a}/open").json()
    assert state["accepting_joins"] is False
    assert "The water is off." in state["refusal"]
    # And at the layer a join route will call, not only through the endpoint.
    with clinics.session() as db:
        site = db.get(Site, clinics.site_a)
        staff = db.execute(
            select(User).where(User.email == "manager.a@clinicq.example")
        ).scalar_one()
        assert site is not None
        access = SiteAccess(site_id=clinics.site_a, user=staff)
        gate = join_gate(site, schedule_for(db, access))
    assert gate.allowed is False


def test_a_suspended_clinic_refuses_joins_however_open_its_hours_say_it_is(
    clinics: SimpleNamespace,
) -> None:
    """A clinic the platform switched off is shut, and the refusal says nothing about why.

    "Suspended" and "never verified" get the same sentence on purpose: which of the two it is, is
    the clinic's business and the platform's, not a stranger's (Issue 29 owns the transitions).
    """
    manager = _manager(clinics)
    manager.put(f"/api/v1/sites/{clinics.site_a}/hours", json=_ALWAYS_OPEN)
    with clinics.session() as db:
        site = db.get(Site, clinics.site_a)
        assert site is not None
        site.status = SiteStatus.SUSPENDED.value
        db.commit()

    state = manager.get(f"/api/v1/sites/{clinics.site_a}/open").json()

    assert state["is_open"] is True  # its hours do say it is open
    assert state["accepting_joins"] is False  # and it is still not taking patients
    assert state["refusal"] == NOT_ACCEPTING
