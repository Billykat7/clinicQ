"""One phone acting for a household, over HTTP (Issue 84).

Against the queue engine's clinic A, with the real one-time-code store and a fake SMS provider:

* **linking a number needs the code sent to it**: without it, nobody can attach themselves to another
  person's record; with it, the link is made and the dependant's consent recorded;
* **a dependant with no phone of their own** (a small child) is a new record the proxy creates;
* **joining and booking for a dependant**: the ticket and the booking are theirs, the audit row names
  both people, and the ticket carries who acted;
* **the messages go to the proxy's phone**, because it is the phone that exists;
* **ending the link stops the next action at once**, from either side.
"""

from __future__ import annotations

import re
from collections.abc import Generator, Iterator
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import (
    AppEnvironment,
    AuditEntityType,
    ConsentPurpose,
    NotificationStatus,
    SiteStatus,
    UserRole,
)
from src.commons.time import business_date, now_sast
from src.core import otp_store, refresh_token_policy, security
from src.core.config import Settings, get_settings
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.database.models import (
    AppointmentSlot,
    AuditEvent,
    Base,
    Notification,
    Patient,
    PatientConsent,
    PatientLink,
    Queue,
    Ticket,
)
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app
from src.modules.notifications import service as notification_service
from src.modules.notifications.sms import FakeSmsProvider
from src.modules.queue.snapshot import NoSnapshotCache, set_snapshot_cache
from tests.factories import QueueFactory, SiteFactory, StaffFactory
from tests.integration.queue.conftest import SITE_A, open_all_day

PROXY_PHONE = "+27820000841"
GOGO_PHONE = "+27820000842"


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """One open clinic with a queue, a signed-in patient, and the SMS the test reads codes from."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret="proxy-booking-test-secret-min-32-characters",
        auth_password_login_enabled=True,
        smtp_host="",
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    set_snapshot_cache(NoSnapshotCache())
    with factory() as db:
        sync_rbac_catalog(db)
        SiteFactory.create(db, id=SITE_A, status=SiteStatus.VERIFIED)
        open_all_day(db, SITE_A)
        triage = QueueFactory.create(
            db, site_id=SITE_A, name="Triage", slug="triage", ticket_prefix="T"
        )
        StaffFactory.create(
            db,
            email="manager.a@clinicq.example",
            role=UserRole.CLINIC_MANAGER,
            site_id=SITE_A,
        )
        db.commit()

    def _db() -> Generator[Session]:
        with factory() as db:
            yield db

    sms = FakeSmsProvider()
    for module in (security, otp_store, refresh_token_policy):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
    monkeypatch.setattr(notification_service, "build_sms_provider", lambda: sms)
    otp_store.reset_otp_state()
    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    def sign_in(phone: str) -> TestClient:
        signing_in = TestClient(app)
        assert (
            signing_in.post(
                "/api/v1/patients/otp/request", json={"phone": phone}
            ).status_code
            == status.HTTP_202_ACCEPTED
        )
        verified = signing_in.post(
            "/api/v1/patients/otp/verify",
            json={"phone": phone, "code": _code(sms)},
        )
        assert verified.status_code == status.HTTP_200_OK, verified.text
        signing_in.headers["X-CSRF-Token"] = signing_in.cookies.get(
            settings.csrf_cookie_name
        )
        return signing_in

    proxy_client = sign_in(PROXY_PHONE)
    yield SimpleNamespace(
        app=app,
        client=client,
        proxy=proxy_client,
        sign_in=sign_in,
        session=factory,
        settings=settings,
        sms=sms,
        triage=triage,
    )
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _code(sms: FakeSmsProvider) -> str:
    """The six digits of the last message sent, as a patient reads them off their phone."""
    found = re.search(r"\b(\d{6})\b", sms.sent[-1].text)
    assert found is not None, sms.sent[-1].text
    return found.group(1)


def _link_child(world: SimpleNamespace, name: str = "Lesedi") -> dict:
    """A dependant with no phone of their own: the flagship case, a small child."""
    made = world.proxy.post(
        "/api/v1/patients/me/dependants",
        json={"name": name, "relationship": "child"},
    )
    assert made.status_code == status.HTTP_201_CREATED, made.text
    return made.json()["items"][-1]


def _link_by_code(world: SimpleNamespace, phone: str = GOGO_PHONE) -> dict:
    """A dependant who has their own phone: the code goes to that phone."""
    asked = world.proxy.post(
        "/api/v1/patients/me/dependants/code", json={"phone": phone}
    )
    assert asked.status_code == status.HTTP_202_ACCEPTED, asked.text
    made = world.proxy.post(
        "/api/v1/patients/me/dependants",
        json={
            "phone": phone,
            "code": _code(world.sms),
            "relationship": "parent",
            "name": "Gogo",
        },
    )
    assert made.status_code == status.HTTP_201_CREATED, made.text
    return made.json()["items"][-1]


def _join_for(world: SimpleNamespace, dependant_id: str | None):
    return world.proxy.post(
        f"/api/v1/clinics/{SITE_A}/queues/{world.triage.id}/tickets",
        json={"for_patient_id": dependant_id} if dependant_id else {},
    )


def test_a_number_is_linked_only_with_the_code_sent_to_it(
    world: SimpleNamespace,
) -> None:
    """How to verify, step 3: typing somebody's number is not enough to act for them."""
    world.proxy.post("/api/v1/patients/me/dependants/code", json={"phone": GOGO_PHONE})

    without = world.proxy.post(
        "/api/v1/patients/me/dependants",
        json={"phone": GOGO_PHONE, "relationship": "parent", "name": "Gogo"},
    )
    wrong = world.proxy.post(
        "/api/v1/patients/me/dependants",
        json={
            "phone": GOGO_PHONE,
            "code": "000000",
            "relationship": "parent",
            "name": "Gogo",
        },
    )

    assert without.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert wrong.status_code == status.HTTP_400_BAD_REQUEST
    assert wrong.json()["code"].startswith("patients.otp.")
    with world.session() as db:
        assert db.execute(select(PatientLink)).scalars().all() == []


def test_the_code_makes_the_link_and_records_the_dependants_consent(
    world: SimpleNamespace,
) -> None:
    """The person being acted for consents, by the person acting, at the moment of the link."""
    linked = _link_by_code(world)

    assert linked["name"] == "Gogo" and linked["has_phone"] is True
    with world.session() as db:
        link = db.execute(select(PatientLink)).scalar_one()
        assert link.dependant_patient_id == linked["patient_id"]
        assert link.verified_at is not None and link.is_active
        consent = db.execute(
            select(PatientConsent).where(
                PatientConsent.patient_id == linked["patient_id"],
                PatientConsent.purpose == ConsentPurpose.PROXY_ACTIONS.value,
            )
        ).scalar_one()
        assert consent.granted is True
        made = (
            db.execute(
                select(AuditEvent).where(
                    AuditEvent.entity_type == AuditEntityType.PATIENT.value,
                    AuditEvent.entity_id == linked["patient_id"],
                )
            )
            .scalars()
            .all()
        )
    assert any("link made" in (row.context or "") for row in made)


def test_a_dependant_with_no_phone_is_a_record_nobody_can_sign_in_as(
    world: SimpleNamespace,
) -> None:
    """A small child: nothing to verify, because there is no number and no record to take over."""
    child = _link_child(world)

    assert child["has_phone"] is False and child["name"] == "Lesedi"
    with world.session() as db:
        record = db.get_one(Patient, child["patient_id"])
        assert record.phone_e164 is None
        assert record.display_name == "Lesedi"


def test_a_proxy_joins_for_a_dependant_and_the_ticket_is_the_dependants(
    world: SimpleNamespace,
) -> None:
    """How to verify, step 1: the ticket belongs to the person being seen, and says who took it."""
    child = _link_child(world)

    joined = _join_for(world, child["patient_id"])

    assert joined.status_code == status.HTTP_201_CREATED, joined.text
    with world.session() as db:
        ticket = db.execute(select(Ticket)).scalar_one()
        proxy_id = db.execute(select(PatientLink.proxy_patient_id)).scalar_one()
        assert ticket.patient_id == child["patient_id"]
        assert ticket.proxy_patient_id == proxy_id
        rows = (
            db.execute(
                select(AuditEvent).where(AuditEvent.entity_id == child["patient_id"])
            )
            .scalars()
            .all()
        )
    assert any("on behalf" in (row.context or "") for row in rows)
    assert all(row.actor == f"patient:{proxy_id}" for row in rows)


def test_the_dependants_messages_go_to_the_proxys_phone(
    world: SimpleNamespace,
) -> None:
    """How to verify, step 1 again: the phone that rings is the one that exists."""
    child = _link_child(world)
    agreed = world.proxy.put(
        "/api/v1/patients/me/consents/notifications", json={"granted": True}
    )
    assert agreed.status_code == status.HTTP_200_OK, agreed.text

    _join_for(world, child["patient_id"])
    with world.session() as db:
        ticket = db.execute(select(Ticket)).scalar_one()
        queue = db.get_one(Queue, ticket.queue_id)
        from src.commons.enums import ActorKind, TicketStatus
        from src.modules.queue.lifecycle import Actor, transition_ticket

        transition_ticket(
            db,
            ticket.id,
            TicketStatus.CALLED,
            actor=Actor(kind=ActorKind.STAFF, label="desk.a@clinicq.example"),
        )
        db.commit()
        assert queue is not None
        sent = (
            db.execute(
                select(Notification).where(
                    Notification.patient_id == child["patient_id"]
                )
            )
            .scalars()
            .all()
        )

    assert sent, "the dependant's call was not recorded as a message at all"
    assert {row.recipient for row in sent} == {PROXY_PHONE}
    assert all(row.status != NotificationStatus.SUPPRESSED.value for row in sent), [
        (row.status, row.last_error) for row in sent
    ]


def test_a_proxy_books_for_a_dependant_and_the_booking_is_theirs(
    world: SimpleNamespace,
) -> None:
    """A daughter books for her mother: the reference, the time and the ticket are her mother's."""
    gogo = _link_by_code(world)
    starts = now_sast().replace(second=0, microsecond=0) + timedelta(hours=2)
    with world.session() as db:
        slot = AppointmentSlot(
            site_id=SITE_A,
            queue_id=world.triage.id,
            starts_at=starts,
            ends_at=starts + timedelta(minutes=15),
            service_day=business_date(starts),
            capacity=2,
        )
        db.add(slot)
        db.commit()
        slot_id = slot.id

    booked = world.proxy.post(
        f"/api/v1/clinics/{SITE_A}/appointments",
        json={"slot_id": slot_id, "for_patient_id": gogo["patient_id"]},
    )
    mine = world.proxy.get("/api/v1/patients/me/appointments")
    theirs = world.proxy.get(
        "/api/v1/patients/me/appointments",
        params={"for_patient_id": gogo["patient_id"]},
    )

    assert booked.status_code == status.HTTP_201_CREATED, booked.text
    assert mine.json()["total"] == 0, "the proxy has no booking of their own"
    assert [row["reference"] for row in theirs.json()["items"]] == [
        booked.json()["reference"]
    ]
    with world.session() as db:
        from src.database.models import Appointment

        appointment = db.execute(select(Appointment)).scalar_one()
        proxy_id = db.execute(select(PatientLink.proxy_patient_id)).scalar_one()
        assert appointment.patient_id == gogo["patient_id"]
        assert appointment.proxy_patient_id == proxy_id


def test_ending_the_link_refuses_the_next_action_at_once(
    world: SimpleNamespace,
) -> None:
    """How to verify, step 2: revoked means revoked, and the tickets already taken stay."""
    child = _link_child(world)
    first = _join_for(world, child["patient_id"])
    assert first.status_code == status.HTTP_201_CREATED

    ended = world.proxy.delete(f"/api/v1/patients/me/dependants/{child['link_id']}")
    again = _join_for(world, child["patient_id"])
    listed = world.proxy.get("/api/v1/patients/me/dependants")

    assert ended.status_code == status.HTTP_200_OK, ended.text
    assert again.status_code == status.HTTP_403_FORBIDDEN
    assert again.json()["code"] == "patients.link.revoked"
    assert listed.json()["total"] == 0
    with world.session() as db:
        assert db.execute(select(Ticket)).scalar_one().patient_id == child["patient_id"]
        withdrawn = db.execute(
            select(PatientConsent).where(
                PatientConsent.patient_id == child["patient_id"],
                PatientConsent.purpose == ConsentPurpose.PROXY_ACTIONS.value,
            )
        ).scalar_one()
        assert withdrawn.granted is False


def test_the_dependant_can_end_it_too(world: SimpleNamespace) -> None:
    """Either side may end a link: the person being acted for is not locked into it."""
    gogo = _link_by_code(world)
    gogo_client = world.sign_in(GOGO_PHONE)

    ended = gogo_client.delete(f"/api/v1/patients/me/dependants/{gogo['link_id']}")
    again = world.proxy.post(
        f"/api/v1/clinics/{SITE_A}/queues/{world.triage.id}/tickets",
        json={"for_patient_id": gogo["patient_id"]},
    )

    assert ended.status_code == status.HTTP_200_OK, ended.text
    assert again.status_code == status.HTTP_403_FORBIDDEN


def test_somebody_elses_dependant_is_the_same_not_found(
    world: SimpleNamespace,
) -> None:
    """A patient cannot act for a person they hold no link to, whoever that person is."""
    child = _link_child(world)
    stranger = world.sign_in("+27820000843")

    joined = stranger.post(
        f"/api/v1/clinics/{SITE_A}/queues/{world.triage.id}/tickets",
        json={"for_patient_id": child["patient_id"]},
    )
    invented = stranger.post(
        f"/api/v1/clinics/{SITE_A}/queues/{world.triage.id}/tickets",
        json={"for_patient_id": "01a0a000-0000-7000-8000-000000000000"},
    )

    assert joined.status_code == invented.status_code == status.HTTP_404_NOT_FOUND
    assert joined.json()["code"] == invented.json()["code"] == "patients.link.not_found"


def test_a_patient_cannot_link_their_own_number_or_hoard_links(
    world: SimpleNamespace,
) -> None:
    """Their own number needs no link, and one phone may act for a household, not a village."""
    own = world.proxy.post(
        "/api/v1/patients/me/dependants/code", json={"phone": PROXY_PHONE}
    )

    from src.modules.patients.proxy import MAX_DEPENDANTS

    for number in range(MAX_DEPENDANTS):
        _link_child(world, name=f"Child {number}")
    too_many = world.proxy.post(
        "/api/v1/patients/me/dependants",
        json={"name": "One more", "relationship": "child"},
    )

    assert own.status_code == status.HTTP_409_CONFLICT
    assert own.json()["code"] == "patients.link.self"
    assert too_many.status_code == status.HTTP_409_CONFLICT
    assert too_many.json()["code"] == "patients.link.too_many"
