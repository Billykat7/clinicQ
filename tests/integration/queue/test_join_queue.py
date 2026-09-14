"""Joining a queue: one service for every channel, and its guards (Issue 40, non-negotiable 1).

Every test here goes through :func:`src.modules.queue.service.join_queue`, over HTTP where a route
exists (the web join, the front desk's walk-in) and directly where the caller is a channel adapter
that M10 will build (USSD, WhatsApp), exactly as those adapters will call it. The criteria:

* **All four sources** are exercised against the same service function, and share one sequence.
* **A remote join and a walk-in in the same second** get adjacent numbers.
* **A second join by the same patient** returns the existing ticket, not a new one.
* **A closed or full queue** is refused with a reason a patient would understand.
* **The rate limiter** blocks bulk joins from one phone, and from one address on the web, and a
  clinic's daily cap bounds remote joins while the desk keeps working.
* **Consent given at join time** is stored with the ticket.

The same-instant races need a database that really runs transactions at once, so those two tests
are PostgreSQL-marked and build their own clinic.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from starlette import status

from src.commons.enums import (
    AuditAction,
    AuditEntityType,
    JoinRefusal,
    PatientChannel,
    SiteStatus,
    TicketSource,
)
from src.commons.time import now_sast
from src.core.audit import REDACTED
from src.database.models import (
    AuditEvent,
    Patient,
    Queue,
    Site,
    SiteClosure,
    SiteQueueSnapshot,
    Ticket,
)
from src.database.schema import apply_postgres_search_path
from src.modules.patients.service import patient_for_gateway
from src.modules.queue import service
from src.modules.queue.service import JoinRefusedError, JoinResult, join_queue
from src.modules.queues.service import WALK_IN_ONLY
from src.modules.sites.hours import published_schedules
from tests.factories import PatientFactory, QueueFactory, SiteFactory
from tests.integration.queue.conftest import open_all_day, queue_settings


def _gateway_join(
    db: Session,
    queue: Queue,
    channel: PatientChannel,
    msisdn: str,
    **options: object,
) -> JoinResult:
    """What a USSD or WhatsApp adapter (M10) does: resolve the vouched number, call the service."""
    patient = patient_for_gateway(db, msisdn=msisdn, channel=channel)
    site = db.get(Site, queue.site_id)
    assert site is not None
    return join_queue(
        db,
        site=site,
        queue=queue,
        schedule=published_schedules(db, [site.id])[site.id],
        source=TicketSource(channel.value),
        patient=patient,
        actor=f"{channel.value}-gateway",
        **options,  # type: ignore[arg-type]
    )


# --- one service, four doors, one sequence ------------------------------------------------------


def test_all_four_sources_join_through_the_one_service_and_share_one_sequence(
    desk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Web and reception over HTTP, USSD and WhatsApp as their adapters will: T001–T004 in order.

    A spy on ``join_queue`` records every call, so "the same service function" is observed, not
    assumed: the two routes and the two adapters all reach it, and nothing else issued a ticket.
    """
    seen: list[TicketSource] = []
    real = service.join_queue

    def spy(*args: object, **kwargs: object) -> JoinResult:
        seen.append(kwargs["source"])  # type: ignore[arg-type]
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "join_queue", spy)
    patient, _ = desk.patient()

    web = patient.post(desk.join_path(desk.triage), json={})
    walk_in = desk.staff("desk.a").post(
        desk.walk_in_path(desk.triage), json={"name": "Gogo M."}
    )
    with desk.session() as db:
        ussd = spy(
            db, **_gateway_kwargs(db, desk.triage, PatientChannel.USSD, "+27105550901")
        )
        whatsapp = spy(
            db,
            **_gateway_kwargs(db, desk.triage, PatientChannel.WHATSAPP, "+27105550902"),
        )
        db.commit()
        tickets = db.execute(
            select(Ticket.number, Ticket.source).order_by(Ticket.sequence)
        ).all()

    assert web.status_code == walk_in.status_code == status.HTTP_201_CREATED
    assert (web.json()["ticket"]["number"], walk_in.json()["ticket"]["number"]) == (
        "T001",
        "T002",
    )
    assert (ussd.ticket.number, whatsapp.ticket.number) == ("T003", "T004")
    assert seen == [
        TicketSource.WEB,
        TicketSource.WALK_IN,
        TicketSource.USSD,
        TicketSource.WHATSAPP,
    ]
    assert tickets == [
        ("T001", TicketSource.WEB.value),
        ("T002", TicketSource.WALK_IN.value),
        ("T003", TicketSource.USSD.value),
        ("T004", TicketSource.WHATSAPP.value),
    ]


def _gateway_kwargs(
    db: Session, queue: Queue, channel: PatientChannel, msisdn: str
) -> dict[str, object]:
    """The keyword arguments a gateway adapter passes to ``join_queue``."""
    patient = patient_for_gateway(db, msisdn=msisdn, channel=channel)
    site = db.get(Site, queue.site_id)
    assert site is not None
    return {
        "site": site,
        "queue": queue,
        "schedule": published_schedules(db, [site.id])[site.id],
        "source": TicketSource(channel.value),
        "patient": patient,
        "actor": f"{channel.value}-gateway",
    }


def test_a_remote_join_and_a_walk_in_in_the_same_second_get_adjacent_numbers(
    desk: SimpleNamespace,
) -> None:
    """The fairness criterion at one instant: a phone and the desk, one second, numbers n and n+1."""
    moment = now_sast().replace(microsecond=0)
    with desk.session() as db:
        site = db.get(Site, desk.triage.site_id)
        schedule = published_schedules(db, [site.id])[site.id]
        remote = _gateway_join(
            db, desk.triage, PatientChannel.USSD, "+27105550911", moment=moment
        )
        walk_in = join_queue(
            db,
            site=site,
            queue=desk.triage,
            schedule=schedule,
            source=TicketSource.WALK_IN,
            patient=None,
            actor="desk.a@clinicq.example",
            moment=moment + timedelta(milliseconds=400),
        )
        db.commit()
    assert remote.ticket.joined_at.replace(
        microsecond=0
    ) == walk_in.ticket.joined_at.replace(microsecond=0)
    assert (remote.ticket.sequence, walk_in.ticket.sequence) == (1, 2)
    assert (
        walk_in.waiting_ahead == 1
    )  # the phone join is ahead: arrival order, not channel


# --- one ticket per patient per queue ------------------------------------------------------------


def test_a_second_join_by_the_same_patient_returns_the_existing_ticket(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 1: the second call answers 200 with the first ticket, and no new row."""
    patient, patient_id = desk.patient()

    first = patient.post(desk.join_path(desk.triage), json={})
    second = patient.post(desk.join_path(desk.triage), json={"reason_text": "again"})

    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_200_OK
    assert second.json()["created"] is False
    assert second.json()["ticket"]["id"] == first.json()["ticket"]["id"]
    assert second.json()["message"] == "You already hold T001 in this queue."
    # The same instant both times, with its Johannesburg offset even when read back from storage.
    assert second.json()["ticket"]["joined_at"] == first.json()["ticket"]["joined_at"]
    assert first.json()["ticket"]["joined_at"].endswith("+02:00")
    with desk.session() as db:
        held = db.scalar(
            select(func.count(Ticket.id)).where(Ticket.patient_id == patient_id)
        )
    assert held == 1
    mine = patient.get("/api/v1/patients/me/tickets").json()
    assert [ticket["number"] for ticket in mine] == ["T001"]


# --- refusals a patient understands ----------------------------------------------------------


def test_joining_a_closed_clinic_is_refused_with_a_reason_a_patient_understands(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 3: a closure announced for today refuses the join and says why."""
    with desk.session() as db:
        db.add(
            SiteClosure(
                site_id=desk.triage.site_id,
                reason="Water outage",
                starts_at=now_sast() - timedelta(hours=1),
                ends_at=now_sast() + timedelta(hours=3),
            )
        )
        db.commit()
    patient, _ = desk.patient()

    refused = patient.post(desk.join_path(desk.triage), json={})

    assert refused.status_code == status.HTTP_409_CONFLICT
    assert refused.json()["code"] == f"queue.join.{JoinRefusal.CLINIC_CLOSED.value}"
    assert refused.json()["detail"].endswith("is closed: Water outage")
    # The desk is shut by the same gate: one closure closes all four doors at once.
    at_desk = desk.staff("desk.a").post(desk.walk_in_path(desk.triage), json={})
    assert at_desk.json()["code"] == f"queue.join.{JoinRefusal.CLINIC_CLOSED.value}"


def test_a_walk_in_only_queue_refuses_a_phone_and_takes_the_desk(
    desk: SimpleNamespace,
) -> None:
    """The pharmacy: no remote ticket, a plain sentence why, and the desk still issues one."""
    patient, _ = desk.patient()

    refused = patient.post(desk.join_path(desk.pharmacy), json={})
    issued = desk.staff("desk.a").post(desk.walk_in_path(desk.pharmacy), json={})

    assert refused.status_code == status.HTTP_409_CONFLICT
    assert refused.json() | {"request_id": None} == {
        "detail": WALK_IN_ONLY,
        "code": f"queue.join.{JoinRefusal.WALK_IN_ONLY.value}",
        "request_id": None,
    }
    assert issued.status_code == status.HTTP_201_CREATED
    assert issued.json()["ticket"]["number"] == "P001"


def test_a_deactivated_queue_refuses_every_door(desk: SimpleNamespace) -> None:
    """A closed line is closed to the desk as well as to phones."""
    with desk.session() as db:
        db.get(Queue, desk.triage.id).is_active = False
        db.commit()
    patient, _ = desk.patient()

    for response in (
        patient.post(desk.join_path(desk.triage), json={}),
        desk.staff("desk.a").post(desk.walk_in_path(desk.triage), json={}),
    ):
        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["code"] == f"queue.join.{JoinRefusal.QUEUE_CLOSED.value}"
        assert (
            response.json()["detail"]
            == "This queue is not taking patients at the moment."
        )


def test_a_full_queue_refuses_and_gives_the_number_back(desk: SimpleNamespace) -> None:
    """Capacity 2: the third join is refused, and raising the cap issues T003, not T004."""
    with desk.session() as db:
        db.get(Queue, desk.triage.id).max_daily_capacity = 2
        db.commit()
    front = desk.staff("desk.a")

    numbers = [
        front.post(desk.walk_in_path(desk.triage), json={}).json()["ticket"]["number"]
        for _ in range(2)
    ]
    full = front.post(desk.walk_in_path(desk.triage), json={})
    with desk.session() as db:
        db.get(Queue, desk.triage.id).max_daily_capacity = 3
        db.commit()
    after = front.post(desk.walk_in_path(desk.triage), json={})

    assert numbers == ["T001", "T002"]
    assert full.status_code == status.HTTP_409_CONFLICT
    assert full.json()["code"] == f"queue.join.{JoinRefusal.QUEUE_FULL.value}"
    assert full.json()["detail"] == service.QUEUE_FULL
    assert after.json()["ticket"]["number"] == "T003"  # the refused join left no gap


# --- abuse guards ---------------------------------------------------------------------------------


def test_the_rate_limiter_blocks_bulk_joins_from_one_phone(
    desk: SimpleNamespace,
) -> None:
    """Two joins an hour per number here: the third queue this phone tries is refused.

    Spread over three queues at two clinics, so it is the phone being limited, not a duplicate.
    """
    settings = queue_settings(queue_join_rate_limit_per_phone=2)
    with desk.session() as db:
        extra = QueueFactory.create(
            db, site_id=desk.other_triage.site_id, ticket_prefix="X"
        )
        db.commit()
        for queue in (desk.triage, desk.other_triage):
            _gateway_join(
                db, queue, PatientChannel.USSD, "+27105550920", settings=settings
            )
        with pytest.raises(JoinRefusedError) as limited:
            _gateway_join(
                db, extra, PatientChannel.USSD, "+27105550920", settings=settings
            )
        # Another number is not affected.
        other = _gateway_join(
            db, extra, PatientChannel.USSD, "+27105550921", settings=settings
        )
        db.commit()
    assert limited.value.refusal is JoinRefusal.RATE_LIMITED
    assert str(limited.value) == service.RATE_LIMITED
    assert other.created is True


def test_the_web_path_is_limited_per_address(
    desk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three patients from one address, a budget of two: the third is a 429 with Retry-After."""
    monkeypatch.setattr(desk.settings, "queue_join_rate_limit_per_ip", 2)
    answers = []
    for _ in range(3):
        patient, _ = desk.patient()
        answers.append(patient.post(desk.join_path(desk.triage), json={}))

    assert [answer.status_code for answer in answers] == [201, 201, 429]
    assert answers[2].json()["detail"] == service.RATE_LIMITED
    assert answers[2].headers["Retry-After"] == str(
        desk.settings.queue_join_rate_limit_window_seconds
    )


def test_a_clinic_takes_a_capped_number_of_remote_joins_a_day_and_the_desk_keeps_working(
    desk: SimpleNamespace,
) -> None:
    """A cap of two remote joins: the third phone is refused, a walk-in is still issued."""
    settings = queue_settings(queue_join_site_daily_cap=2)
    with desk.session() as db:
        for msisdn in ("+27105550930", "+27105550931"):
            _gateway_join(
                db, desk.triage, PatientChannel.WHATSAPP, msisdn, settings=settings
            )
        with pytest.raises(JoinRefusedError) as capped:
            _gateway_join(
                db,
                desk.triage,
                PatientChannel.WHATSAPP,
                "+27105550932",
                settings=settings,
            )
        db.commit()
    walk_in = desk.staff("desk.a").post(desk.walk_in_path(desk.triage), json={})

    assert capped.value.refusal is JoinRefusal.SITE_DAILY_CAP
    assert walk_in.status_code == status.HTTP_201_CREATED
    assert walk_in.json()["ticket"]["number"] == "T003"


# --- what a join records ----------------------------------------------------------------------


def test_consent_given_at_join_is_persisted_with_the_ticket_and_the_audit_keeps_no_reason(
    desk: SimpleNamespace,
) -> None:
    """Criterion 6: the reason and the per-visit consent are on the ticket; the audit row redacts."""
    patient, patient_id = desk.patient()

    joined = patient.post(
        desk.join_path(desk.triage),
        json={"reason_text": "  repeat script  ", "comment_consent": True},
    )

    ticket_id = joined.json()["ticket"]["id"]
    with desk.session() as db:
        ticket = db.get(Ticket, ticket_id)
        audit = db.execute(
            select(AuditEvent).where(AuditEvent.entity_id == ticket_id)
        ).scalar_one()
        snapshot = db.get(SiteQueueSnapshot, desk.triage.id)
    assert ticket is not None
    assert (ticket.reason_text, ticket.comment_consent) == ("repeat script", True)
    assert ticket.patient_id == patient_id and ticket.walk_in_name is None
    assert (audit.action, audit.entity_type) == (
        AuditAction.CREATE.value,
        AuditEntityType.TICKET.value,
    )
    assert audit.actor == f"patient:{patient_id}"
    assert audit.context == "joined Triage as T001 via web"
    assert audit.diff["reason_text"]["after"] == REDACTED
    assert audit.diff["comment_consent"]["after"] is True
    # Written through to the queue snapshot in the same request (Issue 36's hook).
    assert snapshot is not None and snapshot.waiting == 1


def test_a_walk_in_with_a_phone_is_linked_to_that_patient_and_cannot_hold_two(
    desk: SimpleNamespace,
) -> None:
    """The desk may record a number; the walk-in then counts as that patient's one ticket."""
    front = desk.staff("desk.a")
    first = front.post(desk.walk_in_path(desk.triage), json={"phone": "010 555 0940"})
    again = front.post(desk.walk_in_path(desk.triage), json={"phone": "+27105550940"})

    assert first.status_code == status.HTTP_201_CREATED
    assert again.status_code == status.HTTP_200_OK and again.json()["created"] is False
    with desk.session() as db:
        patient = db.execute(
            select(Patient).where(Patient.phone_e164 == "+27105550940")
        ).scalar_one()
    assert patient.last_channel == PatientChannel.WALK_IN.value


def test_the_desk_cannot_issue_into_another_clinics_queue(
    desk: SimpleNamespace,
) -> None:
    """Non-negotiable 3: Clinic B's queue is a 404 to Clinic A's desk, the same as no queue at all."""
    front = desk.staff("desk.a")
    other = front.post(
        f"/api/v1/sites/{desk.other_triage.site_id}/queues/{desk.other_triage.id}/tickets",
        json={},
    )
    assert other.status_code == status.HTTP_404_NOT_FOUND


def test_a_remote_join_without_its_patient_is_a_programming_error(
    desk: SimpleNamespace,
) -> None:
    """No channel can issue an anonymous phone ticket; the message names the rule."""
    with desk.session() as db:
        site = db.get(Site, desk.triage.site_id)
        with pytest.raises(ValueError, match="belongs to the patient who joined"):
            join_queue(
                db,
                site=site,
                queue=desk.triage,
                schedule=published_schedules(db, [site.id])[site.id],
                source=TicketSource.USSD,
                patient=None,
                actor="ussd-gateway",
            )


# --- the same instant, on PostgreSQL ----------------------------------------------------------


@pytest.fixture
def pg_clinic(migrated_database: URL) -> Iterator[SimpleNamespace]:
    """A listed, open clinic with one queue on PostgreSQL, and a pooled session factory."""
    engine = create_engine(migrated_database, pool_size=8, max_overflow=0)
    apply_postgres_search_path(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        site = SiteFactory.create(db, status=SiteStatus.VERIFIED)
        open_all_day(db, site.id)
        queue = QueueFactory.create(db, site_id=site.id, ticket_prefix="T")
        patient = PatientFactory.create(db)
        db.commit()
    yield SimpleNamespace(session=factory, site=site, queue=queue, patient=patient)
    engine.dispose()


def _at_once(*jobs: object) -> list[object]:
    """Run each zero-argument job in its own thread, released together; return results in order."""
    barrier = threading.Barrier(len(jobs))
    results: list[object] = [None] * len(jobs)

    def run(index: int, job: object) -> None:
        barrier.wait()
        results[index] = job()  # type: ignore[operator]

    threads = [threading.Thread(target=run, args=pair) for pair in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


@pytest.mark.postgres
def test_a_phone_and_the_desk_racing_twenty_times_always_get_adjacent_numbers(
    pg_clinic: SimpleNamespace,
) -> None:
    """Twenty rounds of one USSD join and one walk-in released together: each pair is n and n+1."""

    def remote(msisdn: str) -> int:
        with pg_clinic.session() as db:
            queue = db.get(Queue, pg_clinic.queue.id)
            result = _gateway_join(db, queue, PatientChannel.USSD, msisdn)
            db.commit()
            return result.ticket.sequence

    def walk_in() -> int:
        with pg_clinic.session() as db:
            site = db.get(Site, pg_clinic.site.id)
            result = join_queue(
                db,
                site=site,
                queue=db.get(Queue, pg_clinic.queue.id),
                schedule=published_schedules(db, [pg_clinic.site.id])[
                    pg_clinic.site.id
                ],
                source=TicketSource.WALK_IN,
                patient=None,
                actor="desk@clinicq.example",
            )
            db.commit()
            return result.ticket.sequence

    pairs = []
    for n in range(20):
        answers = _at_once(lambda n=n: remote(f"+2710555{1000 + n:04d}"), walk_in)
        assert all(isinstance(answer, int) for answer in answers), answers
        pairs.append(sorted(answers))
    assert all(high == low + 1 for low, high in pairs), pairs
    assert [low for low, _ in pairs] == list(range(1, 40, 2))


@pytest.mark.postgres
def test_two_joins_by_one_patient_at_the_same_instant_make_one_ticket(
    pg_clinic: SimpleNamespace,
) -> None:
    """The duplicate check races; the partial unique index settles it, and both get the same ticket."""

    def join() -> tuple[str, bool]:
        with pg_clinic.session() as db:
            site = db.get(Site, pg_clinic.site.id)
            result = join_queue(
                db,
                site=site,
                queue=db.get(Queue, pg_clinic.queue.id),
                schedule=published_schedules(db, [pg_clinic.site.id])[
                    pg_clinic.site.id
                ],
                source=TicketSource.WEB,
                patient=db.get(Patient, pg_clinic.patient.id),
                actor=f"patient:{pg_clinic.patient.id}",
                # Both racers pass the duplicate check and so both reach the phone limiter; this
                # test is about the index, not the limit.
                settings=queue_settings(queue_join_rate_limit_per_phone=1000),
            )
            db.commit()
            return result.ticket.id, result.created

    for _ in range(5):
        with pg_clinic.session() as db:
            db.execute(Ticket.__table__.delete())
            db.commit()
        answers = _at_once(join, join)
        ids = {ticket_id for ticket_id, _ in answers}  # type: ignore[misc]
        assert len(ids) == 1
        assert sorted(created for _, created in answers) == [False, True]  # type: ignore[misc]
