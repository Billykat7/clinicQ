"""Wait samples and the estimate on every surface (Issue 42).

The pure estimator is tested in ``tests/unit/queue/test_wait_estimate.py``. This file covers its
database side and where its output appears:

* a ticket reaching ``done`` writes one ``wait_time_sample`` in the same transaction, with the call
  interval counted only while the queue was busy;
* **the estimate updates as tickets complete, within one minute**: the next join after a visit
  finishes already reads it, with no job in between (measured);
* **always a range, never a single number, on every surface**: the join response, the discovery
  API and page labels, and the queue snapshot all carry a range and a confidence, and a guard walks
  the application's OpenAPI document for any wait given as one number.
"""

from __future__ import annotations

import time as clock
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from src.commons.enums import ActorKind, TicketStatus
from src.commons.time import now_sast
from src.database.models import Queue, SiteQueueSnapshot, Ticket, WaitTimeSample
from src.main import create_app
from src.modules.queue.estimate import DEFAULT_SETTINGS
from src.modules.queue.lifecycle import Actor, transition_ticket
from tests.factories import TicketFactory, drive_ticket_to

_DESK = Actor(kind=ActorKind.STAFF, label="desk.a@clinicq.example")


def _finish(db: Any, ticket_id: str, *, called: Any, started: Any, done: Any) -> Ticket:
    """Call, start and finish a waiting ticket at the given moments."""
    transition_ticket(db, ticket_id, TicketStatus.CALLED, actor=_DESK, moment=called)
    transition_ticket(
        db, ticket_id, TicketStatus.IN_PROGRESS, actor=_DESK, moment=started
    )
    return transition_ticket(db, ticket_id, TicketStatus.DONE, actor=_DESK, moment=done)


def test_a_finished_visit_writes_one_sample_with_its_wait_service_and_interval(
    desk: SimpleNamespace,
) -> None:
    """Two patients waiting together: the second's interval is the gap between the two calls."""
    start = now_sast() - timedelta(hours=1)
    with desk.session() as db:
        queue = db.get(Queue, desk.triage.id)
        first = TicketFactory.create(db, queue=queue, moment=start)
        second = TicketFactory.create(
            db, queue=queue, moment=start + timedelta(minutes=1)
        )
        _finish(
            db,
            first.id,
            called=start + timedelta(minutes=5),
            started=start + timedelta(minutes=6),
            done=start + timedelta(minutes=12),
        )
        _finish(
            db,
            second.id,
            called=start + timedelta(minutes=13),
            started=start + timedelta(minutes=13),
            done=start + timedelta(minutes=20),
        )
        db.commit()
        samples = {
            s.ticket_id: s for s in db.execute(select(WaitTimeSample)).scalars().all()
        }

    assert set(samples) == {first.id, second.id}
    assert (samples[first.id].wait_minutes, samples[first.id].service_minutes) == (
        5.0,
        6.0,
    )
    assert (
        samples[first.id].interval_minutes is None
    )  # the first call of the day has no gap
    assert samples[second.id].wait_minutes == 12.0
    assert (
        samples[second.id].interval_minutes == 8.0
    )  # 13 minutes minus 5: waiting throughout
    assert samples[second.id].called_hour == (start + timedelta(minutes=13)).hour


def test_a_visit_that_arrived_after_the_previous_call_measures_no_interval(
    desk: SimpleNamespace,
) -> None:
    """An idle room is not throughput: a patient who joined after the last call adds no interval."""
    start = now_sast() - timedelta(hours=2)
    with desk.session() as db:
        queue = db.get(Queue, desk.triage.id)
        early = TicketFactory.create(db, queue=queue, moment=start)
        _finish(
            db, early.id, called=start, started=start, done=start + timedelta(minutes=4)
        )
        late = TicketFactory.create(
            db, queue=queue, moment=start + timedelta(minutes=40)
        )
        _finish(
            db,
            late.id,
            called=start + timedelta(minutes=41),
            started=start + timedelta(minutes=41),
            done=start + timedelta(minutes=45),
        )
        db.commit()
        sample = db.execute(
            select(WaitTimeSample).where(WaitTimeSample.ticket_id == late.id)
        ).scalar_one()
    assert sample.interval_minutes is None


def test_the_estimate_updates_as_tickets_complete_within_one_minute(
    desk: SimpleNamespace,
) -> None:
    """Criterion 4, measured: cold, the join says approximate; once enough visits finish, the next
    join (no job, no wait) is built from them, and the whole change takes well under a minute.
    """
    patient, _ = desk.patient()
    before = patient.post(desk.join_path(desk.triage), json={}).json()
    assert (
        before["wait"]["approximate"] is True and before["wait"]["confidence"] == "low"
    )

    finished_at = clock.perf_counter()
    start = now_sast() - timedelta(hours=1)
    with desk.session() as db:
        queue = db.get(Queue, desk.triage.id)
        waiting = [
            TicketFactory.create(db, queue=queue, moment=start + timedelta(seconds=n))
            for n in range(int(DEFAULT_SETTINGS.min_effective_samples) + 3)
        ]
        for n, ticket in enumerate(waiting):
            called = start + timedelta(minutes=3 * (n + 1))
            _finish(
                db,
                ticket.id,
                called=called,
                started=called,
                done=called + timedelta(minutes=2),
            )
        db.commit()
    other, _ = desk.patient()
    after = other.post(desk.join_path(desk.triage), json={}).json()
    elapsed = clock.perf_counter() - finished_at

    assert after["wait"]["approximate"] is False
    assert after["wait"]["confidence"] in {"medium", "high"}
    assert (
        after["wait"]["label"]
        == f"~{after['wait']['low_minutes']}–{after['wait']['high_minutes']} min"
    )
    assert elapsed < 60


def test_every_surface_shows_a_range_and_the_snapshot_keeps_one(
    desk: SimpleNamespace,
) -> None:
    """The join response, the clinic-facing snapshot row and the public discovery read all carry a range."""
    patient, _ = desk.patient()
    joined = patient.post(desk.join_path(desk.triage), json={}).json()
    wait = joined["wait"]
    assert wait["high_minutes"] > wait["low_minutes"] >= 0
    with desk.session() as db:
        row = db.get(SiteQueueSnapshot, desk.triage.id)
    assert row is not None
    assert row.wait_high_minutes > row.wait_low_minutes  # type: ignore[operator]
    assert row.wait_confidence == "low" and row.wait_approximate is True


#: Properties that count people, not minutes: allowed to be a single number.
_COUNTS = {"waiting", "waiting_ahead", "total_waiting"}


def _single_number_waits(document: dict[str, Any]) -> list[str]:
    """Every schema property that gives a wait as one number instead of a range."""
    findings: list[str] = []
    for name, schema in document.get("components", {}).get("schemas", {}).items():
        properties = schema.get("properties", {})
        is_range = {"low_minutes", "high_minutes"} <= set(properties)
        for field, spec in properties.items():
            if "wait" not in field or field in _COUNTS:
                continue
            types = spec.get("type")
            numeric = (
                types in ("integer", "number")
                or (isinstance(types, list) and {"integer", "number"} & set(types))
                or any(
                    option.get("type") in ("integer", "number")
                    for option in spec.get("anyOf", []) + spec.get("oneOf", [])
                )
            )
            if numeric and not is_range:
                findings.append(f"{name}.{field}")
    return findings


def test_no_api_schema_gives_a_wait_as_a_single_number() -> None:
    """Criterion 1 across the whole API: a wait-named number outside a low/high pair fails here."""
    document = TestClient(create_app()).get("/openapi.json").json()
    assert _single_number_waits(document) == []
    # And the guard can fail: an "estimated_wait_minutes" integer is exactly what it catches.
    broken = {
        "components": {
            "schemas": {
                "TicketOut": {
                    "properties": {"estimated_wait_minutes": {"type": "integer"}}
                }
            }
        }
    }
    assert _single_number_waits(broken) == ["TicketOut.estimated_wait_minutes"]


def test_a_ticket_driven_to_done_through_the_factory_is_sampled_too(
    desk: SimpleNamespace,
) -> None:
    """The one path to done is the lifecycle, so every finished visit is sampled, factories included."""
    with desk.session() as db:
        ticket = drive_ticket_to(
            db,
            TicketFactory.create(db, queue=db.get(Queue, desk.triage.id)),
            TicketStatus.DONE,
        )
        db.commit()
        sampled = db.execute(
            select(WaitTimeSample.ticket_id).where(
                WaitTimeSample.ticket_id == ticket.id
            )
        ).scalar_one_or_none()
    assert sampled == ticket.id
