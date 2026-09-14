"""Recall timers and automatic no-shows (Issue 43).

The clock is advanced by passing ``moment`` to the sweep, never by sleeping. The criteria:

* a called ticket not attended within the timeout **auto-recalls exactly once**;
* a second timeout marks it **no-show and frees the room**;
* the patient is **told at both steps**, with how to rejoin (recorded in the notification ledger
  through the provider, gated by their consent);
* timers **survive a worker restart and do not double-fire**, proved on PostgreSQL by running the
  sweep in fresh interpreter processes before, across and after a restart, and two at once;
* staff can **override either step immediately**;
* automatic moves are **audited as the system**, distinguishable from staff.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from starlette import status

from src.commons.enums import (
    ActorKind,
    AuditAction,
    AuditEntityType,
    ConsentPurpose,
    NotificationStatus,
    NotificationTemplate,
    PatientChannel,
    SiteStatus,
    TicketSource,
    TicketStatus,
)
from src.commons.time import now_sast
from src.core import scheduler
from src.core.audit import SYSTEM_ACTOR
from src.core.config import Settings
from src.database.models import (
    AuditEvent,
    Notification,
    Patient,
    Queue,
    Site,
    Ticket,
)
from src.database.schema import apply_postgres_search_path
from src.modules.notifications.sms import FakeSmsProvider
from src.modules.patients.consent import record_consent
from src.modules.queue.lifecycle import Actor, call_next, transition_ticket
from src.modules.queue.timers import run_recall_timers, timeout_minutes
from tests.factories import PatientFactory, QueueFactory, SiteFactory, TicketFactory
from tests.integration.queue.conftest import open_all_day, queue_settings

_REPO = Path(__file__).resolve().parents[3]
_DESK = Actor(kind=ActorKind.STAFF, label="desk.a@clinicq.example")


def _called_ticket(
    db: Session, queue_id: str, *, called_at: datetime, consent: bool = True
) -> Ticket:
    """A remote patient's ticket in ``queue_id``, called at ``called_at``; consent to messages given."""
    patient = PatientFactory.create(db)
    if consent:
        record_consent(
            db,
            patient,
            ConsentPurpose.NOTIFICATIONS,
            granted=True,
            channel=PatientChannel.WEB,
        )
    queue = db.get(Queue, queue_id)
    ticket = TicketFactory.create(
        db,
        queue=queue,
        source=TicketSource.WEB,
        patient_id=patient.id,
        moment=called_at - timedelta(minutes=20),
    )
    return transition_ticket(
        db, ticket.id, TicketStatus.CALLED, actor=_DESK, moment=called_at
    )


def _sweep(db: Session, at: datetime, provider: FakeSmsProvider, **settings: object):
    swept = run_recall_timers(
        db, moment=at, settings=queue_settings(**settings), sms_provider=provider
    )
    db.commit()
    return swept


def _system_rows(db: Session, ticket_id: str) -> list[AuditEvent]:
    return list(
        db.execute(
            select(AuditEvent)
            .where(
                AuditEvent.entity_type == AuditEntityType.TICKET.value,
                AuditEvent.entity_id == ticket_id,
                AuditEvent.action == AuditAction.UPDATE.value,
                AuditEvent.actor_role == ActorKind.SYSTEM.value,
            )
            .order_by(AuditEvent.created_at)
        ).scalars()
    )


def test_a_called_ticket_recalls_exactly_once_then_becomes_a_no_show_that_frees_the_room(
    desk: SimpleNamespace,
) -> None:
    """How to verify, steps 1 and 2: advance past the timeout, then past it again."""
    provider = FakeSmsProvider()
    # A fixed hour of one service day, so the call, both sweeps and Call next fall on the same day
    # whatever time the suite runs (an hour before now crossed midnight between 00:00 and 01:00).
    called = (now_sast() - timedelta(days=1)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    with desk.session() as db:
        ticket = _called_ticket(db, desk.triage.id, called_at=called)
        behind = TicketFactory.create(
            db, queue=db.get(Queue, desk.triage.id), moment=called
        )
        db.commit()

        early = _sweep(db, called + timedelta(minutes=4, seconds=59), provider)
        first = _sweep(db, called + timedelta(minutes=5, seconds=1), provider)
        # Again at the same instant and a little later: nothing more is due.
        again = [
            _sweep(db, called + timedelta(minutes=5, seconds=1), provider),
            _sweep(db, called + timedelta(minutes=9), provider),
        ]
        db.expire_all()
        recalled = db.get(Ticket, ticket.id)
        assert recalled is not None

        assert (early.moved, first.recalled, [s.moved for s in again]) == (
            0,
            [ticket.id],
            [0, 0],
        )
        assert recalled.status == TicketStatus.RECALLED.value and recalled.recalled_at

        second = _sweep(db, called + timedelta(minutes=10, seconds=2), provider)
        db.expire_all()
        missed = db.get(Ticket, ticket.id)
        assert missed is not None
        assert second.no_shows == [ticket.id]
        assert missed.status == TicketStatus.NO_SHOW.value and missed.completed_at

        # The room is free: nothing is called or recalled in it, and Call next moves on.
        occupying = db.scalars(
            select(Ticket.id).where(
                Ticket.queue_id == desk.triage.id,
                Ticket.status.in_(
                    [TicketStatus.CALLED.value, TicketStatus.RECALLED.value]
                ),
            )
        ).all()
        assert occupying == []
        assert (
            call_next(
                db,
                db.get(Queue, desk.triage.id),
                actor=_DESK,
                moment=called + timedelta(minutes=11),
            ).id
            == behind.id
        )
        db.commit()

        rows = _system_rows(db, ticket.id)
        assert [row.diff for row in rows] == [
            {"status": {"before": "called", "after": "recalled"}},
            {"status": {"before": "recalled", "after": "no_show"}},
        ]
        assert {row.actor for row in rows} == {SYSTEM_ACTOR}
        assert [row.context.split(": ", 1)[1] for row in rows] == [
            "called → recalled (recall timer: not attended within 5 min)",
            "recalled → no_show (no-show timer: not attended within 5 min)",
        ]
        # The staff call is on the trail too, and is plainly not the system's.
        staff = (
            db.execute(
                select(AuditEvent.actor_role).where(
                    AuditEvent.entity_id == ticket.id, AuditEvent.actor == _DESK.label
                )
            )
            .scalars()
            .all()
        )
        assert staff == [ActorKind.STAFF.value]


def test_the_patient_is_told_at_both_steps_and_how_to_rejoin(
    desk: SimpleNamespace,
) -> None:
    """Criterion 3: two SMS through the provider, each on the ledger with a plain explanation."""
    provider = FakeSmsProvider()
    # A fixed hour of one service day, so the call, both sweeps and Call next fall on the same day
    # whatever time the suite runs (an hour before now crossed midnight between 00:00 and 01:00).
    called = (now_sast() - timedelta(days=1)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    with desk.session() as db:
        ticket = _called_ticket(db, desk.triage.id, called_at=called)
        phone = db.get(Patient, ticket.patient_id).phone_e164
        db.commit()
        _sweep(db, called + timedelta(minutes=6), provider)
        _sweep(db, called + timedelta(minutes=12), provider)
        ledger = db.execute(
            select(
                Notification.template_key, Notification.status, Notification.recipient
            )
            .where(Notification.recipient == phone)
            .order_by(Notification.created_at)
        ).all()

    assert [row.template_key for row in ledger] == [
        NotificationTemplate.TICKET_RECALLED.value,
        NotificationTemplate.TICKET_NO_SHOW.value,
    ]
    assert {row.status for row in ledger} == {NotificationStatus.SENT.value}
    recall_text, no_show_text = (sms.text for sms in provider.sent)
    assert (
        f"ticket {ticket.number}" in recall_text
        and "called you once more" in recall_text
    )
    assert "within 5 minutes" in recall_text
    assert "marked missed" in no_show_text and "join the queue again" in no_show_text
    assert "front desk" in no_show_text


def test_a_patient_who_has_not_agreed_to_messages_is_not_texted_but_the_ledger_says_so(
    desk: SimpleNamespace,
) -> None:
    """Consent first (Issue 21): the move still happens, the SMS is suppressed and recorded."""
    provider = FakeSmsProvider()
    called = now_sast() - timedelta(hours=1)
    with desk.session() as db:
        ticket = _called_ticket(db, desk.triage.id, called_at=called, consent=False)
        phone = db.get(Patient, ticket.patient_id).phone_e164
        db.commit()
        swept = _sweep(db, called + timedelta(minutes=6), provider)
        statuses = db.scalars(
            select(Notification.status).where(Notification.recipient == phone)
        ).all()
    assert swept.recalled == [ticket.id]
    assert provider.sent == []
    assert statuses == [NotificationStatus.SUPPRESSED.value]


def test_the_timeout_is_the_queues_then_the_clinics_then_the_default(
    desk: SimpleNamespace,
) -> None:
    """A queue's 2 minutes wins over its clinic's 10; a queue with none uses the clinic's."""
    assert timeout_minutes(2, 10, 5) == 2
    assert timeout_minutes(None, 10, 5) == 10
    assert timeout_minutes(None, None, 5) == 5

    provider = FakeSmsProvider()
    called = now_sast() - timedelta(hours=1)
    with desk.session() as db:
        db.get(Site, desk.triage.site_id).recall_timeout_minutes = 10
        db.get(Queue, desk.pharmacy.id).recall_timeout_minutes = 2
        in_pharmacy = _called_ticket(db, desk.pharmacy.id, called_at=called)
        in_triage = _called_ticket(db, desk.triage.id, called_at=called)
        db.commit()
        at_three = _sweep(db, called + timedelta(minutes=3), provider)
        at_eleven = _sweep(db, called + timedelta(minutes=11), provider)
    assert at_three.recalled == [in_pharmacy.id]
    assert at_eleven.recalled == [in_triage.id]


def test_a_clinic_manager_sets_the_clinics_timeout_and_a_queues_is_validated(
    desk: SimpleNamespace,
) -> None:
    """Configurable per site (the manager's, audited) and per queue, within 1–60 minutes."""
    path = f"/api/v1/sites/{desk.triage.site_id}/settings/recall"
    manager = desk.staff("manager.a")

    default = manager.get(path).json()
    refused = desk.staff("desk.a").put(path, json={"recall_timeout_minutes": 8})
    saved = manager.put(path, json={"recall_timeout_minutes": 8})
    too_long = manager.put(path, json={"recall_timeout_minutes": 61})

    assert (default["recall_timeout_minutes"], default["effective_minutes"]) == (
        None,
        5,
    )
    assert refused.status_code == status.HTTP_403_FORBIDDEN
    assert (
        saved.json()["recall_timeout_minutes"] == saved.json()["effective_minutes"] == 8
    )
    assert too_long.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    with desk.session() as db:
        audited = db.scalars(
            select(AuditEvent.context).where(AuditEvent.site_id == desk.triage.site_id)
        ).all()
    assert any(context and "recall timeout set to 8" in context for context in audited)

    queue_path = f"/api/v1/sites/{desk.triage.site_id}/queues/{desk.triage.id}"
    current = manager.get(queue_path).json()
    editable = {
        key: current[key]
        for key in (
            "name",
            "slug",
            "kind",
            "room_label",
            "ticket_prefix",
            "display_order",
            "expected_service_minutes",
            "max_daily_capacity",
            "allows_remote_join",
            "is_active",
        )
    }
    # The factory numbers display_order by creation, which passes the API's 0–999 once enough
    # queues exist in one test process (CI found it): set a valid one so only the timeout is judged.
    editable["display_order"] = 0
    zero = manager.put(queue_path, json={**editable, "recall_timeout_minutes": 0})
    assert zero.status_code == 422
    assert [error["loc"][-1] for error in zero.json()["detail"]] == [
        "recall_timeout_minutes"
    ]
    updated = manager.put(queue_path, json={**editable, "recall_timeout_minutes": 2})
    assert updated.status_code == status.HTTP_200_OK
    assert updated.json()["recall_timeout_minutes"] == 2


def test_staff_can_recall_or_mark_a_no_show_at_once_and_the_timer_follows(
    desk: SimpleNamespace,
) -> None:
    """Criterion 5: the staff member's recall starts the no-show clock; a staff no-show needs no timer."""
    provider = FakeSmsProvider()
    called = now_sast() - timedelta(hours=1)
    front = desk.staff("desk.a")
    with desk.session() as db:
        recall_me = _called_ticket(db, desk.triage.id, called_at=called)
        miss_me = _called_ticket(db, desk.triage.id, called_at=called)
        db.commit()
    base = f"/api/v1/sites/{desk.triage.site_id}/tickets"

    recalled = front.post(f"{base}/{recall_me.id}/transitions", json={"to": "recalled"})
    missed = front.post(f"{base}/{miss_me.id}/transitions", json={"to": "no_show"})

    assert (
        recalled.json()["status"] == "recalled" and missed.json()["status"] == "no_show"
    )
    with desk.session() as db:
        recalled_at = db.get(Ticket, recall_me.id).recalled_at
        assert recalled_at is not None
        # Five minutes after the staff recall, not after the original call.
        later = now_sast() + timedelta(minutes=5, seconds=1)
        swept = _sweep(db, later, provider)
        assert swept.no_shows == [recall_me.id] and swept.recalled == []
        assert _system_rows(db, miss_me.id) == []


def test_the_sweep_is_registered_once_and_replaced_on_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restarted scheduler replaces the recall job rather than adding a second copy."""
    settings = Settings(_env_file=None, scheduler_enabled=True)  # type: ignore[call-arg]
    started = scheduler.start_scheduler(settings)
    try:
        assert started is not None
        jobs = [job for job in started.get_jobs() if job.id == "queue_recall_timers"]
        assert len(jobs) == 1 and jobs[0].func is scheduler.run_recall_timer_sweep
        scheduler.shutdown_scheduler()
        restarted = scheduler.start_scheduler(settings)
        assert restarted is not None
        assert [job.id for job in restarted.get_jobs()].count(
            "queue_recall_timers"
        ) == 1
    finally:
        scheduler.shutdown_scheduler()


# --- restart safety, on PostgreSQL, in separate processes -----------------------------------------


@pytest.fixture
def pg_called(migrated_database: URL) -> Iterator[SimpleNamespace]:
    """A called ticket at an open, listed clinic on PostgreSQL, and a way to run the sweep elsewhere."""
    engine = create_engine(migrated_database)
    apply_postgres_search_path(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    called = now_sast() - timedelta(hours=1)
    with factory() as db:
        site = SiteFactory.create(db, status=SiteStatus.VERIFIED)
        open_all_day(db, site.id)
        queue = QueueFactory.create(db, site_id=site.id)
        ticket = _called_ticket(db, queue.id, called_at=called)
        db.commit()
    url = migrated_database.render_as_string(hide_password=False)
    yield SimpleNamespace(session=factory, ticket=ticket, called=called, url=url)
    engine.dispose()


_SWEEP_IN_A_NEW_PROCESS = (
    "import sys\n"
    "from datetime import datetime\n"
    "from src.core.scheduler import run_recall_timer_sweep\n"
    "print(run_recall_timer_sweep(moment=datetime.fromisoformat(sys.argv[1])))\n"
)


def _process(url: str, at: datetime) -> subprocess.Popen[str]:
    """Start the scheduler's sweep entry point in a brand-new Python process (a restarted worker)."""
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "SCHEDULER_ENABLED": "false",
        "SMS_PROVIDER": "logging",
    }
    return subprocess.Popen(
        [sys.executable, "-c", _SWEEP_IN_A_NEW_PROCESS, at.isoformat()],
        cwd=_REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _moved(*processes: subprocess.Popen[str]) -> list[int]:
    """Wait for the processes; each one's count of tickets moved."""
    counts = []
    for process in processes:
        out, err = process.communicate(timeout=120)
        assert process.returncode == 0, err
        counts.append(int(out.strip().splitlines()[-1]))
    return counts


@pytest.mark.postgres
def test_timers_survive_a_restart_and_never_double_fire(
    pg_called: SimpleNamespace,
) -> None:
    """How to verify, step 3. Every sweep below runs in a process that did not exist a moment before.

    1. A worker sweeps one second before the deadline: nothing. It exits (the restart).
    2. A fresh worker sweeps one second after: the ticket is recalled, once.
    3. Two fresh workers sweep the same instant at the same time: neither recalls it again.
    4. After the second deadline two fresh workers race: exactly one marks the no-show.
    """
    called = pg_called.called
    assert _moved(
        _process(pg_called.url, called + timedelta(minutes=4, seconds=59))
    ) == [0]
    assert _moved(
        _process(pg_called.url, called + timedelta(minutes=5, seconds=1))
    ) == [1]
    racing = [
        _process(pg_called.url, called + timedelta(minutes=5, seconds=2))
        for _ in range(2)
    ]
    assert _moved(*racing) == [0, 0]
    no_show = [
        _process(pg_called.url, called + timedelta(minutes=10, seconds=5))
        for _ in range(2)
    ]
    assert sorted(_moved(*no_show)) == [0, 1]

    with pg_called.session() as db:
        ticket = db.get(Ticket, pg_called.ticket.id)
        rows = _system_rows(db, pg_called.ticket.id)
        messages = db.scalars(
            select(Notification.template_key).where(
                Notification.recipient == db.get(Patient, ticket.patient_id).phone_e164  # type: ignore[union-attr]
            )
        ).all()
    assert ticket is not None and ticket.status == TicketStatus.NO_SHOW.value
    assert [row.diff["status"]["after"] for row in rows] == ["recalled", "no_show"]
    assert sorted(messages) == sorted(
        [
            NotificationTemplate.TICKET_RECALLED.value,
            NotificationTemplate.TICKET_NO_SHOW.value,
        ]
    )
