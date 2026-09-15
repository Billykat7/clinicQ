"""Delivery rate and cost per transport, and the alert when a transport's messages are failing (Issue 71).

Against a ledger with known rows:

* **the dashboard's figures are the ledger's**: per transport, sent, delivered, failed (dead), not sent
  (suppressed) and waiting, the failure rate over what was attempted, and what it cost; operators only;
* **the alert fires when the failure rate crosses the threshold**, with enough attempts behind it, once per
  transport per window however often the watch runs, and again in the next window if it is still failing;
* **a small number of failures does not page anyone**: below the minimum attempts, no alert;
* **the watch is on the scheduler**, registered once.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette import status

from src.commons.enums import (
    NotificationChannel,
    NotificationStatus,
    NotificationTemplate,
    UserRole,
)
from src.commons.time import now_sast
from src.core import scheduler
from src.core.config import Settings
from src.database.models import NotificationFailureAlert
from src.modules.notifications import delivery_stats, service
from tests.factories import FACTORY_STAFF_PASSWORD, StaffFactory
from tests.integration.queue.conftest import queue_settings


def _rows(
    db: Session,
    channel: NotificationChannel,
    status_: NotificationStatus,
    count: int,
    *,
    at: datetime,
    cost: str | None = None,
) -> None:
    for _ in range(count):
        row = service.enqueue(
            db,
            channel=channel,
            template=NotificationTemplate.TICKET_CALLED,
            recipient="+27820000001",
            subject=None,
            payload={},
            max_attempts=1,
        )
        row.status = status_.value
        row.created_at = at
        if cost is not None:
            row.cost, row.cost_currency = Decimal(cost), "ZAR"
    db.flush()


def _operator(desk: SimpleNamespace) -> TestClient:
    with desk.session() as db:
        StaffFactory.create(db, email="ops@clinicq.example", role=UserRole.ADMIN)
        db.commit()
    client = TestClient(desk.app)
    client.post(
        "/api/v1/auth/password/login",
        json={"email": "ops@clinicq.example", "password": FACTORY_STAFF_PASSWORD},
    )
    return client


def test_the_dashboard_figures_are_the_ledgers_per_transport(
    desk: SimpleNamespace,
) -> None:
    moment = now_sast() - timedelta(minutes=30)
    with desk.session() as db:
        _rows(
            db,
            NotificationChannel.SMS,
            NotificationStatus.SENT,
            6,
            at=moment,
            cost="0.2500",
        )
        _rows(
            db,
            NotificationChannel.SMS,
            NotificationStatus.DELIVERED,
            2,
            at=moment,
            cost="0.2500",
        )
        _rows(db, NotificationChannel.SMS, NotificationStatus.DEAD, 2, at=moment)
        _rows(db, NotificationChannel.SMS, NotificationStatus.SUPPRESSED, 3, at=moment)
        _rows(db, NotificationChannel.SMS, NotificationStatus.FAILED, 1, at=moment)
        _rows(db, NotificationChannel.WEB_PUSH, NotificationStatus.SENT, 4, at=moment)
        # Two days old: outside a 24-hour window, inside a week.
        _rows(
            db,
            NotificationChannel.SMS,
            NotificationStatus.DEAD,
            5,
            at=moment - timedelta(days=2),
        )
        db.commit()

    operator = _operator(desk)
    day = operator.get("/api/v1/notifications/delivery-stats")
    assert day.status_code == status.HTTP_200_OK, day.text
    channels = {row["channel"]: row for row in day.json()["channels"]}
    assert set(channels) == {channel.value for channel in NotificationChannel}
    assert channels["sms"] == {
        "channel": "sms",
        "total": 14,
        "sent": 6,
        "delivered": 2,
        "failed": 2,
        "suppressed": 3,
        "waiting": 1,
        "failure_rate": 0.2,
        "cost": "2.0000",
        "alerting": False,
    }
    assert (
        channels["web_push"]["failure_rate"] == 0.0
        and channels["whatsapp"]["failure_rate"] is None
    )
    assert day.json()["currency"] == "ZAR" and day.json()["alert_rate"] == 0.25

    week = operator.get(
        "/api/v1/notifications/delivery-stats", params={"hours": 168}
    ).json()
    sms_week = next(row for row in week["channels"] if row["channel"] == "sms")
    assert sms_week["failed"] == 7 and sms_week["failure_rate"] == pytest.approx(7 / 15)

    assert (
        operator.get(
            "/api/v1/notifications/delivery-stats", params={"hours": 0}
        ).status_code
        == 422
    )
    assert (
        desk.staff("desk.a").get("/api/v1/notifications/delivery-stats").status_code
        == 403
    )
    assert (
        TestClient(desk.app).get("/api/v1/notifications/delivery-stats").status_code
        == 401
    )


@pytest.fixture
def watching() -> Settings:
    return queue_settings(
        notification_failure_alert_rate=0.25,
        notification_failure_alert_min_attempts=20,
        notification_failure_alert_window_minutes=60,
    )


def test_the_alert_fires_over_the_threshold_once_per_window_and_again_in_the_next(
    desk: SimpleNamespace, watching: Settings
) -> None:
    """Criterion: an alert fires when the delivery failure rate crosses a threshold."""
    posted: list[str] = []
    noon = now_sast().replace(hour=12, minute=10, second=0, microsecond=0)
    with desk.session() as db:
        _rows(
            db,
            NotificationChannel.SMS,
            NotificationStatus.SENT,
            20,
            at=noon - timedelta(minutes=20),
        )
        _rows(
            db,
            NotificationChannel.SMS,
            NotificationStatus.DEAD,
            10,
            at=noon - timedelta(minutes=10),
        )
        # Two of three push messages failed: a high rate, but too few attempts to mean anything yet.
        _rows(
            db,
            NotificationChannel.WEB_PUSH,
            NotificationStatus.DEAD,
            2,
            at=noon - timedelta(minutes=5),
        )
        _rows(
            db,
            NotificationChannel.WEB_PUSH,
            NotificationStatus.SENT,
            1,
            at=noon - timedelta(minutes=5),
        )
        db.commit()

        first = delivery_stats.watch_failures(
            db, now=noon, settings=watching, alert=posted.append
        )
        again = delivery_stats.watch_failures(
            db, now=noon + timedelta(minutes=5), settings=watching, alert=posted.append
        )
        db.commit()
        assert (first, again) == ([NotificationChannel.SMS], [])
        assert posted == [
            "📉 Notifications failing: 10 of 30 sms messages dead-lettered in the last 60 minutes (33%, the "
            "threshold is 25%). Check the provider, then the ledger at /admin/notifications. Runbook: "
            'docs/CICD/RUNBOOK_ALERTS.md, "Notifications are failing".'
        ]
        (recorded,) = db.scalars(select(NotificationFailureAlert)).all()
        assert (
            recorded.channel,
            recorded.attempted,
            recorded.failed,
            recorded.failure_rate,
        ) == (
            "sms",
            30,
            10,
            Decimal("0.3333"),
        )

        # Still failing an hour later: a new window, a new alert.
        _rows(
            db,
            NotificationChannel.SMS,
            NotificationStatus.DEAD,
            25,
            at=noon + timedelta(minutes=50),
        )
        db.commit()
        later = delivery_stats.watch_failures(
            db, now=noon + timedelta(minutes=65), settings=watching, alert=posted.append
        )
        db.commit()
    assert later == [NotificationChannel.SMS] and len(posted) == 2


def test_failures_below_the_threshold_or_the_minimum_attempts_page_nobody(
    desk: SimpleNamespace, watching: Settings
) -> None:
    posted: list[str] = []
    moment = now_sast()
    with desk.session() as db:
        _rows(
            db,
            NotificationChannel.SMS,
            NotificationStatus.SENT,
            80,
            at=moment - timedelta(minutes=5),
        )
        _rows(
            db,
            NotificationChannel.SMS,
            NotificationStatus.DEAD,
            19,
            at=moment - timedelta(minutes=5),
        )
        _rows(
            db,
            NotificationChannel.WHATSAPP,
            NotificationStatus.DEAD,
            19,
            at=moment - timedelta(minutes=5),
        )
        _rows(
            db,
            NotificationChannel.EMAIL,
            NotificationStatus.SUPPRESSED,
            50,
            at=moment - timedelta(minutes=5),
        )
        db.commit()
        assert (
            delivery_stats.watch_failures(
                db, now=moment, settings=watching, alert=posted.append
            )
            == []
        )
    assert posted == []


def test_the_watch_is_registered_once_on_the_scheduler() -> None:
    settings = Settings(_env_file=None, scheduler_enabled=True)  # type: ignore[call-arg]
    started = scheduler.start_scheduler(settings)
    try:
        assert started is not None
        jobs = [
            job for job in started.get_jobs() if job.id == "notification_failure_watch"
        ]
        assert (
            len(jobs) == 1 and jobs[0].func is scheduler.run_notification_failure_watch
        )
    finally:
        scheduler.shutdown_scheduler()
