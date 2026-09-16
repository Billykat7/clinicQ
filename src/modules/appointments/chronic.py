"""Chronic medication: the collection that comes round again, and the one tap that joins the queue (Issue 85).

A patient on chronic treatment comes back every 28 days, forever. Missing one collection is how people
fall off treatment, and the nudge that prevents it is the cheapest thing this product does.

**The sweep** (:func:`run_due`, ``run_chronic_collections``, advisory lock 883, every quarter of an hour)
does two things and nothing else:

* **the reminder**, once per cycle, on the first day from the due date that the clinic actually opens and
  not before :data:`EARLIEST_HOUR` in the morning. ``reminded_for`` holds the due date it was sent for, so
  however many sweeps run, one message goes;
* **the follow-up**, **exactly once**, for a cycle whose grace period has passed with no collection. Then
  the schedule rolls forward to the next cycle, so a patient who misses one month is reminded again next
  month and never nagged in between. ``followed_up_for`` is what makes "exactly once" a fact rather than
  an intention.

**One tap, one interaction.** The reminder carries ``/api/v1/collections/joins/{token}``: the push
notification's own button posts it, and an SMS reply of ``COLLECT`` reaches :func:`join_by_phone`. Either
way the patient is put in the collection queue through **Issue 40's** ``join_queue`` — the same counter,
the same sequence, the same board — and is told their number in the answer.

**Collecting rolls it forward**, from the day it happened rather than the day it was due (:func:`collected`,
called by the lifecycle's ``done`` hook), because that is how a month's supply actually works.

**What the sweep never does**: send outside the clinic's opening days, send to somebody who has opted out
or is inside their quiet hours (the notification service holds both), or send twice. A patient who replies
``STOP`` stops every message on every channel, which is what "stop reminders from any channel" means here;
the clinic's own list still shows the collection as due, because the pharmacy still has to plan for it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.commons.enums import (
    TICKET_ACTIVE_STATUSES,
    AuditAction,
    AuditEntityType,
    PatientEvent,
    TicketSource,
    TicketStatus,
)
from src.commons.exceptions import ConflictError, NotFoundError
from src.commons.phone import InvalidPhoneNumberError, normalize_phone
from src.commons.time import business_date, now_sast, stored_sast
from src.core.audit import record_audit_event
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models import ChronicSchedule, Patient, Queue, Site, Ticket
from src.database.models.chronic_schedule import (
    DEFAULT_GRACE_DAYS,
    DEFAULT_INTERVAL_DAYS,
    MAX_GRACE_DAYS,
    MAX_INTERVAL_DAYS,
    MIN_INTERVAL_DAYS,
)
from src.modules.notifications import service as notifications
from src.modules.queue.service import join_queue
from src.modules.sites.hours import OpeningSchedule, published_schedules

#: Nothing is sent before this hour: a collection reminder at 04:00 helps nobody, and quiet hours are a
#: patient's own setting rather than a rule about what is decent.
EARLIEST_HOUR: Final = 8
#: How far past the due date the sweep looks for an open day. Beyond this the clinic is not opening and
#: the follow-up is the right message, not another reminder.
OPEN_DAY_HORIZON: Final = 14
#: The wire codes for the refusals this module owns.
NOT_FOUND_CODE: Final = "collections.not_found"
STOPPED_CODE: Final = "collections.stopped"
#: Who the audit trail says acted when the sweep did.
SWEEP_ACTOR: Final = "system:chronic-collections"
#: What the patient's own stop is recorded as.
PATIENT_ACTOR: Final = "patient"
#: Where a reminder's join button posts.
JOIN_PATH_PREFIX: Final = "/api/v1/collections/joins/"


class ScheduleNotFoundError(NotFoundError):
    """No such schedule at this clinic, or none for this token: HTTP 404."""

    def __init__(self) -> None:
        super().__init__("No such collection schedule.", code=NOT_FOUND_CODE)


class ScheduleStoppedError(ConflictError):
    """The schedule has been stopped, so its join link no longer does anything: HTTP 409."""

    def __init__(self) -> None:
        super().__init__(
            "These reminders have been stopped. Please see reception.",
            code=STOPPED_CODE,
        )


@dataclass(slots=True)
class SweepResult:
    """What one run did: reminders sent, follow-ups sent, and cycles rolled past."""

    reminded: list[str] = field(default_factory=list)
    followed_up: list[str] = field(default_factory=list)
    rolled: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Adherence:
    """How one clinic's collections are going, for the M12 dashboard (Issue 90)."""

    queue_id: str
    queue_name: str
    schedules: int
    due: int
    collected_on_time: int
    collected_late: int
    missed: int


def join_path(token: str) -> str:
    """Where the reminder's join button posts; the token is the whole of the credential."""
    return f"{JOIN_PATH_PREFIX}{token}"


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _open_day(schedule: OpeningSchedule | None, due: date) -> date | None:
    """The first day from ``due`` the clinic opens, or ``None`` within the horizon.

    A collection is a visit: asking somebody to come on a day the doors are shut wastes their taxi fare.
    """
    if schedule is None:
        return due
    for offset in range(OPEN_DAY_HORIZON + 1):
        day = due + timedelta(days=offset)
        if schedule.spans_on(day):
            return day
    return None


def _collected_since(
    db: Session, schedule: ChronicSchedule, since: date
) -> date | None:
    """The day this patient's collection ticket in that queue was finished, on or after ``since``."""
    return db.execute(
        select(func.max(Ticket.service_day)).where(
            Ticket.queue_id == schedule.queue_id,
            Ticket.patient_id == schedule.patient_id,
            Ticket.service_day >= since,
            Ticket.status == TicketStatus.DONE.value,
        )
    ).scalar_one_or_none()


def _tell(
    db: Session,
    schedule: ChronicSchedule,
    event: PatientEvent,
    *,
    due: date,
    moment: datetime,
) -> None:
    """Send one message about this schedule, through the notification service."""
    site = db.get(Site, schedule.site_id)
    queue = db.get(Queue, schedule.queue_id)
    notifications.notify(
        db,
        patient_id=schedule.patient_id,
        event=event,
        context={
            "clinic": site.name if site else "",
            "queue": queue.name if queue else "",
            "room": queue.room_label if queue else None,
            "when": due.strftime("%a %-d %b"),
            "reply_url": join_path(schedule.join_token)
            if schedule.join_token
            else None,
        },
        site_id=schedule.site_id,
        dedupe_key=f"{schedule.id}:{event.value}:{due.isoformat()}",
        now=moment,
    )


def run_due(db: Session, *, moment: datetime | None = None) -> SweepResult:
    """Send the reminders and the one follow-up that are due now. The caller commits.

    Ordered so that a cycle is reminded before it can be followed up, and rolled forward only once the
    follow-up has gone: a patient never sees a follow-up for a cycle they were never reminded about.
    """
    moment = moment or now_sast()
    today = business_date(moment)
    done = SweepResult()
    rows = list(
        db.execute(
            select(ChronicSchedule)
            .where(
                ChronicSchedule.stopped_at.is_(None),
                ChronicSchedule.next_due_on <= today + timedelta(days=OPEN_DAY_HORIZON),
            )
            .order_by(ChronicSchedule.next_due_on)
        ).scalars()
    )
    if not rows:
        return done
    schedules = published_schedules(
        db,
        sorted({row.site_id for row in rows}),
        from_day=today,
        horizon_days=OPEN_DAY_HORIZON + 1,
    )
    for schedule in rows:
        due = schedule.next_due_on
        opening = schedules.get(schedule.site_id)
        send_on = _open_day(opening, due)
        if (
            schedule.reminded_for != due
            and send_on is not None
            and send_on == today
            and moment.hour >= EARLIEST_HOUR
        ):
            schedule.reminded_for = due
            schedule.join_token = schedule.join_token or _new_token()
            db.flush()
            _tell(db, schedule, PatientEvent.COLLECTION_DUE, due=due, moment=moment)
            done.reminded.append(schedule.id)
            continue
        if today <= due + timedelta(days=schedule.grace_days):
            continue
        if _collected_since(db, schedule, due) is not None:
            _roll(db, schedule, collected_on=due, moment=moment)
            done.rolled.append(schedule.id)
            continue
        if schedule.followed_up_for != due:
            schedule.followed_up_for = due
            db.flush()
            _tell(db, schedule, PatientEvent.COLLECTION_MISSED, due=due, moment=moment)
            done.followed_up.append(schedule.id)
            record_audit_event(
                db,
                action=AuditAction.UPDATE,
                entity_type=AuditEntityType.PATIENT,
                entity_id=schedule.patient_id,
                actor=SWEEP_ACTOR,
                site_id=schedule.site_id,
                context=f"collection due {due.isoformat()} was missed: one follow-up sent",
            )
        # The cycle is over either way: roll it forward, so the next reminder is the next month's.
        schedule.next_due_on = due + timedelta(days=schedule.interval_days)
        db.flush()
        done.rolled.append(schedule.id)
    return done


def _roll(
    db: Session, schedule: ChronicSchedule, *, collected_on: date, moment: datetime
) -> None:
    """Move the schedule on from a collection: the next one is an interval from that day."""
    schedule.last_collected_on = collected_on
    schedule.next_due_on = collected_on + timedelta(days=schedule.interval_days)
    db.flush()


def collected(db: Session, ticket: Ticket, *, moment: datetime | None = None) -> bool:
    """A collection has happened: roll that patient's schedule forward. The caller commits.

    Called by the lifecycle's ``done`` hook for every finished visit; it does nothing unless the ticket
    is in a queue somebody has a live schedule in. Rolling from the day it happened, not the day it was
    due, is how a month's supply is counted.
    """
    moment = moment or now_sast()
    if ticket.patient_id is None:
        return False
    schedule = db.execute(
        select(ChronicSchedule).where(
            ChronicSchedule.queue_id == ticket.queue_id,
            ChronicSchedule.patient_id == ticket.patient_id,
            ChronicSchedule.stopped_at.is_(None),
        )
    ).scalar_one_or_none()
    if schedule is None:
        return False
    _roll(db, schedule, collected_on=ticket.service_day, moment=moment)
    return True


# --------------------------------------------------------------------------------------
# The one tap
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Joined:
    """What the patient is told back: their number, where to go, and how many are ahead."""

    ticket_id: str
    number: str
    queue_name: str
    room_label: str | None
    waiting_ahead: int
    already: bool


def by_token(db: Session, token: str) -> ChronicSchedule:
    """The schedule a reminder's join button names; anything else is the same "not found".

    Raises:
        ScheduleNotFoundError: No such token.
    """
    schedule = (
        db.execute(
            select(ChronicSchedule).where(ChronicSchedule.join_token == token)
        ).scalar_one_or_none()
        if token and len(token) <= 64
        else None
    )
    if schedule is None:
        raise ScheduleNotFoundError()
    return schedule


def join_now(
    db: Session, schedule: ChronicSchedule, *, moment: datetime | None = None
) -> Joined:
    """Take this patient's place in the collection queue, in one interaction. The caller commits.

    Raises:
        ScheduleStoppedError: The schedule was stopped.
        JoinRefusedError: The clinic is closed or the queue is full; the patient is told which.
    """
    moment = moment or now_sast()
    if not schedule.is_active:
        raise ScheduleStoppedError()
    site = db.get(Site, schedule.site_id)
    queue = db.get(Queue, schedule.queue_id)
    patient = db.get(Patient, schedule.patient_id)
    if site is None or queue is None or patient is None:
        raise ScheduleNotFoundError()
    opening = published_schedules(db, [site.id], from_day=business_date(moment)).get(
        site.id
    )
    if opening is None:
        raise ScheduleNotFoundError()
    result = join_queue(
        db,
        site=site,
        queue=queue,
        schedule=opening,
        source=TicketSource.WEB,
        patient=patient,
        actor=f"patient:{patient.id}",
        # The clinic put this patient in this queue's repeat list and asked them to come: a collection
        # queue that takes walk-ins only still takes the patient it invited (the hours and the daily
        # capacity are unchanged).
        invited=True,
        moment=moment,
    )
    record_audit_event(
        db,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.PATIENT,
        entity_id=patient.id,
        actor=f"patient:{patient.id}",
        site_id=site.id,
        context=f"joined {queue.name} as {result.ticket.number} from a collection reminder",
    )
    return Joined(
        ticket_id=result.ticket.id,
        number=result.ticket.number,
        queue_name=queue.name,
        room_label=queue.room_label,
        waiting_ahead=result.waiting_ahead,
        already=not result.created,
    )


def join_by_phone(
    db: Session, raw_phone: str, *, moment: datetime | None = None
) -> Joined | None:
    """A patient replying ``COLLECT`` to a reminder: their soonest reminded collection, or ``None``.

    The SMS gateway calls this before anything else it does with a keyword, and falls through when the
    number has no collection waiting, so ``COLLECT`` from anyone else still means nothing.
    """
    moment = moment or now_sast()
    try:
        phone = normalize_phone(raw_phone)
    except InvalidPhoneNumberError:
        return None
    patient = db.execute(
        select(Patient).where(
            Patient.phone_e164 == phone, Patient.is_deleted.is_(False)
        )
    ).scalar_one_or_none()
    if patient is None:
        return None
    schedule = (
        db.execute(
            select(ChronicSchedule)
            .where(
                ChronicSchedule.patient_id == patient.id,
                ChronicSchedule.stopped_at.is_(None),
                ChronicSchedule.reminded_for.is_not(None),
            )
            .order_by(ChronicSchedule.reminded_for.desc())
        )
        .scalars()
        .first()
    )
    if schedule is None:
        return None
    return join_now(db, schedule, moment=moment)


# --------------------------------------------------------------------------------------
# The clinic's own list
# --------------------------------------------------------------------------------------


def schedules_at(
    db: Session, access: SiteAccess, *, include_stopped: bool = False
) -> list[ChronicSchedule]:
    """This clinic's collection schedules, the soonest due first."""
    query = scoped_select(ChronicSchedule, access)
    if not include_stopped:
        query = query.where(ChronicSchedule.stopped_at.is_(None))
    return list(db.execute(query.order_by(ChronicSchedule.next_due_on)).scalars())


def schedule_at(db: Session, access: SiteAccess, schedule_id: str) -> ChronicSchedule:
    """One of this clinic's schedules; another clinic's is the same "not found"."""
    schedule = db.execute(
        scoped_select(ChronicSchedule, access).where(ChronicSchedule.id == schedule_id)
    ).scalar_one_or_none()
    if schedule is None:
        raise ScheduleNotFoundError()
    return schedule


def create(
    db: Session,
    access: SiteAccess,
    *,
    patient: Patient,
    queue: Queue,
    next_due_on: date,
    interval_days: int = DEFAULT_INTERVAL_DAYS,
    grace_days: int = DEFAULT_GRACE_DAYS,
    service: str | None = None,
    actor: str,
) -> ChronicSchedule:
    """Set up a repeating collection for one patient. The caller audits and commits.

    A schedule that already exists for that patient and queue is **updated**, not duplicated: the desk
    setting one up twice is a correction, not a second course of treatment.
    """
    if not MIN_INTERVAL_DAYS <= interval_days <= MAX_INTERVAL_DAYS:
        raise ConflictError(
            f"A repeat is between {MIN_INTERVAL_DAYS} and {MAX_INTERVAL_DAYS} days.",
            code="collections.interval",
        )
    if not 0 <= grace_days <= MAX_GRACE_DAYS:
        raise ConflictError(
            f"A grace period is at most {MAX_GRACE_DAYS} days.",
            code="collections.grace",
        )
    existing = db.execute(
        select(ChronicSchedule).where(
            ChronicSchedule.patient_id == patient.id,
            ChronicSchedule.queue_id == queue.id,
        )
    ).scalar_one_or_none()
    schedule = existing or ChronicSchedule(
        site_id=queue.site_id,
        queue_id=queue.id,
        patient_id=patient.id,
        created_by=actor,
    )
    schedule.interval_days = interval_days
    schedule.grace_days = grace_days
    schedule.next_due_on = next_due_on
    schedule.service = (service or "").strip()[:60] or None
    schedule.join_token = schedule.join_token or _new_token()
    schedule.stopped_at = None
    schedule.stopped_by = None
    schedule.reminded_for = None
    schedule.followed_up_for = None
    if existing is None:
        db.add(schedule)
    db.flush()
    return schedule


def stop(
    db: Session,
    schedule: ChronicSchedule,
    *,
    by: str,
    moment: datetime | None = None,
) -> ChronicSchedule:
    """Stop a schedule, from the clinic or at the patient's own asking. The caller commits.

    Nothing is sent from this moment. The row stays: the collections it explains stay too.
    """
    moment = moment or now_sast()
    if schedule.stopped_at is None:
        schedule.stopped_at = moment
        schedule.stopped_by = by[:64]
        db.flush()
        record_audit_event(
            db,
            action=AuditAction.UPDATE,
            entity_type=AuditEntityType.PATIENT,
            entity_id=schedule.patient_id,
            actor=by,
            site_id=schedule.site_id,
            context="collection reminders stopped",
        )
    return schedule


def adherence(
    db: Session, access: SiteAccess, *, start: date, end: date
) -> list[Adherence]:
    """How collections are going per queue at this clinic, for Issue 90's dashboard.

    A cycle counts as **on time** when the collection happened by the due day plus the grace period,
    **late** when it happened after that, and **missed** when it has not happened and the grace period
    has passed.
    """
    rows = list(
        db.execute(
            scoped_select(ChronicSchedule, access).where(
                ChronicSchedule.next_due_on
                >= start - timedelta(days=MAX_INTERVAL_DAYS),
            )
        ).scalars()
    )
    names = {
        queue.id: queue.name
        for queue in db.execute(scoped_select(Queue, access)).scalars()
    }
    by_queue: dict[str, list[ChronicSchedule]] = {}
    for schedule in rows:
        by_queue.setdefault(schedule.queue_id, []).append(schedule)
    report: list[Adherence] = []
    for queue_id, schedules in sorted(
        by_queue.items(), key=lambda pair: names.get(pair[0], "")
    ):
        due = [s for s in schedules if start <= _last_due(s) <= end]
        on_time = late = missed = 0
        for schedule in due:
            last_due = _last_due(schedule)
            collected_on = schedule.last_collected_on
            if collected_on is None or collected_on < last_due:
                missed += 1
            elif collected_on <= last_due + timedelta(days=schedule.grace_days):
                on_time += 1
            else:
                late += 1
        report.append(
            Adherence(
                queue_id=queue_id,
                queue_name=names.get(queue_id, ""),
                schedules=len(schedules),
                due=len(due),
                collected_on_time=on_time,
                collected_late=late,
                missed=missed,
            )
        )
    return report


def _last_due(schedule: ChronicSchedule) -> date:
    """The due date of the cycle that has just been through: what the report counts."""
    if schedule.last_collected_on is not None:
        return schedule.next_due_on - timedelta(days=schedule.interval_days)
    return schedule.reminded_for or schedule.next_due_on


def holds_ticket_today(db: Session, schedule: ChronicSchedule, day: date) -> bool:
    """Whether this patient already holds a place in the collection queue that day."""
    return (
        db.execute(
            select(Ticket.id).where(
                Ticket.queue_id == schedule.queue_id,
                Ticket.patient_id == schedule.patient_id,
                Ticket.service_day == day,
                Ticket.status.in_(sorted(s.value for s in TICKET_ACTIVE_STATUSES)),
            )
        ).first()
        is not None
    )


def stored_day(value: datetime) -> date:
    """The service day of an aware datetime, in the clinic's own time."""
    return business_date(stored_sast(value))
