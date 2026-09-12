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
from sqlalchemy.orm import Session

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
    for event in session.info.pop(_PENDING_KEY, []):
        publish(event)


def _discard(session: Session, _previous: object = None) -> None:
    """Throw away what this session queued: the transaction rolled back, so nothing happened.

    Registered on ``after_soft_rollback`` as well as ``after_rollback``, which is why it takes the
    extra argument that hook passes: ``after_rollback`` fires only when there was a real database
    transaction to undo, and a caller that queued an event and then changed its mind before writing
    anything must still not have it published by the *next* commit on the same session.
    """
    session.info.pop(_PENDING_KEY, None)


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
    pending: list[DomainEvent] = session.info.setdefault(_PENDING_KEY, [])
    pending.append(event)
    if not sqlalchemy_event.contains(session, "after_commit", _drain):
        sqlalchemy_event.listen(session, "after_commit", _drain)
        sqlalchemy_event.listen(session, "after_rollback", _discard)
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
