"""How well each transport is delivering, and the alert when one is failing (Issue 71).

A message that is never sent looks exactly like a message nobody replied to, so failure has to be counted
and shown rather than found by accident. Everything here is read from the delivery ledger
(``notification``), the one record of every message:

* :func:`channel_stats` counts one window's messages per transport: sent, delivered, dead-lettered,
  suppressed and still waiting, with what they cost. It feeds ``GET /notifications/delivery-stats`` and the
  panel on the operators' notifications page.
* :func:`failure_rate` is what the alert measures: of the messages that reached an outcome with a provider
  (sent, delivered or dead), the share that died. A suppressed message was never attempted (no consent, an
  opt-out, a cap) and a waiting one has no outcome yet, so neither counts either way.
* :func:`watch_failures` runs on the scheduler. When a transport has at least
  ``NOTIFICATION_FAILURE_ALERT_MIN_ATTEMPTS`` attempted messages in the last
  ``NOTIFICATION_FAILURE_ALERT_WINDOW_MINUTES`` and their failure rate is at or over
  ``NOTIFICATION_FAILURE_ALERT_RATE``, the team is told, once per transport per window
  (:class:`~src.database.models.notification_failure_alert.NotificationFailureAlert`).

Platform-wide on purpose: a gateway outage fails every clinic's messages at once, and is one incident.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.commons.enums import NotificationChannel, NotificationStatus
from src.commons.time import now_sast, stored_sast
from src.core.config import Settings, get_settings
from src.core.team_alerts import post_team_alert
from src.database.models.notification import Notification
from src.database.models.notification_failure_alert import NotificationFailureAlert

logger = logging.getLogger(__name__)

Alert = Callable[[str], object]


@dataclass(frozen=True, slots=True)
class ChannelStats:
    """One transport's messages created in a window, by where each one stands now."""

    channel: NotificationChannel
    sent: int = 0
    """Accepted by the provider and not (yet) confirmed: ``sent``."""
    delivered: int = 0
    """Confirmed by a delivery receipt: ``delivered``."""
    dead: int = 0
    """Dead-lettered: every attempt failed, or the address can never work."""
    suppressed: int = 0
    """Deliberately not sent: no consent, an opt-out, a cap, the kill switch, or no address."""
    waiting: int = 0
    """Queued or failed and due another try: no outcome yet."""
    cost: Decimal = Decimal("0")
    """What the provider charged for this window's messages."""

    @property
    def total(self) -> int:
        """Every message in the window."""
        return self.sent + self.delivered + self.dead + self.suppressed + self.waiting

    @property
    def attempted(self) -> int:
        """Messages that reached an outcome with a provider."""
        return self.sent + self.delivered + self.dead


def failure_rate(stats: ChannelStats) -> float | None:
    """``dead / attempted``, or ``None`` when nothing was attempted."""
    return stats.dead / stats.attempted if stats.attempted else None


def channel_stats(
    db: Session, *, since: datetime, until: datetime
) -> list[ChannelStats]:
    """Every transport's messages created in ``[since, until)``, in channel order, including idle ones."""
    rows = db.execute(
        select(
            Notification.channel,
            Notification.status,
            func.count(Notification.id),
            func.coalesce(func.sum(Notification.cost), 0),
        )
        .where(Notification.created_at >= since, Notification.created_at < until)
        .group_by(Notification.channel, Notification.status)
    ).all()
    counts: dict[str, dict[str, int]] = {}
    costs: dict[str, Decimal] = {}
    for channel, status, count, cost in rows:
        counts.setdefault(channel, {})[status] = int(count)
        costs[channel] = costs.get(channel, Decimal("0")) + Decimal(str(cost))
    out = []
    for channel in NotificationChannel:
        by_status = counts.get(channel.value, {})
        out.append(
            ChannelStats(
                channel=channel,
                sent=by_status.get(NotificationStatus.SENT.value, 0),
                delivered=by_status.get(NotificationStatus.DELIVERED.value, 0),
                dead=by_status.get(NotificationStatus.DEAD.value, 0),
                suppressed=by_status.get(NotificationStatus.SUPPRESSED.value, 0),
                waiting=by_status.get(NotificationStatus.QUEUED.value, 0)
                + by_status.get(NotificationStatus.FAILED.value, 0),
                cost=costs.get(channel.value, Decimal("0")),
            )
        )
    return out


def is_alerting(stats: ChannelStats, settings: Settings) -> bool:
    """Whether ``stats`` crosses the alert threshold: enough attempts, and a failure rate at or over it."""
    rate = failure_rate(stats)
    return (
        rate is not None
        and stats.attempted >= settings.notification_failure_alert_min_attempts
        and rate >= settings.notification_failure_alert_rate
    )


def window_start(moment: datetime, minutes: int) -> datetime:
    """The start of the fixed alert window ``moment`` falls in: windows begin at midnight, ``minutes`` long."""
    local = stored_sast(moment)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = int((local - midnight).total_seconds() // 60)
    return midnight + timedelta(minutes=elapsed - elapsed % minutes)


def watch_failures(
    db: Session,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    alert: Alert | None = None,
) -> list[NotificationChannel]:
    """Alert the team about every transport failing over the threshold, once per window; the ones alerted.

    Measures the last window's messages (a rolling window ending now), and records the alert against the
    fixed window ``now`` falls in, so a sustained outage alerts once per window rather than on every run.
    The caller commits.
    """
    cfg = settings or get_settings()
    moment = now or now_sast()
    minutes = cfg.notification_failure_alert_window_minutes
    since = moment - timedelta(minutes=minutes)
    alerted: list[NotificationChannel] = []
    for stats in channel_stats(db, since=since, until=moment):
        if not is_alerting(stats, cfg):
            continue
        rate = failure_rate(stats) or 0.0
        try:
            with db.begin_nested():
                db.add(
                    NotificationFailureAlert(
                        channel=stats.channel.value,
                        window_start=window_start(moment, minutes),
                        attempted=stats.attempted,
                        failed=stats.dead,
                        failure_rate=Decimal(f"{rate:.4f}"),
                    )
                )
        except IntegrityError:
            continue  # already announced in this window
        (alert or post_team_alert)(
            f"📉 Notifications failing: {stats.dead} of {stats.attempted} {stats.channel.value} messages "
            f"dead-lettered in the last {minutes} minutes ({rate:.0%}, the threshold is "
            f"{cfg.notification_failure_alert_rate:.0%}). Check the provider, then the ledger at "
            '/admin/notifications. Runbook: docs/CICD/RUNBOOK_ALERTS.md, "Notifications are failing".'
        )
        logger.error(
            "Notification failure rate for %s is %.2f over %d attempts.",
            stats.channel.value,
            rate,
            stats.attempted,
        )
        alerted.append(stats.channel)
    return alerted
