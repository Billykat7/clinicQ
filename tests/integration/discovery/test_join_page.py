"""Joining a queue from the web: the join page and the installed app's sign-in (Issue 200).

The page's decisions are made in :func:`src.web.join.join_page` (the step to start at, the queues offered, the
reason when joining is not possible, the question asked), so they are asserted as data. The routes are
asserted by status, headers and the page they render. The calls the page's scripts make, in the order they
make them, run against the real app and database. The browser walk-through is
``tests/e2e/patient/test_join_page.py``.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from starlette import status

from src.commons.enums import ConsentPurpose, PatientChannel, SiteStatus
from src.commons.phone import INVALID_PHONE_MESSAGE
from src.commons.time import APP_TIMEZONE
from src.core import otp_store
from src.core.config import get_settings
from src.database.models import Patient, Queue, Site, SiteOpeningHours, Ticket
from src.modules.discovery.profile import WALK_IN_ONLY, ClinicProfile, clinic_profile
from src.modules.notifications import service as notification_service
from src.modules.notifications.sms import FakeSmsProvider
from src.modules.patients import service as patient_service
from src.modules.patients.consent import has_consent, record_consent
from src.modules.patients.consent_text import CONSENT_WORDING
from src.modules.patients.schemas import PHONE_NOTICE
from src.web import join
from src.web.discover import JOIN_NOT_SWITCHED_ON, live_view
from src.web.join import JoinStep, join_page

pytestmark = pytest.mark.postgres

_TUESDAY_10AM = datetime(2026, 9, 15, 10, 0, tzinfo=APP_TIMEZONE)
_SATURDAY_10AM = _TUESDAY_10AM + timedelta(days=4)
_JOIN = "/discover/clinics/hillbrow-chc/join"
_PHONE = "082 555 0200"


@pytest.fixture
def join_on(
    directory: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    """Joining from a phone switched on, Hillbrow open around the clock, and SMS going to a fake."""
    on = get_settings().model_copy(update={"patient_join_enabled": True})
    monkeypatch.setattr(join, "get_settings", lambda: on)
    site_id = directory.ids["hillbrow-chc"]
    with directory.session() as db:
        db.execute(delete(SiteOpeningHours).where(SiteOpeningHours.site_id == site_id))
        # 00:00–00:00 crosses midnight, so the clinic never closes whenever the suite runs.
        for weekday in range(7):
            db.add(
                SiteOpeningHours(
                    site_id=site_id,
                    weekday=weekday,
                    opens_at=time(0),
                    closes_at=time(0),
                )
            )
        db.commit()
    sms = FakeSmsProvider()
    monkeypatch.setattr(notification_service, "build_sms_provider", lambda: sms)
    otp_store.reset_otp_state()
    return SimpleNamespace(settings=on, site_id=site_id, sms=sms)


def _profile(
    directory: SimpleNamespace, moment: datetime = _TUESDAY_10AM
) -> ClinicProfile:
    with directory.session() as db:
        found = clinic_profile(db, "hillbrow-chc", moment=moment)
    assert found is not None
    return found


# --------------------------------------------------------------------------------------
# What the page shows, decided as data
# --------------------------------------------------------------------------------------


def test_a_visitor_starts_at_the_phone_number_and_is_asked_the_consent_wording(
    directory: SimpleNamespace,
) -> None:
    """Open, switched on, nobody signed in: the phone step, with the words every channel uses."""
    page = join_page(_profile(directory), join_enabled=True, signed_in=False)
    assert (page.step, page.unavailable_reason) == (JoinStep.PHONE, None)
    assert page.notifications_question == CONSENT_WORDING[ConsentPurpose.NOTIFICATIONS]
    assert page.sign_in.phone_notice == PHONE_NOTICE
    assert page.sign_in.invalid_phone == INVALID_PHONE_MESSAGE
    assert page.clinic_href == "/discover/clinics/hillbrow-chc"
    assert page.site_id == directory.ids["hillbrow-chc"]


def test_a_signed_in_patient_starts_at_the_queue(directory: SimpleNamespace) -> None:
    page = join_page(
        _profile(directory),
        join_enabled=True,
        signed_in=True,
        notifications_granted=True,
    )
    assert page.step is JoinStep.QUEUE
    assert page.notifications_granted is True


def test_only_queues_that_take_remote_joins_are_offered_in_the_clinics_order(
    directory: SimpleNamespace,
) -> None:
    """A walk-in-only queue is on the clinic page, marked, and never on the join page."""
    site_id = directory.ids["hillbrow-chc"]
    with directory.session() as db:
        queues = db.scalars(
            select(Queue).where(Queue.site_id == site_id).order_by(Queue.display_order)
        ).all()
        walk_in_id = queues[0].id
        db.execute(
            update(Queue).where(Queue.id == walk_in_id).values(allows_remote_join=False)
        )
        db.commit()
        expected = [queue.id for queue in queues[1:]]
    profile = _profile(directory)
    page = join_page(profile, join_enabled=True, signed_in=False)
    assert [option.queue_id for option in page.queues] == expected
    assert walk_in_id not in {option.queue_id for option in page.queues}
    # Each option carries the clinic page's own figures for that queue.
    rows = {row.name: row for row in live_view(profile, join_enabled=True).queues}
    for option in page.queues:
        assert (option.waiting_label, option.wait_label) == (
            rows[option.name].waiting_label,
            rows[option.name].wait_label,
        )


@pytest.mark.parametrize(
    ("moment", "remote", "flag", "reason"),
    [
        (_TUESDAY_10AM, True, False, JOIN_NOT_SWITCHED_ON),
        (
            _SATURDAY_10AM,
            True,
            True,
            "Hillbrow Community Health Centre is closed at the moment. Opens Mon 21 Sep at 07:00.",
        ),
        (_TUESDAY_10AM, False, True, WALK_IN_ONLY),
    ],
    ids=["switched-off", "closed", "walk-in-only"],
)
def test_when_joining_is_not_possible_the_page_says_why_and_offers_no_queue(
    directory: SimpleNamespace, moment: datetime, remote: bool, flag: bool, reason: str
) -> None:
    """The same reason the clinic page's disabled button gives, and nothing to choose."""
    if not remote:
        with directory.session() as db:
            db.execute(
                update(Queue)
                .where(Queue.site_id == directory.ids["hillbrow-chc"])
                .values(allows_remote_join=False)
            )
            db.commit()
    profile = _profile(directory, moment)
    page = join_page(profile, join_enabled=flag, signed_in=True)
    assert page.step is JoinStep.UNAVAILABLE
    assert (
        page.unavailable_reason
        == reason
        == live_view(profile, join_enabled=flag).join.reason
    )
    assert page.queues == ()


# --------------------------------------------------------------------------------------
# The routes
# --------------------------------------------------------------------------------------


def test_the_page_is_served_never_cached_and_starts_where_the_visitor_is(
    directory: SimpleNamespace, join_on: SimpleNamespace
) -> None:
    anonymous = directory.client.get(_JOIN)
    assert anonymous.status_code == status.HTTP_200_OK
    assert anonymous.headers["cache-control"] == "no-store"
    assert anonymous.context["page"].step is JoinStep.PHONE  # type: ignore[attr-defined]
    assert 'data-step="phone"' in anonymous.text

    client, patient_id = directory.patient_client()
    with directory.session() as db:
        record_consent(
            db,
            db.get_one(Patient, patient_id),
            ConsentPurpose.NOTIFICATIONS,
            granted=True,
            channel=PatientChannel.WEB,
        )
        db.commit()
    signed_in = client.get(_JOIN)
    page = signed_in.context["page"]  # type: ignore[attr-defined]
    assert (page.step, page.signed_in, page.notifications_granted) == (
        JoinStep.QUEUE,
        True,
        True,
    )


def test_a_clinic_a_patient_may_not_see_is_the_same_404_on_the_join_page(
    directory: SimpleNamespace, join_on: SimpleNamespace
) -> None:
    with directory.session() as db:
        db.execute(
            update(Site)
            .where(Site.slug == "red-hill-clinic")
            .values(status=SiteStatus.DRAFT)
        )
        db.commit()
    for path in (
        "/discover/clinics/red-hill-clinic/join",
        "/discover/clinics/no-such-clinic/join",
    ):
        response = directory.client.get(path)
        assert response.status_code == status.HTTP_404_NOT_FOUND, path
        assert response.headers["cache-control"] == "no-store"


def test_switched_off_the_page_is_served_with_the_reason_and_no_form(
    directory: SimpleNamespace,
    join_on: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    off = join_on.settings.model_copy(update={"patient_join_enabled": False})
    monkeypatch.setattr(join, "get_settings", lambda: off)
    response = directory.client.get(_JOIN)
    assert response.status_code == status.HTTP_200_OK
    page = response.context["page"]  # type: ignore[attr-defined]
    assert (page.step, page.unavailable_reason) == (
        JoinStep.UNAVAILABLE,
        JOIN_NOT_SWITCHED_ON,
    )
    assert "sign-in-phone-form" not in response.text
    assert "patient-join.js" not in response.text


# --------------------------------------------------------------------------------------
# The calls the page makes, in its order
# --------------------------------------------------------------------------------------


def _code(sms: FakeSmsProvider) -> str:
    match = re.search(r"\b(\d{6})\b", sms.sent[-1].text)
    assert match is not None
    return match.group(1)


def _csrf(client: TestClient) -> dict[str, str]:
    """The header the page's scripts send: the readable CSRF cookie the sign-in set."""
    token = client.cookies.get(get_settings().csrf_cookie_name)
    assert token
    return {"X-CSRF-Token": token}


def test_the_page_signs_in_records_the_answer_joins_and_opens_the_same_ticket_twice(
    directory: SimpleNamespace, join_on: SimpleNamespace
) -> None:
    """Phone, code, "no" to messages, a queue: a ticket page; joining again opens that ticket, not a second."""
    client = TestClient(directory.app)
    page = client.get(_JOIN).context["page"]  # type: ignore[attr-defined]
    assert page.step is JoinStep.PHONE

    sent = client.post("/api/v1/patients/otp/request", json={"phone": _PHONE})
    assert sent.status_code == status.HTTP_202_ACCEPTED
    assert sent.json()["resend_after_seconds"] > 0
    verified = client.post(
        "/api/v1/patients/otp/verify",
        json={"phone": _PHONE, "code": _code(join_on.sms)},
    )
    assert verified.status_code == status.HTTP_200_OK

    # The answer given on the page is the one recorded.
    answer = client.put(
        "/api/v1/patients/me/consents/notifications",
        json={"granted": False},
        headers=_csrf(client),
    )
    assert answer.status_code == status.HTTP_200_OK
    with directory.session() as db:
        assert (
            has_consent(db, verified.json()["id"], ConsentPurpose.NOTIFICATIONS)
            is False
        )

    # Now signed in, the page starts at the queue, and offers the queue the patient then joins.
    page = client.get(_JOIN).context["page"]  # type: ignore[attr-defined]
    assert page.step is JoinStep.QUEUE
    queue_id = page.queues[0].queue_id
    first = client.post(
        f"/api/v1/clinics/{page.site_id}/queues/{queue_id}/tickets",
        json={"reason_text": "Headache"},
        headers=_csrf(client),
    )
    assert first.status_code == status.HTTP_201_CREATED, first.text
    again = client.post(
        f"/api/v1/clinics/{page.site_id}/queues/{queue_id}/tickets",
        json={"reason_text": None},
        headers=_csrf(client),
    )
    assert again.status_code == status.HTTP_200_OK
    assert again.json()["page_url"] == first.json()["page_url"]
    assert re.fullmatch(r"/t/[A-Za-z0-9_-]{43}", first.json()["page_url"])
    with directory.session() as db:
        assert (
            len(
                db.scalars(
                    select(Ticket).where(Ticket.patient_id == verified.json()["id"])
                ).all()
            )
            == 1
        )

    # The installed app's start page, for this signed-in patient, opens that ticket.
    home = client.get("/t/", follow_redirects=False)
    assert home.status_code == status.HTTP_303_SEE_OTHER
    assert home.headers["location"] == first.json()["page_url"]


def test_each_refusal_the_page_shows_is_a_sentence_from_the_api(
    directory: SimpleNamespace, join_on: SimpleNamespace
) -> None:
    """A second code inside the cooldown, a wrong code, an unreadable number: each has its own words."""
    client = TestClient(directory.app)
    assert (
        client.post("/api/v1/patients/otp/request", json={"phone": _PHONE}).status_code
        == 202
    )

    again = client.post("/api/v1/patients/otp/request", json={"phone": _PHONE})
    assert again.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert int(again.headers["retry-after"]) > 0
    assert again.json()["detail"] == "Too many code requests. Try again shortly."

    wrong = client.post(
        "/api/v1/patients/otp/verify", json={"phone": _PHONE, "code": "000000"}
    )
    assert wrong.status_code == status.HTTP_400_BAD_REQUEST
    assert wrong.json()["detail"] == "That code is not right."
    assert wrong.json()["attempts_left"] > 0

    unreadable = client.post("/api/v1/patients/otp/request", json={"phone": "abcdefgh"})
    assert (unreadable.status_code, unreadable.json()["detail"]) == (
        422,
        INVALID_PHONE_MESSAGE,
    )
    # Too short for request validation: no sentence comes back, so the page shows the same one it was given.
    short = client.post("/api/v1/patients/otp/request", json={"phone": "123"})
    assert short.status_code == 422 and not isinstance(short.json()["detail"], str)
    page = client.get(_JOIN).context["page"]  # type: ignore[attr-defined]
    assert page.sign_in.invalid_phone == INVALID_PHONE_MESSAGE


def test_with_sms_switched_off_the_code_request_refuses_in_words(
    directory: SimpleNamespace,
    join_on: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = patient_service.get_settings()
    monkeypatch.setattr(
        patient_service,
        "get_settings",
        lambda: real.model_copy(update={"sms_enabled": False}),
    )
    refused = TestClient(directory.app).post(
        "/api/v1/patients/otp/request", json={"phone": _PHONE}
    )
    assert refused.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert refused.json()["code"] == "patients.otp.sms_disabled"
    assert refused.json()["detail"].startswith(
        "Sign-in by SMS code is not available here."
    )
    assert join_on.sms.sent == []


# --------------------------------------------------------------------------------------
# The installed app's start page
# --------------------------------------------------------------------------------------


def test_the_app_start_page_offers_the_sign_in_to_nobody_and_not_to_a_signed_in_patient(
    directory: SimpleNamespace, join_on: SimpleNamespace
) -> None:
    anonymous = directory.client.get("/t/")
    assert anonymous.status_code == status.HTTP_200_OK
    assert anonymous.context["signed_in"] is False  # type: ignore[attr-defined]
    assert (
        "sign-in-phone-form" in anonymous.text
        and "patient-sign-in.js" in anonymous.text
    )

    client, _ = directory.patient_client()
    signed_in = client.get("/t/", follow_redirects=False)
    assert (
        signed_in.status_code == status.HTTP_200_OK
    )  # no open ticket today: the page, not a redirect
    assert signed_in.context["signed_in"] is True  # type: ignore[attr-defined]
    assert "sign-in-phone-form" not in signed_in.text
