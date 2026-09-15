"""A clinic's appointment book: its policy, windows, overrides, generated slots and blocks (Issue 80).

Everything that touches the database for this module except the capacity guard
(:mod:`src.modules.appointments.capacity`). Every read of a site-scoped row is built by the site guard:
a staff request passes its :class:`~src.core.site_scope.SiteAccess` (:func:`staff_rows`), and the
nightly generation, which acts for no one, reads only clinics a patient may be shown
(:func:`published_rows`). The functions below take either as a :data:`RowSource`, so the rules are
written once and each caller decides only whose rows it may see.

**Generation is a reconciliation, and running it twice changes nothing.** For each service day in the
range it compares the stored slots with what the plan produces now
(:func:`~src.modules.appointments.schedule.plan_slots`):

* a produced time with no slot is created; one with a slot is kept, its length, capacity and service
  brought up to date (capacity never below the places already booked, which the check constraint
  would refuse anyway);
* a stored future slot no longer produced is **deleted** when nobody ever booked it, and **withdrawn**
  when somebody did: it stops being offered, and the bookings stand for Issue 81 to reach;
* a withdrawn slot produced again is offered again.

Slots that have already started are history and are never touched.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from src.commons.enums import SlotRefusal
from src.commons.time import business_date, business_day_bounds, now_sast, stored_sast
from src.core.site_scope import (
    SiteAccess,
    publicly_visible_site_clauses,
    published_select,
    scoped_select,
)
from src.database.models import (
    Appointment,
    AppointmentBlock,
    AppointmentDayOverride,
    AppointmentPolicy,
    AppointmentSlot,
    AppointmentTemplateWindow,
    ClinicService,
    Queue,
    Site,
)
from src.database.models.appointment_slot import (
    DEFAULT_CONVERT_LEAD_MINUTES,
    DEFAULT_HORIZON_DAYS,
    DEFAULT_MIN_LEAD_MINUTES,
)
from src.modules.appointments import capacity
from src.modules.appointments.availability import (
    BlockedRange,
    BookingPolicy,
    OfferContext,
    refusal_for,
)
from src.modules.appointments.schedule import (
    PlannedSlot,
    SlotPlan,
    SlotWindow,
    open_stretches,
    plan_slots,
)
from src.modules.appointments.schemas import (
    BlockIn,
    DayAvailabilityOut,
    DayOverrideIn,
    GenerateOut,
    PolicyIn,
    QueueDayOut,
    SlotOut,
    WeeklyTemplateIn,
    WindowFields,
)
from src.modules.sites.hours import (
    OpeningSchedule,
    TimeSpan,
    published_schedules,
    schedule_for,
)

#: A function building ``SELECT <model> …`` already narrowed to the rows its caller may see.
type RowSource = Callable[[type[Any]], Select[Any]]

#: How far either side of a range the opening schedule is loaded: a window starting the day before
#: may cross midnight into the range, and a slot at the end may run past midnight out of it.
PLANNING_MARGIN = timedelta(days=1)


class UnknownServiceError(ValueError):
    """A window names a service that is not on this clinic's catalogue."""


class BlockNotFoundError(LookupError):
    """No such block at this clinic, or it has already been lifted."""


def staff_rows(access: SiteAccess) -> RowSource:
    """The rows a staff request may read: its clinic's, through the site guard."""
    return partial(_scoped, access=access)


def published_rows(site_ids: Sequence[str]) -> RowSource:
    """The rows a system job may read for publicly visible clinics among ``site_ids``."""
    return partial(_published, site_ids=tuple(site_ids))


def _scoped(model: type[Any], *, access: SiteAccess) -> Select[Any]:
    """:func:`~src.core.site_scope.scoped_select`, with the model first so it partials."""
    return scoped_select(model, access)


def _published(model: type[Any], *, site_ids: tuple[str, ...]) -> Select[Any]:
    """:func:`~src.core.site_scope.published_select`, with the model first so it partials."""
    return published_select(model, site_ids)


def staff_opening(
    db: Session, access: SiteAccess, first_day: date, last_day: date
) -> OpeningSchedule:
    """The clinic's opening schedule covering ``first_day`` to ``last_day`` with the planning margin."""
    return schedule_for(
        db,
        access,
        from_day=first_day - PLANNING_MARGIN,
        horizon_days=(last_day - first_day).days + 3,
    )


def bookable_queues(db: Session, rows: RowSource) -> list[Queue]:
    """The clinic's live queues, in the clinic's order: the ones a day view lists."""
    return list(
        db.execute(
            rows(Queue)
            .where(Queue.is_deleted.is_(False), Queue.is_active.is_(True))
            .order_by(Queue.display_order, Queue.name)
        )
        .scalars()
        .all()
    )


# --------------------------------------------------------------------------------------
# The policy
# --------------------------------------------------------------------------------------


def policy_for(db: Session, rows: RowSource) -> tuple[BookingPolicy, bool]:
    """The clinic's booking policy, and whether it is the default (no row written yet)."""
    row = db.execute(rows(AppointmentPolicy)).scalar_one_or_none()
    if row is None:
        return (
            BookingPolicy(
                DEFAULT_HORIZON_DAYS,
                DEFAULT_MIN_LEAD_MINUTES,
                DEFAULT_CONVERT_LEAD_MINUTES,
            ),
            True,
        )
    return (
        BookingPolicy(row.horizon_days, row.min_lead_minutes, row.convert_lead_minutes),
        False,
    )


def set_policy(db: Session, access: SiteAccess, payload: PolicyIn) -> BookingPolicy:
    """Write the clinic's booking policy. The caller audits and commits."""
    row = db.execute(scoped_select(AppointmentPolicy, access)).scalar_one_or_none()
    if row is None:
        row = AppointmentPolicy(site_id=access.site_id)
        db.add(row)
    row.horizon_days = payload.horizon_days
    row.min_lead_minutes = payload.min_lead_minutes
    row.convert_lead_minutes = payload.convert_lead_minutes
    db.flush()
    return BookingPolicy(
        row.horizon_days, row.min_lead_minutes, row.convert_lead_minutes
    )


# --------------------------------------------------------------------------------------
# Windows and overrides
# --------------------------------------------------------------------------------------


def _check_services(
    db: Session, access: SiteAccess, windows: Sequence[WindowFields]
) -> None:
    """Refuse a window naming a service that is not on this clinic's catalogue."""
    named = {window.service_id for window in windows if window.service_id is not None}
    if not named:
        return
    found = set(
        db.execute(
            scoped_select(ClinicService, access).where(
                ClinicService.id.in_(sorted(named))
            )
        )
        .scalars()
        .all()
    )
    missing = named - {service.id for service in found}
    if missing:
        raise UnknownServiceError(
            f"Not a service at this clinic: {', '.join(sorted(missing))}."
        )


def weekly_windows(
    db: Session, rows: RowSource, queue_id: str
) -> list[AppointmentTemplateWindow]:
    """A queue's weekly windows, ordered by weekday and start."""
    return list(
        db.execute(
            rows(AppointmentTemplateWindow)
            .where(AppointmentTemplateWindow.queue_id == queue_id)
            .order_by(
                AppointmentTemplateWindow.weekday, AppointmentTemplateWindow.starts_at
            )
        )
        .scalars()
        .all()
    )


def replace_weekly_windows(
    db: Session, access: SiteAccess, queue: Queue, payload: WeeklyTemplateIn
) -> list[AppointmentTemplateWindow]:
    """Replace a queue's whole week. Existing slots change only when generation next runs."""
    _check_services(db, access, payload.windows)
    for row in weekly_windows(db, staff_rows(access), queue.id):
        db.delete(row)
    db.flush()
    for window in payload.windows:
        db.add(
            AppointmentTemplateWindow(
                site_id=access.site_id,
                queue_id=queue.id,
                service_id=window.service_id,
                weekday=window.weekday,
                starts_at=window.starts_at,
                ends_at=window.ends_at,
                slot_minutes=window.slot_minutes,
                capacity=window.capacity,
            )
        )
    db.flush()
    return weekly_windows(db, staff_rows(access), queue.id)


def day_override_rows(
    db: Session, rows: RowSource, queue_id: str, first_day: date, last_day: date
) -> list[AppointmentDayOverride]:
    """A queue's override rows for the dates ``first_day`` to ``last_day``."""
    return list(
        db.execute(
            rows(AppointmentDayOverride)
            .where(
                AppointmentDayOverride.queue_id == queue_id,
                AppointmentDayOverride.day >= first_day,
                AppointmentDayOverride.day <= last_day,
            )
            .order_by(AppointmentDayOverride.day, AppointmentDayOverride.starts_at)
        )
        .scalars()
        .all()
    )


def set_day_override(
    db: Session, access: SiteAccess, queue: Queue, day: date, payload: DayOverrideIn
) -> list[AppointmentDayOverride]:
    """Replace one date's windows. No windows writes the "no appointments that day" row."""
    _check_services(db, access, payload.windows)
    clear_day_override(db, access, queue, day)
    if not payload.windows:
        db.add(
            AppointmentDayOverride(site_id=access.site_id, queue_id=queue.id, day=day)
        )
    for window in payload.windows:
        db.add(
            AppointmentDayOverride(
                site_id=access.site_id,
                queue_id=queue.id,
                day=day,
                service_id=window.service_id,
                starts_at=window.starts_at,
                ends_at=window.ends_at,
                slot_minutes=window.slot_minutes,
                capacity=window.capacity,
            )
        )
    db.flush()
    return day_override_rows(db, staff_rows(access), queue.id, day, day)


def clear_day_override(db: Session, access: SiteAccess, queue: Queue, day: date) -> int:
    """Remove one date's override, so the week applies again; return how many rows went."""
    removed = day_override_rows(db, staff_rows(access), queue.id, day, day)
    for row in removed:
        db.delete(row)
    db.flush()
    return len(removed)


def window_fields(
    row: AppointmentTemplateWindow | AppointmentDayOverride,
) -> WindowFields:
    """A stored window as the API writes it."""
    assert row.starts_at is not None and row.ends_at is not None
    assert row.slot_minutes is not None and row.capacity is not None
    return WindowFields(
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        slot_minutes=row.slot_minutes,
        capacity=row.capacity,
        service_id=row.service_id,
    )


def _slot_window(row: AppointmentTemplateWindow | AppointmentDayOverride) -> SlotWindow:
    """A stored window as the planner reads it."""
    fields = window_fields(row)
    return SlotWindow(
        span=TimeSpan(fields.starts_at, fields.ends_at),
        slot_minutes=fields.slot_minutes,
        capacity=fields.capacity,
        service_id=fields.service_id,
    )


def load_plan(
    db: Session, rows: RowSource, queue_id: str, first_day: date, last_day: date
) -> SlotPlan:
    """A queue's plan for ``first_day`` to ``last_day``: its week, and the overrides in the range.

    Overrides are loaded from the day before, because a window starting then may cross midnight.
    """
    weekly: dict[int, list[SlotWindow]] = {}
    for window in weekly_windows(db, rows, queue_id):
        weekly.setdefault(window.weekday, []).append(_slot_window(window))
    overrides: dict[date, list[SlotWindow]] = {}
    for override in day_override_rows(
        db, rows, queue_id, first_day - timedelta(days=1), last_day
    ):
        windows = overrides.setdefault(override.day, [])
        if override.starts_at is not None:
            windows.append(_slot_window(override))
    return SlotPlan(
        weekly={weekday: tuple(windows) for weekday, windows in weekly.items()},
        overrides={day: tuple(windows) for day, windows in overrides.items()},
    )


# --------------------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Generated:
    """What one generation run changed for one queue."""

    first_day: date
    last_day: date
    created: int = 0
    kept: int = 0
    removed: int = 0
    withdrawn: int = 0
    restored: int = 0

    def as_out(self, site_id: str, queue_id: str) -> GenerateOut:
        """The API's view of the run."""
        return GenerateOut(
            site_id=site_id,
            queue_id=queue_id,
            first_day=self.first_day,
            last_day=self.last_day,
            created=self.created,
            kept=self.kept,
            removed=self.removed,
            withdrawn=self.withdrawn,
            restored=self.restored,
        )


def generation_range(
    policy: BookingPolicy,
    moment: datetime,
    from_day: date | None = None,
    days: int | None = None,
) -> tuple[date, date]:
    """The service days a run covers: never before today, and by default through the horizon."""
    today = business_date(moment)
    first = max(from_day or today, today)
    last = (
        first + timedelta(days=days - 1)
        if days is not None
        else policy.last_bookable_day(moment)
    )
    return first, last


def generate_slots(
    db: Session,
    rows: RowSource,
    queue: Queue,
    opening: OpeningSchedule,
    first_day: date,
    last_day: date,
    *,
    moment: datetime | None = None,
) -> Generated:
    """Bring a queue's stored slots for ``first_day`` to ``last_day`` into line with its plan.

    See the module docstring for the rules. Idempotent. The caller commits.

    Args:
        db: The session.
        rows: Whose rows may be read (:func:`staff_rows` or :func:`published_rows`).
        queue: The queue, already resolved through the same guard.
        opening: The clinic's opening schedule, loaded from the day before ``first_day`` through the
            day after ``last_day``.
        first_day: The first service day, inclusive.
        last_day: The last service day, inclusive.
        moment: Now (aware); slots that have started by then are left alone.
    """
    moment = moment or now_sast()
    planned: dict[datetime, PlannedSlot] = {
        slot.starts_at: slot
        for slot in plan_slots(
            load_plan(db, rows, queue.id, first_day, last_day),
            opening,
            first_day,
            last_day,
        )
    }
    stored = {
        stored_sast(slot.starts_at): slot
        for slot in db.execute(
            rows(AppointmentSlot).where(
                AppointmentSlot.queue_id == queue.id,
                AppointmentSlot.service_day >= first_day,
                AppointmentSlot.service_day <= last_day,
            )
        )
        .scalars()
        .all()
    }
    ever_booked = _slots_with_bookings(db, rows, [slot.id for slot in stored.values()])
    created = kept = removed = withdrawn = restored = 0
    for starts_at, plan in planned.items():
        if starts_at < moment:
            continue
        slot = stored.get(starts_at)
        if slot is None:
            db.add(
                AppointmentSlot(
                    site_id=queue.site_id,
                    queue_id=queue.id,
                    service_id=plan.service_id,
                    starts_at=plan.starts_at,
                    ends_at=plan.ends_at,
                    service_day=plan.service_day,
                    capacity=plan.capacity,
                )
            )
            created += 1
            continue
        slot.ends_at = plan.ends_at
        slot.capacity = max(plan.capacity, slot.booked_count)
        slot.service_id = plan.service_id
        if slot.withdrawn_at is not None:
            slot.withdrawn_at = None
            restored += 1
        else:
            kept += 1
    for starts_at, slot in stored.items():
        if starts_at in planned or starts_at < moment:
            continue
        if slot.id in ever_booked:
            if slot.withdrawn_at is None:
                slot.withdrawn_at = moment
                withdrawn += 1
            continue
        db.delete(slot)
        removed += 1
    db.flush()
    return Generated(first_day, last_day, created, kept, removed, withdrawn, restored)


def _slots_with_bookings(db: Session, rows: RowSource, slot_ids: list[str]) -> set[str]:
    """The slots among ``slot_ids`` any booking (held or cancelled) points at."""
    if not slot_ids:
        return set()
    return set(
        db.execute(
            rows(Appointment)
            .with_only_columns(Appointment.slot_id)
            .where(Appointment.slot_id.in_(slot_ids))
            .distinct()
        ).scalars()
    )


def generate_published(db: Session, *, moment: datetime | None = None) -> int:
    """Roll every publicly visible clinic's slots forward to its horizon; return how many were created.

    The nightly sweep's body (:func:`src.core.scheduler.run_appointment_slot_generation`). It acts for
    no one, so it reads through :func:`published_rows`: only clinics a patient may be shown, which are
    the only ones a patient can book. A clinic still being checked generates its slots by hand from the
    manager's screen. Idempotent, like :func:`generate_slots`. The caller commits.
    """
    moment = moment or now_sast()
    visible = list(
        db.execute(select(Site.id).where(*publicly_visible_site_clauses())).scalars()
    )
    created = 0
    for site_id in visible:
        rows = published_rows([site_id])
        planned_queue_ids = {
            *db.execute(
                rows(AppointmentTemplateWindow).with_only_columns(
                    AppointmentTemplateWindow.queue_id
                )
            ).scalars(),
            *db.execute(
                rows(AppointmentDayOverride).with_only_columns(
                    AppointmentDayOverride.queue_id
                )
            ).scalars(),
        }
        if not planned_queue_ids:
            continue
        policy, _ = policy_for(db, rows)
        first_day, last_day = generation_range(policy, moment)
        opening = published_schedules(
            db,
            [site_id],
            from_day=first_day - PLANNING_MARGIN,
            horizon_days=(last_day - first_day).days + 3,
        )[site_id]
        for queue in bookable_queues(db, rows):
            if queue.id in planned_queue_ids:
                created += generate_slots(
                    db, rows, queue, opening, first_day, last_day, moment=moment
                ).created
    return created


# --------------------------------------------------------------------------------------
# Blocks
# --------------------------------------------------------------------------------------


def active_blocks(
    db: Session, rows: RowSource, first_day: date, last_day: date
) -> list[BlockedRange]:
    """The blocks standing over ``first_day`` to ``last_day`` (with a day's margin either side)."""
    start_sast, _ = business_day_bounds(first_day - timedelta(days=1))
    _, end_sast = business_day_bounds(last_day + timedelta(days=1))
    return [
        BlockedRange.of(row)
        for row in db.execute(
            rows(AppointmentBlock).where(
                AppointmentBlock.lifted_at.is_(None),
                AppointmentBlock.starts_at < end_sast,
                AppointmentBlock.ends_at > start_sast,
            )
        ).scalars()
    ]


def list_blocks(
    db: Session, access: SiteAccess, *, include_lifted: bool = False
) -> list[AppointmentBlock]:
    """The clinic's blocks, most recent first."""
    statement = scoped_select(AppointmentBlock, access).order_by(
        AppointmentBlock.starts_at.desc()
    )
    if not include_lifted:
        statement = statement.where(AppointmentBlock.lifted_at.is_(None))
    return list(db.execute(statement).scalars().all())


def add_block(
    db: Session, access: SiteAccess, payload: BlockIn, queue: Queue | None
) -> tuple[AppointmentBlock, int]:
    """Block a range; return the block and how many booked places now sit inside it.

    The bookings are not cancelled: telling those patients and moving them is the booking flow's
    (Issue 81). The count is returned so the manager is not left to discover them.
    """
    block = AppointmentBlock(
        site_id=access.site_id,
        queue_id=queue.id if queue is not None else None,
        starts_at=stored_sast(payload.starts_at),
        ends_at=stored_sast(payload.ends_at),
        reason=payload.reason.strip(),
        created_by=str(access.user.id),
    )
    db.add(block)
    db.flush()
    inside = scoped_select(AppointmentSlot, access).where(
        AppointmentSlot.starts_at < block.ends_at,
        AppointmentSlot.ends_at > block.starts_at,
    )
    if queue is not None:
        inside = inside.where(AppointmentSlot.queue_id == queue.id)
    booked = sum(slot.booked_count for slot in db.execute(inside).scalars())
    return block, booked


def lift_block(db: Session, access: SiteAccess, block_id: str) -> AppointmentBlock:
    """Lift a block early: its slots are offered again. The row stays."""
    block = db.execute(
        scoped_select(AppointmentBlock, access).where(
            AppointmentBlock.id == block_id, AppointmentBlock.lifted_at.is_(None)
        )
    ).scalar_one_or_none()
    if block is None:
        raise BlockNotFoundError(
            "No such block at this clinic, or it is already lifted."
        )
    block.lifted_at = now_sast()
    db.flush()
    return block


# --------------------------------------------------------------------------------------
# The day view
# --------------------------------------------------------------------------------------


def offer_context(
    db: Session,
    rows: RowSource,
    opening: OpeningSchedule,
    first_day: date,
    last_day: date,
    moment: datetime,
) -> OfferContext:
    """Load what :func:`~src.modules.appointments.availability.refusal_for` needs for a range of days."""
    policy, _ = policy_for(db, rows)
    return OfferContext(
        policy=policy,
        blocks=active_blocks(db, rows, first_day, last_day),
        moment=moment,
        stretches=open_stretches(opening, first_day, last_day),
    )


def day_availability(
    db: Session,
    rows: RowSource,
    *,
    site_id: str,
    queues: Sequence[Queue],
    opening: OpeningSchedule,
    day: date,
    include_unavailable: bool = False,
    moment: datetime | None = None,
) -> DayAvailabilityOut:
    """One service day's bookable slots per queue, with each queue's day capacity.

    A slot's ``remaining`` is its own free places capped by what the queue's day has left, so a day
    that walk-ins have filled shows no room in any slot. Read live on every call.
    """
    moment = moment or now_sast()
    context = offer_context(db, rows, opening, day, day, moment)
    slots_by_queue: dict[str, list[AppointmentSlot]] = {}
    for slot in db.execute(
        rows(AppointmentSlot)
        .where(
            AppointmentSlot.service_day == day,
            AppointmentSlot.queue_id.in_([queue.id for queue in queues]),
        )
        .order_by(AppointmentSlot.starts_at)
    ).scalars():
        slots_by_queue.setdefault(slot.queue_id, []).append(slot)
    out: list[QueueDayOut] = []
    for queue in queues:
        usage = capacity.day_usage(db, queue, day)
        day_left = usage.remaining
        slots: list[SlotOut] = []
        for slot in slots_by_queue.get(queue.id, []):
            refusal = refusal_for(slot, context)
            left = slot.capacity - slot.booked_count
            if day_left is not None:
                left = min(left, day_left)
            if refusal is None and left <= 0:
                refusal = SlotRefusal.DAY_FULL
            if refusal is not None and not include_unavailable:
                continue
            slots.append(
                SlotOut(
                    id=slot.id,
                    queue_id=slot.queue_id,
                    service_id=slot.service_id,
                    starts_at=stored_sast(slot.starts_at),
                    ends_at=stored_sast(slot.ends_at),
                    capacity=slot.capacity,
                    booked=slot.booked_count,
                    remaining=max(left, 0) if refusal is None else 0,
                    refusal=refusal,
                )
            )
        out.append(
            QueueDayOut(
                queue_id=queue.id,
                queue_name=queue.name,
                daily_limit=usage.limit,
                tickets=usage.tickets,
                appointments=usage.appointments,
                day_remaining=day_left,
                slots=slots,
            )
        )
    return DayAvailabilityOut(site_id=site_id, day=day, as_of=moment, queues=out)
