"""A small in-process event bus: something happened, and other modules may care (Issue 24).

ClinicQ has a standing rule that a module which *decides* something does not also *deliver* the
consequence. A clinic manager closing their clinic for the afternoon is the clearest case: closing
it is the sites module's business, telling the twenty people holding a ticket is the notification
service's (Issue 63), and wiring the one directly to the other would make the closure fail when the
SMS gateway is down.

So the closing code publishes a fact and returns. Three properties, each deliberate:

* **Publishing never raises.** A subscriber that fails is logged and the next one still runs: the
  event describes something that has *already happened*, and a failure to react must not roll back
  the thing that happened.
* **Publishing happens after the commit.** A subscriber that reads the database must see the change
  the event describes, so the publisher commits first. :func:`publish_after_commit` attaches the
  publication to the session's ``after_commit``, which is the one safe place to say "when this is
  really true".
* **Subscriptions are registered at import time and are process-local.** This is not a message
  queue and does not pretend to be: nothing is persisted, nothing is retried, and a restart forgets
  what was in flight. When delivery has to survive a restart, the subscriber's own job is to write
  a durable row (which is exactly what the notification service does).

    @subscribe(SiteClosureAnnounced)
    def tell_the_ticket_holders(event: SiteClosureAnnounced) -> None: ...
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.orm import Session, SessionTransaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Base class for a fact about something that has already happened.

    Frozen, because an event is a record rather than a message anyone may edit on the way past.
    """


#: ``{event class: [handler, ...]}``. Populated by :func:`subscribe` at import time.
_SUBSCRIBERS: dict[type[DomainEvent], list[Callable[[Any], None]]] = defaultdict(list)


def subscribe[E: DomainEvent](
    event_type: type[E],
) -> Callable[[Callable[[E], None]], Callable[[E], None]]:
    """Register a handler for ``event_type``. Usable as a decorator."""

    def register(handler: Callable[[E], None]) -> Callable[[E], None]:
        _SUBSCRIBERS[event_type].append(handler)
        return handler

    return register


def subscribers(event_type: type[DomainEvent]) -> tuple[Callable[[Any], None], ...]:
    """The handlers registered for ``event_type``, in registration order (for tests and /info)."""
    return tuple(_SUBSCRIBERS.get(event_type, ()))


def publish(event: DomainEvent) -> None:
    """Hand ``event`` to every subscriber. Never raises: a failing handler is logged and skipped.

    Call this only once the change the event describes is committed — or let
    :func:`publish_after_commit` do it for you.
    """
    for handler in _SUBSCRIBERS.get(type(event), ()):
        try:
            handler(event)
        except Exception:
            logger.exception(
                "A subscriber to %s failed; the event stands and the next subscriber runs.",
                type(event).__name__,
            )


#: Where a session's not-yet-published events wait. On ``Session.info`` rather than in a module
#: global so two sessions (two requests) can never drain each other's queue.
_PENDING_KEY = "clinicq_pending_domain_events"


def _drain(session: Session) -> None:
    """Publish everything this session queued, now that its transaction has committed."""
    for _savepoint, event in session.info.pop(_PENDING_KEY, []):
        publish(event)


def _inside(savepoint: SessionTransaction | None, ended: SessionTransaction) -> bool:
    """Whether an event queued in ``savepoint`` was inside the transaction ``ended``."""
    while savepoint is not None:
        if savepoint is ended:
            return True
        savepoint = savepoint.parent
    return False


def _discard(session: Session, previous: SessionTransaction) -> None:
    """Throw away what a rollback undid: nothing it describes happened.

    Registered on ``after_soft_rollback``, which fires for every rollback: a real one, a caller that
    changed its mind before writing anything, and a **savepoint**. A rolled-back savepoint undoes only
    what was queued inside it (Issue 63). The recall sweep moves each ticket in its own savepoint and
    skips one that staff moved first, and before this distinction that skip threw away the events,
    and so the messages, of every ticket the sweep had already moved. Any other rollback discards
    everything the session queued.
    """
    if not previous.nested:
        session.info.pop(_PENDING_KEY, None)
        return
    pending: list[tuple[SessionTransaction | None, DomainEvent]] = session.info.get(
        _PENDING_KEY, []
    )
    pending[:] = [item for item in pending if not _inside(item[0], previous)]


def publish_after_commit(session: Session, event: DomainEvent) -> None:
    """Publish ``event`` when ``session``'s current transaction commits, and not before.

    A subscriber that queries the database has to see the row the event is about, and a subscriber
    must never hear about a change that then rolled back. So the event is queued on the session and
    drained by a listener attached **once** per session.

    The "once" matters: an earlier version attached a self-removing listener per event, and calling
    ``event.remove`` from inside the ``after_commit`` dispatch raised ``RuntimeError: deque mutated
    during iteration`` the moment a request published two events — which is any request that closes
    a clinic and lifts a stale closure in one go. The queue lives on ``Session.info``, so the two
    permanent listeners are all that is ever registered.
    """
    pending: list[tuple[SessionTransaction | None, DomainEvent]] = (
        session.info.setdefault(_PENDING_KEY, [])
    )
    # Remember the innermost savepoint, so rolling that savepoint back discards only this event.
    pending.append((session.get_nested_transaction(), event))
    if not sqlalchemy_event.contains(session, "after_commit", _drain):
        sqlalchemy_event.listen(session, "after_commit", _drain)
        sqlalchemy_event.listen(session, "after_soft_rollback", _discard)


@dataclass(frozen=True, slots=True)
class SiteClosureAnnounced(DomainEvent):
    """A clinic has closed unexpectedly, and the people waiting for it need to be told (Issue 24).

    The event carries the *reason and the window*, not a message and not a recipient list: who is
    holding a ticket is a question for the queue (Issue 39) and how to reach them is a question for
    the notification service (Issue 63). Deciding that here would put a clinic's closure at the
    mercy of an SMS gateway.
    """

    site_id: str
    closure_id: str
    #: The manager's own words, shown to the patient. Never a template key.
    reason: str
    #: Aware ``Africa/Johannesburg`` datetimes; ``ends_at`` is ``None`` for "until further notice".
    starts_at: datetime
    ends_at: datetime | None
    #: Who closed it, for the trail the notification carries back to the clinic.
    announced_by: str


@dataclass(frozen=True, slots=True)
class SiteClosureLifted(DomainEvent):
    """A clinic that was closed early is open again before its closure was due to end."""

    site_id: str
    closure_id: str
    lifted_by: str


@dataclass(frozen=True, slots=True)
class SiteStatusChanged(DomainEvent):
    """A clinic's listing moved, and whoever submitted it should be told (Issue 29).

    The event carries the decision and the note, not a message: what to send and how to reach the
    submitter is the notification service's (Issue 63). The same reasoning as
    :class:`SiteClosureAnnounced` — a platform admin must be able to reject a clinic while the SMS
    gateway is down.
    """

    site_id: str
    site_name: str
    #: :class:`~src.commons.enums.SiteStatus` values, as strings on the wire.
    from_status: str
    to_status: str
    #: The platform admin who decided, or ``"submitter"`` for the submission itself.
    decided_by: str
    #: What they wrote, shown to the submitter. ``None`` for an approval with nothing to add.
    note: str | None
    #: Where to reach whoever put the clinic forward. May be ``None`` for a clinic an operator
    #: typed in, which nobody is waiting to hear about.
    contact_email: str | None
    contact_phone: str | None


@dataclass(frozen=True, slots=True)
class QueueChanged(DomainEvent):
    """Something changed in a queue's day: a join, a call, a finish, a transfer (Issue 49).

    Published after the commit by the one hook every queue write already calls
    (:func:`src.modules.queue.snapshot.on_queue_changed`), so a live screen hears about exactly the
    changes that happened and never one that rolled back. It names the queue and nothing about any
    patient: a screen that hears it reads the queue again through its own gate.
    """

    site_id: str
    queue_id: str
    #: Set when the change was a call: the number now called, which a public board shows anyway.
    called_number: str | None = None


@dataclass(frozen=True, slots=True)
class BoardSettingsChanged(DomainEvent):
    """What a clinic's waiting-room screen may show changed (Issues 27 and 49)."""

    site_id: str
