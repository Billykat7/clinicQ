"""Joining a queue: the one function every channel calls (Issue 40, non-negotiable 1).

A patient reaches a clinic through four doors: the web page, a USSD menu, WhatsApp, and the
reception desk. **All four call :func:`join_queue`, and nothing else issues a ticket.** That is how
"one queue, not two" is kept rather than hoped for: a remote join and a walk-in draw their numbers
from the same database counter (:func:`~src.modules.queue.sequence.allocate_sequence`), in arrival
order, and the source is recorded on the ticket for reporting but never read to order the queue.
The channel adapters (M10) and the walk-in form (Issue 51) are thin callers of this function.

What :func:`join_queue` decides, in this order, and why the order matters:

1. **Is this patient already in the queue today?** Then they get their existing ticket back
   (``created=False``), before anything else is asked. A patient who rejoins after the queue filled
   up, or after the clinic closed its doors at 16:00, still holds the place they already had.
2. **Is the clinic taking patients now?** :func:`~src.modules.sites.availability.join_gate`: opening
   hours, holidays, announced closures, a suspended listing. The same gate for every channel, so a
   closure shuts all four doors at the same instant.
3. **Is this queue taking this patient?** :func:`~src.modules.queues.service.refusal_for`: a
   deactivated queue refuses everyone; a walk-in-only queue refuses a phone.
4. **Abuse guards**, for remote joins only (the kernel limiter, :mod:`src.core.rate_limit`): per
   phone number on every remote channel, per client address on the web, and a per-clinic daily cap
   on remote joins. A walk-in is exempt: reception is authenticated staff, and a cap at the desk
   would turn away someone standing at the counter.
5. **Is there room?** The queue's ``max_daily_capacity``, checked **after** the number is allocated,
   by :func:`src.modules.appointments.capacity.day_is_over`: the one place walk-ins, remote joins and
   booked appointments are counted against the same limit (Issue 80). Allocation locks the queue's
   counter row, and the capacity check then locks the queue's day, so neither another join nor a
   booking can change the count underneath it. A join that finds the queue full rolls back its
   savepoint, which returns the number: the day keeps no gap.

Every refusal is a :class:`JoinRefusedError` carrying a :class:`~src.commons.enums.JoinRefusal`
code and a sentence written for a patient ("Hillbrow Clinic is closed at the moment."), so each
channel can say it without deciding it.

A created ticket is audited (``AuditEntityType.TICKET``, the channel in the context, the reason
redacted), counted in the clinic's view-to-join analytics when it came from a phone (Issue 38), and
written through to the queue snapshot (:func:`~src.modules.queue.snapshot.on_queue_changed`), so the
next discovery read sees it. The caller commits.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.commons.enums import (
    TICKET_ACTIVE_STATUSES,
    AuditAction,
    AuditEntityType,
    DiscoveryChannel,
    JoinRefusal,
    TicketSource,
)
from src.commons.exceptions import ConflictError
from src.commons.time import business_date, now_sast
from src.core.audit import record_audit_event
from src.core.config import Settings, get_settings
from src.core.rate_limit import queue_join_limiter
from src.database.models.appointment_slot import Appointment
from src.database.models.patient import Patient
from src.database.models.queue import Queue
from src.database.models.site import Site
from src.database.models.ticket import Ticket
from src.modules.appointments.call_forward import stated_travel
from src.modules.appointments.capacity import (
    BookingAlreadyConvertedError,
    day_is_over,
    lock_day,
    mark_converted,
)
from src.modules.discovery import analytics
from src.modules.queue.estimate import WaitEstimate
from src.modules.queue.sequence import is_second_active_ticket, issue_ticket
from src.modules.queue.snapshot import on_queue_changed
from src.modules.queue.waits import ticket_wait
from src.modules.queues.service import (
    QUEUE_CLOSED,
    REMOTE_SOURCES,
    WALK_IN_ONLY,
    refusal_for,
)
from src.modules.sites.availability import join_gate
from src.modules.sites.hours import OpeningSchedule

#: What a patient is told when the queue has issued its capacity for the day.
QUEUE_FULL: Final = (
    "This queue is full for today. Please try another queue, or come back tomorrow."
)
#: What a patient is told when the clinic has taken all the remote joins it accepts today.
SITE_DAILY_CAP: Final = "This clinic is not taking more joins by phone today. Please join at the front desk."
#: What a patient is told when one number, or one address, has joined too often.
RATE_LIMITED: Final = (
    "Too many joins in a short time. Please wait a while and try again."
)

#: The analytics channel a remote ticket source is recorded under (Issue 38).
_ANALYTICS_CHANNEL: Final = {
    TicketSource.WEB: DiscoveryChannel.WEB,
    TicketSource.USSD: DiscoveryChannel.USSD,
    TicketSource.WHATSAPP: DiscoveryChannel.WHATSAPP,
}
#: One service day, in seconds: the window of the per-clinic cap, whose key already names the day.
_DAY_SECONDS: Final = 24 * 60 * 60


class JoinRefusedError(ConflictError):
    """A join was refused: HTTP 409, or 429 for :attr:`JoinRefusal.RATE_LIMITED`.

    ``str(error)`` is the sentence for the patient; :attr:`refusal` is the machine-readable reason.
    """

    def __init__(
        self,
        refusal: JoinRefusal,
        message: str,
        *,
        next_open_at: datetime | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message, code=f"queue.join.{refusal.value}")
        self.refusal = refusal
        self.next_open_at = next_open_at
        self.retry_after_seconds = retry_after_seconds


class _QueueFullError(Exception):
    """Raised inside the allocation savepoint so leaving it rolls the number back."""


@dataclass(frozen=True, slots=True)
class JoinResult:
    """A ticket in the queue, whether this call issued it, and where it stands."""

    ticket: Ticket
    created: bool
    #: How many ``waiting`` tickets are ahead of this one in its queue right now.
    waiting_ahead: int
    #: How long the patient should expect to wait: always a range (Issue 42).
    wait: WaitEstimate


def _result(
    db: Session, queue: Queue, ticket: Ticket, *, created: bool, moment: datetime
) -> JoinResult:
    """The answer to a join: the ticket, how many are ahead, and the wait that means."""
    ahead, estimate = ticket_wait(db, ticket, queue, moment=moment)
    return JoinResult(ticket, created=created, waiting_ahead=ahead, wait=estimate)


def describe_ticket(
    db: Session, queue: Queue, ticket: Ticket, *, moment: datetime | None = None
) -> JoinResult:
    """Where a ticket issued earlier stands now, as a join would have answered (``created=False``).

    For the front desk's repeated request (Issue 51): a double-pressed *Issue ticket* is answered with
    the ticket the first press issued, and where it stands now.
    """
    return _result(db, queue, ticket, created=False, moment=moment or now_sast())


def _existing_ticket(
    db: Session, queue: Queue, patient_id: str, service_day: date
) -> Ticket | None:
    """The patient's ticket still in this queue today, if they hold one."""
    return db.execute(
        select(Ticket).where(
            Ticket.queue_id == queue.id,
            Ticket.service_day == service_day,
            Ticket.patient_id == patient_id,
            Ticket.status.in_(
                sorted(status.value for status in TICKET_ACTIVE_STATUSES)
            ),
        )
    ).scalar_one_or_none()


def _guard_abuse(
    *,
    site: Site,
    source: TicketSource,
    patient: Patient,
    proxy: Patient | None,
    client_ip: str | None,
    service_day: date,
    settings: Settings,
) -> str:
    """Apply the per-phone and per-address limits; return the clinic's cap key for recording later.

    The phone and address hits are recorded even when refused, so sustained abuse keeps tripping
    the limit (the kernel limiter's rule). The clinic's cap is only *checked* here and recorded once
    a ticket is really issued, so refused and duplicate joins never spend a clinic's daily budget.

    Raises:
        JoinRefusedError: ``RATE_LIMITED`` or ``SITE_DAILY_CAP``.
    """
    window = settings.queue_join_rate_limit_window_seconds
    # A dependant has no number of their own; the phone doing the joining is the one to limit.
    phone = (proxy or patient).phone_e164 or patient.id
    phone_ok = queue_join_limiter.check_and_record(
        f"phone:{phone}",
        limit=settings.queue_join_rate_limit_per_phone,
        window_seconds=window,
    )
    ip_ok = (
        queue_join_limiter.check_and_record(
            f"ip:{client_ip}",
            limit=settings.queue_join_rate_limit_per_ip,
            window_seconds=window,
        )
        if source is TicketSource.WEB and client_ip
        else True
    )
    if not (phone_ok and ip_ok):
        raise JoinRefusedError(
            JoinRefusal.RATE_LIMITED, RATE_LIMITED, retry_after_seconds=window
        )
    cap_key = f"site:{site.id}:{service_day}"
    if not queue_join_limiter.within_limit(
        cap_key, limit=settings.queue_join_site_daily_cap, window_seconds=_DAY_SECONDS
    ):
        raise JoinRefusedError(JoinRefusal.SITE_DAILY_CAP, SITE_DAILY_CAP)
    return cap_key


def join_queue(
    db: Session,
    *,
    site: Site,
    queue: Queue,
    schedule: OpeningSchedule,
    source: TicketSource,
    patient: Patient | None,
    actor: str,
    actor_id: str | None = None,
    walk_in_name: str | None = None,
    reason_text: str | None = None,
    comment_consent: bool = False,
    travel_minutes: int | None = None,
    proxy: Patient | None = None,
    invited: bool = False,
    appointment: Appointment | None = None,
    client_ip: str | None = None,
    discovery_session: str | None = None,
    settings: Settings | None = None,
    moment: datetime | None = None,
) -> JoinResult:
    """Put a patient in a queue, from any channel, or say why not. The caller commits.

    Args:
        db: The session.
        site: The clinic.
        queue: One of its queues.
        schedule: The clinic's hours, holidays and closures, as the caller's channel reads them
            (:func:`~src.modules.sites.hours.schedule_for` for staff,
            :func:`~src.modules.sites.hours.published_schedules` for a patient).
        source: The door the patient came through. Recorded, never used to order the queue.
        patient: The patient; ``None`` only for a walk-in the desk issues without a number.
        actor: Who is acting, for the audit row: ``patient:<id>``, a staff email, a gateway name.
        actor_id: The staff member's user id, when a staff member is acting.
        walk_in_name: What the desk wrote down to call a walk-in by (walk-ins only).
        reason_text: The optional short reason for the visit.
        comment_consent: Per-visit consent, given now, to show the reason on the board.
        travel_minutes: The trip to the clinic the patient stated, for the virtual waiting room
            (Issue 86); kept only where the clinic runs one, and defaulted there when not stated.
        proxy: The patient taking this place **for** ``patient`` (Issue 84), where one phone acts for
            a household. The ticket stays the dependant's — their number, their name on the board under
            their own consent — and this only records who pressed the button, on the ticket and in the
            audit row. The per-phone abuse guard is keyed on the proxy's number, the one that exists.
        invited: The clinic itself put this patient in this queue's list and asked them to come today
            (Issue 85's repeating collection). The clinic's own invitation passes the walk-in-only rule
            and the abuse guards, exactly as a booking does, because the clinic made it; the queue's
            hours, its daily capacity and every other rule still apply.
        appointment: The booking this join converts (Issue 81). The ticket is issued from the same counter,
            in the same queue, as any other; the booking already passed the abuse guards and the clinic's
            own appointment book, so neither those nor the walk-in-only rule are asked again. The booking
            is marked converted in the same savepoint, and ``ticket.appointment_id`` is unique, so a booking
            becomes one ticket however often this runs.
        client_ip: The caller's address; the per-address guard applies to the web path.
        discovery_session: The browser's discovery session, for the view-to-join analytics.
        settings: Settings; ``None`` reads the application's.
        moment: When the join happens (aware); ``None`` means now in Johannesburg.

    Returns:
        The ticket, whether it was issued now, and how many are waiting ahead of it.

    Raises:
        JoinRefusedError: The clinic or queue is closed, the queue is walk-in only or full, the
            clinic's daily cap is reached, or the caller is rate limited.
        BookingAlreadyConvertedError: ``appointment`` is no longer booked (converted, cancelled or moved
            by another transaction); nothing was issued.
        ValueError: A remote join with no patient, or a queue that belongs to another clinic.
            Both are programming errors in the caller, not a patient's mistake.
    """
    if queue.site_id != site.id:
        raise ValueError("The queue does not belong to this clinic.")
    if patient is None and source in REMOTE_SOURCES:
        raise ValueError(
            f"A {source.value} join always belongs to the patient who joined."
        )
    cfg = settings or get_settings()
    moment = moment or now_sast()
    service_day = business_date(moment)

    if patient is not None:
        existing = _existing_ticket(db, queue, patient.id, service_day)
        if existing is not None:
            return _result(db, queue, existing, created=False, moment=moment)

    gate = join_gate(site, schedule, moment)
    if not gate.allowed:
        raise JoinRefusedError(
            JoinRefusal.CLINIC_CLOSED,
            gate.reason or QUEUE_CLOSED,
            next_open_at=gate.next_open_at,
        )
    refusal = None if appointment is not None or invited else refusal_for(queue, source)
    if refusal is not None:
        code = (
            JoinRefusal.WALK_IN_ONLY
            if refusal == WALK_IN_ONLY
            else JoinRefusal.QUEUE_CLOSED
        )
        raise JoinRefusedError(code, refusal)

    cap_key = None
    if (
        source in REMOTE_SOURCES
        and patient is not None
        and appointment is None
        and not invited
    ):
        cap_key = _guard_abuse(
            site=site,
            source=source,
            patient=patient,
            proxy=proxy,
            client_ip=client_ip,
            service_day=service_day,
            settings=cfg,
        )

    try:
        with db.begin_nested():
            ticket = issue_ticket(
                db,
                queue=queue,
                source=source,
                patient_id=patient.id if patient is not None else None,
                walk_in_name=walk_in_name,
                reason_text=reason_text,
                comment_consent=comment_consent,
                moment=moment,
            )
            if appointment is not None:
                # The booking hands its place to the ticket before the day is counted, so the two are
                # never counted together. Order: counter, day, booking (see appointments.capacity).
                lock_day(db, queue.id, service_day)
                if not mark_converted(db, appointment, ticket, moment=moment):
                    raise BookingAlreadyConvertedError
            if day_is_over(db, queue, service_day):
                raise _QueueFullError
            ticket.proxy_patient_id = proxy.id if proxy is not None else None
            ticket.travel_minutes = stated_travel(
                enabled=site.virtual_waiting_enabled,
                source=source,
                requested=travel_minutes,
            )
    except _QueueFullError:
        raise JoinRefusedError(JoinRefusal.QUEUE_FULL, QUEUE_FULL) from None
    except IntegrityError as exc:
        # Two joins by one patient in the same instant: the other one won the unique index.
        if patient is None or not is_second_active_ticket(exc):
            raise
        winner = _existing_ticket(db, queue, patient.id, service_day)
        if winner is None:
            raise
        return _result(db, queue, winner, created=False, moment=moment)

    if cap_key is not None:
        queue_join_limiter.record(cap_key, window_seconds=_DAY_SECONDS)
    record_audit_event(
        db,
        action=AuditAction.CREATE,
        entity_type=AuditEntityType.TICKET,
        entity_id=ticket.id,
        actor=actor,
        actor_id=actor_id,
        site_id=site.id,
        ip_address=client_ip,
        after={
            "queue_id": ticket.queue_id,
            "number": ticket.number,
            "source": ticket.source,
            "status": ticket.status,
            "reason_text": ticket.reason_text,
            "comment_consent": ticket.comment_consent,
            "travel_minutes": ticket.travel_minutes,
        },
        context=f"joined {queue.name} as {ticket.number} via {source.value}"
        + (" on behalf of the patient" if proxy is not None else ""),
    )
    if source in _ANALYTICS_CHANNEL:
        analytics.record_join_completed(
            db,
            site.id,
            channel=_ANALYTICS_CHANNEL[source],
            session_token=discovery_session,
            settings=cfg,
            moment=moment,
        )
    db.flush()
    on_queue_changed(db, queue, settings=cfg)
    return _result(db, queue, ticket, created=True, moment=moment)
