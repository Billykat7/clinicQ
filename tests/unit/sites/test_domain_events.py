"""Something happened, and other modules may care — without the two being wired together (Issue 24).

The bus exists so that closing a clinic does not depend on an SMS gateway. Three properties carry
that, and each has a test: a subscriber that fails does not undo what happened, nothing is published
until the transaction commits, and a rollback publishes nothing at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.commons.enums import SaProvince, SiteSector
from src.commons.geo import Coordinates
from src.commons.time import now_sast
from src.core import domain_events
from src.core.domain_events import (
    DomainEvent,
    SiteClosureAnnounced,
    publish,
    publish_after_commit,
    subscribe,
    subscribers,
)
from src.database.models import Base, Site
from src.database.schema import sqlite_schema_translate_map


@dataclass(frozen=True, slots=True)
class _Thing(DomainEvent):
    """An event used only by this file."""

    label: str


@pytest.fixture(autouse=True)
def _isolated_subscribers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test its own subscriber registry, so registrations never leak between them."""
    from collections import defaultdict

    monkeypatch.setattr(domain_events, "_SUBSCRIBERS", defaultdict(list))


@pytest.fixture
def session() -> Iterator[Session]:
    """A real session on an in-memory database, so the commit hooks are the real ones."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        yield db
    engine.dispose()


def _a_clinic() -> dict[str, object]:
    """The columns a ``Site`` needs, so a test can make a real transaction to roll back."""
    return {
        "slug": f"clinic-{now_sast().timestamp()}",
        "name": "A Clinic",
        "sector": SiteSector.PUBLIC.value,
        "location": Coordinates(latitude=-26.2, longitude=28.0),
        "address_line": "1 Street",
        "city": "Johannesburg",
        "province": SaProvince.GAUTENG.value,
    }


def _closure(reason: str = "The water is off.") -> SiteClosureAnnounced:
    """A closure event with everything filled in."""
    return SiteClosureAnnounced(
        site_id="site-a",
        closure_id="closure-1",
        reason=reason,
        starts_at=now_sast(),
        ends_at=None,
        announced_by="manager@clinicq.example",
    )


def test_a_subscriber_hears_its_own_event_and_nobody_elses() -> None:
    """Dispatch is by event class, not by a string anybody could mistype."""
    heard: list[DomainEvent] = []
    subscribe(_Thing)(heard.append)

    publish(_Thing(label="one"))
    publish(_closure())

    assert [event.label for event in heard] == ["one"]  # type: ignore[attr-defined]
    assert subscribers(_Thing) == (heard.append,)


def test_a_failing_subscriber_does_not_stop_the_next_one_or_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The event describes something that already happened; a bad reaction cannot undo it."""
    heard: list[str] = []

    @subscribe(_Thing)
    def _explode(event: _Thing) -> None:
        raise RuntimeError("the gateway is down")

    @subscribe(_Thing)
    def _record(event: _Thing) -> None:
        heard.append(event.label)

    publish(_Thing(label="two"))  # does not raise

    assert heard == ["two"]
    assert "the gateway is down" in caplog.text


def test_nothing_is_published_until_the_transaction_commits(session: Session) -> None:
    """A subscriber that reads the database must find the row the event is about."""
    heard: list[DomainEvent] = []
    subscribe(SiteClosureAnnounced)(heard.append)

    publish_after_commit(session, _closure())
    assert heard == []

    session.commit()
    assert len(heard) == 1


def test_a_rollback_publishes_nothing(session: Session) -> None:
    """A change that did not happen must not be announced as though it did.

    The second ``commit`` is the point: a discarded event must not surface on the session's *next*
    transaction, which is what would happen if the queue were only drained and never cleared.
    """
    heard: list[DomainEvent] = []
    subscribe(SiteClosureAnnounced)(heard.append)

    session.add(Site(**_a_clinic()))
    publish_after_commit(session, _closure())
    session.rollback()

    session.add(Site(**_a_clinic()))
    session.commit()

    assert heard == []


def test_two_events_in_one_transaction_both_arrive(session: Session) -> None:
    """The regression this module's queue exists for.

    An earlier version attached a self-removing listener per event, and removing one from inside
    the ``after_commit`` dispatch raised ``RuntimeError: deque mutated during iteration`` — so a
    request that closed a clinic and lifted a stale closure in one go answered 500.
    """
    heard: list[DomainEvent] = []
    subscribe(SiteClosureAnnounced)(heard.append)

    publish_after_commit(session, _closure("first"))
    publish_after_commit(session, _closure("second"))
    session.commit()

    assert [event.reason for event in heard] == ["first", "second"]  # type: ignore[attr-defined]


def test_a_rolled_back_savepoint_discards_only_what_was_published_inside_it(
    session: Session,
) -> None:
    """Issue 63: the recall sweep moves each ticket in a savepoint and skips one staff moved first.

    Before the fix, that skip threw away the events (and so the patient messages) of every ticket
    the sweep had already moved in the same transaction.
    """
    heard: list[str] = []
    subscribe(_Thing)(lambda event: heard.append(event.label))

    session.add(Site(**_a_clinic()))
    publish_after_commit(session, _Thing("before the savepoint"))
    with pytest.raises(RuntimeError), session.begin_nested():
        publish_after_commit(session, _Thing("inside the failed savepoint"))
        raise RuntimeError("staff moved it first")
    with session.begin_nested():
        publish_after_commit(session, _Thing("inside a kept savepoint"))
    session.commit()

    assert heard == ["before the savepoint", "inside a kept savepoint"]


def test_releasing_a_savepoint_publishes_nothing_until_the_real_commit(
    session: Session,
) -> None:
    """Issue 68: SQLAlchemy fires ``after_commit`` on a savepoint release, before the data is visible.

    The notification service writes its row in a savepoint inside a queue move, so every call used to
    publish the move's events early, and a ticket page's stream read the ticket before the call.
    """
    heard: list[str] = []
    subscribe(_Thing)(lambda event: heard.append(event.label))

    session.add(Site(**_a_clinic()))
    publish_after_commit(session, _Thing("the move"))
    with session.begin_nested():
        session.add(Site(**_a_clinic()))
    assert heard == []  # released, not committed
    session.commit()
    assert heard == ["the move"]
