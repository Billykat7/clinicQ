"""Property tests: no sequence of queue operations reaches an illegal state (Issue 47).

Issue 41 checked every ``(from, to)`` pair one move at a time. This file lets `Hypothesis`_ choose
the moves: long, arbitrary interleavings of every operation the queue engine offers, across three
queues, three patients and walk-ins, including the requests a buggy or hostile client would send
(any status to any status, a transfer to the same queue, an override with no reason). After **every**
step it checks the whole database against the rules M6 promised, and a failure is shrunk to the
shortest sequence that breaks one.

The operations are the ones a caller can reach, each called the way its route calls it:

* a join from any channel (``join_queue``), *Call next* (``call_next``);
* a staff move through the transitions route (``staff_move``), with any target status, and a
  clinician's start and finish on it;
* a patient's cancel (``cancel_own_ticket``) and a receptionist's (``cancel_ticket``);
* a transfer (``transfer_ticket``), a priority override (``override_priority``);
* the recall timer's sweep (``run_recall_timers``).

The rules checked are written out here from the product doc and the issues, **not imported** from
``src/modules/queue/lifecycle.py``, so this is a second reading of the lifecycle rather than the
table checking itself:

1. every status change of every ticket is a move in :data:`SPEC`, and a terminal ticket never
   changes at all;
2. a refused operation changes nothing, anywhere;
3. each ticket's audited status moves form one legal path from ``waiting`` to its current status;
4. numbers in each queue and day are 1..n with no gap and no repeat;
5. no patient holds two active tickets in one queue;
6. the waiting tickets' places in each queue are exactly 0..k-1;
7. a ticket's timestamps agree with its status;
8. a ``transferred`` ticket has exactly one successor, in the same visit, and a ``cancelled``
   ticket records the channel it was cancelled through.

.. _Hypothesis: https://hypothesis.readthedocs.io/en/latest/stateful.html
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

import pytest
from hypothesis import HealthCheck, event, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.commons.enums import (
    TICKET_ACTIVE_STATUSES,
    ActorKind,
    AuditAction,
    AuditEntityType,
    PatientChannel,
    PriorityReason,
    SiteStatus,
    TicketSource,
    TicketStatus,
    TransferReason,
)
from src.commons.exceptions import ConflictError, NotFoundError, UnprocessableError
from src.commons.time import now_sast
from src.database.models import AuditEvent, Base, Patient, Queue, Site, Ticket
from src.database.schema import sqlite_schema_translate_map
from src.modules.notifications.sms import FakeSmsProvider
from src.modules.queue.cancellation import cancel_own_ticket, cancel_ticket
from src.modules.queue.lifecycle import Actor, call_next, staff_move
from src.modules.queue.priority import override_priority
from src.modules.queue.service import join_queue
from src.modules.queue.snapshot import NoSnapshotCache, set_snapshot_cache
from src.modules.queue.tickets import waiting_ahead
from src.modules.queue.timers import run_recall_timers
from src.modules.queue.transfer import transfer_ticket
from src.modules.sites.hours import published_schedules
from tests.factories import PatientFactory, QueueFactory, SiteFactory
from tests.integration.queue.conftest import open_all_day, queue_settings

W, C, R, P = "waiting", "called", "recalled", "in_progress"
D, N, X, T = "done", "no_show", "cancelled", "transferred"

#: The lifecycle as ``docs/PRODUCT/03-booking-and-queue.md`` and Issues 41, 43–45 describe it.
SPEC: dict[str, set[str]] = {
    W: {C, X, T},
    C: {P, R, N, X},
    R: {P, N, X},
    P: {D, T},
    D: set(),
    N: set(),
    X: set(),
    T: set(),
}
TERMINAL = {status for status, moves in SPEC.items() if not moves}

_SETTINGS = queue_settings(
    queue_join_rate_limit_per_phone=100_000,
    queue_join_rate_limit_per_ip=100_000,
    queue_join_site_daily_cap=100_000,
)
_STAFF = Actor(kind=ActorKind.STAFF, label="desk@clinicq.example", user_id="staff-1")
#: What a refused operation may raise. Anything else is a bug and fails the run.
_REFUSALS = (ConflictError, UnprocessableError, NotFoundError)


class QueueEngine(RuleBasedStateMachine):
    """One clinic, three queues, three patients; every operation, in any order."""

    def __init__(self) -> None:
        super().__init__()
        set_snapshot_cache(NoSnapshotCache())
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        ).execution_options(schema_translate_map=sqlite_schema_translate_map())
        Base.metadata.create_all(self.engine)
        self.db: Session = sessionmaker(bind=self.engine, autoflush=False)()
        site = SiteFactory.create(self.db, status=SiteStatus.VERIFIED)
        open_all_day(self.db, site.id)
        self.site_id = site.id
        self.queue_ids = [
            QueueFactory.create(self.db, site_id=site.id, ticket_prefix=prefix).id
            for prefix in "TDP"
        ]
        self.patient_ids = [
            PatientFactory.create(self.db, phone_e164=f"+27105550{n:03d}").id
            for n in range(3)
        ]
        self.db.commit()
        self.steps = 0
        self.refused = 0

    def teardown(self) -> None:
        # What the run reached, for ``--hypothesis-show-statistics``: a pass that never got past
        # ``waiting`` would prove little.
        for status in set(self.db.scalars(select(Ticket.status))):
            event(f"reached: {status}")
        self.db.close()
        self.engine.dispose()

    # --- how every operation is run and judged -----------------------------------------------

    def _state(self) -> dict[str, tuple[Any, ...]]:
        self.db.expire_all()
        return {
            t.id: (
                t.status,
                t.called_at,
                t.started_at,
                t.completed_at,
                t.order_key,
                t.cancelled_via,
            )
            for t in self.db.scalars(select(Ticket))
        }

    def _ticket_ids(self) -> list[str]:
        return list(
            self.db.scalars(select(Ticket.id).order_by(Ticket.joined_at, Ticket.id))
        )

    def _pick(self, data: st.DataObject, label: str) -> str | None:
        """Any ticket, drawn mostly from those still in the day so runs reach the later statuses."""
        ids = self._ticket_ids()
        if not ids:
            return None
        active = list(
            self.db.scalars(
                select(Ticket.id)
                .where(Ticket.status.in_([s.value for s in TICKET_ACTIVE_STATUSES]))
                .order_by(Ticket.joined_at, Ticket.id)
            )
        )
        anyone = st.sampled_from(ids)
        return data.draw(
            st.sampled_from(active) | anyone if active else anyone, label=label
        )

    def _apply(self, operation: Any) -> None:
        """Run one operation; commit it, or roll back a refusal and prove it changed nothing."""
        before = self._state()
        self.steps += 1
        try:
            operation()
        except _REFUSALS as refused:
            event(f"refused: {refused.code}")
            self.db.rollback()
            self.refused += 1
            assert self._state() == before, "a refused operation changed something"
            return
        self.db.commit()
        after = self._state()
        for ticket_id, was in before.items():
            now = after[ticket_id]
            if was[0] in TERMINAL:
                assert now[:4] == was[:4], (
                    f"terminal {was[0]} ticket changed: {was} -> {now}"
                )
            elif now[0] != was[0]:
                assert now[0] in SPEC[was[0]], f"illegal move {was[0]} -> {now[0]}"

    # --- the operations ----------------------------------------------------------------------

    @rule(
        queue=st.integers(0, 2),
        who=st.one_of(st.none(), st.integers(0, 2)),
        channel=st.sampled_from(
            [TicketSource.WEB, TicketSource.USSD, TicketSource.WHATSAPP]
        ),
    )
    def join(self, queue: int, who: int | None, channel: TicketSource) -> None:
        def run() -> None:
            site = self.db.get(Site, self.site_id)
            patient = (
                None if who is None else self.db.get(Patient, self.patient_ids[who])
            )
            join_queue(
                self.db,
                site=site,
                queue=self.db.get(Queue, self.queue_ids[queue]),
                schedule=published_schedules(self.db, [self.site_id])[self.site_id],
                source=TicketSource.WALK_IN if patient is None else channel,
                patient=patient,
                actor="property-test",
                walk_in_name="Walk-in" if patient is None else None,
                settings=_SETTINGS,
            )

        self._apply(run)

    @rule(queue=st.integers(0, 2))
    def press_call_next(self, queue: int) -> None:
        self._apply(
            lambda: call_next(
                self.db, self.db.get(Queue, self.queue_ids[queue]), actor=_STAFF
            )
        )

    @rule(data=st.data())
    def staff_moves_a_ticket(self, data: st.DataObject) -> None:
        if (ticket_id := self._pick(data, "ticket")) is None:
            return
        # Usually a move the lifecycle offers from here, so runs get past ``waiting``; often any
        # status at all, so the refusals are exercised. The judging never reads this choice.
        offered = sorted(SPEC[self.db.get(Ticket, ticket_id).status])
        anything = st.sampled_from(list(TicketStatus))
        to = data.draw(
            st.sampled_from(offered).map(TicketStatus) | anything
            if offered
            else anything,
            label="to",
        )
        self._apply(lambda: staff_move(self.db, ticket_id, to, actor=_STAFF))

    @rule(queue=st.integers(0, 2))
    def clinician_presses_start_or_finish(self, queue: int) -> None:
        """The dashboard's next button for a room: start the called patient, or finish the one in."""
        rows = self.db.execute(
            select(Ticket.id, Ticket.status)
            .where(
                Ticket.queue_id == self.queue_ids[queue], Ticket.status.in_([C, R, P])
            )
            .order_by(Ticket.joined_at, Ticket.id)
        ).first()
        if rows is not None:
            ticket_id, current = rows
            to = TicketStatus.DONE if current == P else TicketStatus.IN_PROGRESS
            self._apply(lambda: staff_move(self.db, ticket_id, to, actor=_STAFF))

    @rule(data=st.data())
    def patient_cancels(self, data: st.DataObject) -> None:
        if (ticket_id := self._pick(data, "ticket")) is None:
            return
        owner = self.db.get(Ticket, ticket_id).patient_id
        patient_id = (
            owner or self.patient_ids[0]
        )  # a walk-in's ticket: someone else's, not found
        self._apply(
            lambda: cancel_own_ticket(
                self.db, patient_id, ticket_id, channel=PatientChannel.USSD
            )
        )

    @rule(data=st.data())
    def reception_cancels(self, data: st.DataObject) -> None:
        if (ticket_id := self._pick(data, "ticket")) is not None:
            self._apply(
                lambda: cancel_ticket(
                    self.db, ticket_id, channel=PatientChannel.WALK_IN, actor=_STAFF
                )
            )

    @rule(data=st.data(), queue=st.integers(0, 2))
    def transfer(self, data: st.DataObject, queue: int) -> None:
        if (ticket_id := self._pick(data, "ticket")) is not None:
            self._apply(
                lambda: transfer_ticket(
                    self.db,
                    ticket_id,
                    self.db.get(Queue, self.queue_ids[queue]),
                    actor=_STAFF,
                    reason=TransferReason.NEXT_STEP,
                )
            )

    @rule(
        data=st.data(),
        reason=st.one_of(st.none(), st.sampled_from(list(PriorityReason))),
    )
    def override(self, data: st.DataObject, reason: PriorityReason | None) -> None:
        ticket_id, ahead_of = self._pick(data, "ticket"), self._pick(data, "ahead_of")
        if ticket_id is not None and ahead_of is not None:
            self._apply(
                lambda: override_priority(
                    self.db,
                    ticket_id,
                    ahead_of_ticket_id=ahead_of,
                    reason=reason,
                    actor=_STAFF,
                )
            )

    @rule(minutes=st.integers(0, 30))
    def recall_timer_sweeps(self, minutes: int) -> None:
        self._apply(
            lambda: run_recall_timers(
                self.db,
                moment=now_sast() + timedelta(minutes=minutes),
                settings=_SETTINGS,
                sms_provider=FakeSmsProvider(),
            )
        )

    # --- the rules, after every step ---------------------------------------------------------

    @invariant()
    def statuses_have_legal_audited_histories(self) -> None:
        moves: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for entity_id, diff in self.db.execute(
            select(AuditEvent.entity_id, AuditEvent.diff).where(
                AuditEvent.entity_type == AuditEntityType.TICKET.value,
                AuditEvent.action == AuditAction.UPDATE.value,
            )
        ):
            if diff and "status" in diff:
                moves[entity_id].append(
                    (diff["status"]["before"], diff["status"]["after"])
                )
        for ticket in self.db.scalars(select(Ticket)):
            remaining, current = list(moves[ticket.id]), W
            while remaining:
                step = next((m for m in remaining if m[0] == current), None)
                assert step is not None, (
                    f"{ticket.number}: audited moves {moves[ticket.id]} break"
                )
                assert step[1] in SPEC[step[0]], (
                    f"{ticket.number}: audited illegal move {step}"
                )
                remaining.remove(step)
                current = step[1]
            assert current == ticket.status, (
                ticket.number,
                moves[ticket.id],
                ticket.status,
            )

    @invariant()
    def numbers_are_gapless_per_queue_and_day(self) -> None:
        sequences: dict[tuple[str, Any], list[int]] = defaultdict(list)
        for ticket in self.db.scalars(select(Ticket)):
            sequences[(ticket.queue_id, ticket.service_day)].append(ticket.sequence)
            assert ticket.number.endswith(f"{ticket.sequence:03d}")
        for key, found in sequences.items():
            assert sorted(found) == list(range(1, len(found) + 1)), key

    @invariant()
    def one_active_ticket_per_patient_per_queue(self) -> None:
        active = Counter(
            (t.patient_id, t.queue_id, t.service_day)
            for t in self.db.scalars(
                select(Ticket).where(
                    Ticket.patient_id.is_not(None),
                    Ticket.status.in_([s.value for s in TICKET_ACTIVE_STATUSES]),
                )
            )
        )
        assert all(count == 1 for count in active.values()), active

    @invariant()
    def waiting_places_are_zero_to_k(self) -> None:
        places: dict[str, list[int]] = defaultdict(list)
        for ticket in self.db.scalars(select(Ticket).where(Ticket.status == W)):
            places[ticket.queue_id].append(waiting_ahead(self.db, ticket))
        for queue_id, found in places.items():
            assert sorted(found) == list(range(len(found))), (queue_id, found)

    @invariant()
    def timestamps_agree_with_status(self) -> None:
        for t in self.db.scalars(select(Ticket)):
            if t.status == W:
                assert (t.called_at, t.started_at, t.completed_at) == (
                    None,
                    None,
                    None,
                ), t.number
            if t.status in {C, R, P, D, N}:
                assert t.called_at is not None, t.number
            if t.status in {P, D}:
                assert t.started_at is not None, t.number
            if t.status == D:
                assert t.completed_at is not None, t.number
            if t.status == R:
                assert t.recalled_at is not None, t.number

    @invariant()
    def transfers_have_a_successor_and_cancellations_a_channel(self) -> None:
        tickets = list(self.db.scalars(select(Ticket)))
        successors = Counter(
            t.transferred_from_id for t in tickets if t.transferred_from_id
        )
        by_id = {t.id: t for t in tickets}
        for t in tickets:
            if t.status == T:
                assert successors[t.id] == 1, (
                    f"{t.number} is transferred with nowhere to go"
                )
            if t.transferred_from_id:
                assert by_id[t.transferred_from_id].visit_id == t.visit_id, t.number
            assert (t.status == X) == (t.cancelled_via is not None), (
                t.number,
                t.cancelled_via,
            )


QueueEngine.TestCase.settings = settings(
    max_examples=100,
    stateful_step_count=50,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
test_no_sequence_of_queue_operations_reaches_an_illegal_state = (
    pytest.mark.filterwarnings("ignore::DeprecationWarning")(QueueEngine.TestCase)
)
