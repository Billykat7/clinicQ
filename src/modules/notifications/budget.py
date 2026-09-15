"""What SMS may cost: the kill switch, the daily caps, and what each clinic has spent (Issue 65).

SMS is the only transport that costs money per message, and on a student budget an unbounded send loop is
a real financial risk. So every SMS passes :func:`check_sms` just before it is handed to the gateway, after
consent and preferences, and it is refused when:

1. **the kill switch is on** (:func:`set_sms_kill_switch`, from ``PUT /notifications/sms/kill-switch``). It
   is a database row read on every send, so it stops the very next message: no deploy, no restart, and
   well inside a minute. It stops *every* SMS, sign-in codes included; that is what it is for.
2. **the clinic has sent its day's allowance** (``site.sms_daily_cap``, else ``SMS_SITE_DAILY_CAP``);
3. **this patient has been sent their day's allowance** (``SMS_PATIENT_DAILY_CAP``), which also caps what a
   loop stuck on one ticket can spend.

A refused message is recorded on the ledger as ``suppressed`` with the reason, and reaching a cap posts a
team alert **once** per clinic or patient per day (:class:`~src.database.models.sms_budget.SmsCapAlert`): a
cap never fails silently, and it never floods the channel either.

A day is the Johannesburg day. What counts against a cap is what the gateway accepted (``sent_at`` set),
because that is what it bills, including a message whose delivery later failed. On PostgreSQL the count and
the send for one clinic are serialised by a transaction-level advisory lock, so two workers cannot both
send the last message of the day.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import ColumnElement, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.commons.enums import NotificationChannel, PlatformSwitch, SmsBlockReason
from src.commons.time import business_date, business_day_bounds, now_sast
from src.core.config import Settings, get_settings
from src.core.team_alerts import post_team_alert
from src.database.models.notification import Notification
from src.database.models.site import Site
from src.database.models.sms_budget import PlatformSwitchState, SmsCapAlert

logger = logging.getLogger(__name__)

type Alert = Callable[[str], object]


@dataclass(frozen=True, slots=True)
class SmsBudget:
    """One clinic's SMS on one day: its cap, what it has sent, and what that cost."""

    site_id: str
    day: date
    cap: int
    sent: int
    spend: Decimal
    currency: str

    @property
    def remaining(self) -> int:
        """Messages left today; never below zero."""
        return max(self.cap - self.sent, 0)


# --- the kill switch --------------------------------------------------------------------------------------


def sms_kill_switch(db: Session) -> PlatformSwitchState | None:
    """The SMS kill switch's row, or ``None`` when nobody has ever flipped it (off)."""
    return db.get(PlatformSwitchState, PlatformSwitch.SMS_KILL.value)


def sms_stopped(db: Session) -> bool:
    """Whether the kill switch is on. Read from the database every time, never cached."""
    row = db.execute(
        select(PlatformSwitchState.enabled).where(
            PlatformSwitchState.key == PlatformSwitch.SMS_KILL.value
        )
    ).scalar_one_or_none()
    return bool(row)


def set_sms_kill_switch(
    db: Session,
    *,
    enabled: bool,
    reason: str | None,
    actor: str,
    now: datetime | None = None,
    alert: Alert | None = None,
) -> PlatformSwitchState:
    """Turn every SMS off (``enabled``) or back on, and tell the team. The caller commits."""
    moment = now or now_sast()
    row = sms_kill_switch(db)
    if row is None:
        row = PlatformSwitchState(key=PlatformSwitch.SMS_KILL.value)
        db.add(row)
    changed = bool(row.enabled) != enabled
    row.enabled = enabled
    row.reason = (reason or "").strip()[:200] or None
    row.changed_by = actor
    row.changed_at = moment
    db.flush()
    if changed:
        (alert or post_team_alert)(
            (
                "🛑 SMS kill switch ON: no SMS will be sent"
                if enabled
                else "✅ SMS kill switch OFF: SMS is sending again"
            )
            + f" (by {actor}{': ' + row.reason if row.reason else ''})."
        )
    return row


# --- caps and spend ---------------------------------------------------------------------------------------


def site_cap(db: Session, site_id: str, settings: Settings | None = None) -> int:
    """The clinic's daily SMS cap: its own, else ``SMS_SITE_DAILY_CAP``."""
    own = db.execute(
        select(Site.sms_daily_cap).where(Site.id == site_id)
    ).scalar_one_or_none()
    return own if own is not None else (settings or get_settings()).sms_site_daily_cap


def _sent_on(day: date) -> tuple[ColumnElement[bool], ...]:
    """The clauses for "an SMS the gateway accepted on ``day``"."""
    start, end = business_day_bounds(day)
    return (
        Notification.channel == NotificationChannel.SMS.value,
        Notification.sent_at >= start,
        Notification.sent_at < end,
    )


def sent_count(
    db: Session, *, day: date, site_id: str | None = None, patient_id: str | None = None
) -> int:
    """How many SMS the gateway accepted on ``day`` for a clinic, or for a patient."""
    clauses = list(_sent_on(day))
    if site_id is not None:
        clauses.append(Notification.site_id == site_id)
    if patient_id is not None:
        clauses.append(Notification.patient_id == patient_id)
    return int(
        db.execute(select(func.count(Notification.id)).where(*clauses)).scalar_one()
    )


def site_budget(
    db: Session,
    site_id: str,
    *,
    day: date | None = None,
    settings: Settings | None = None,
) -> SmsBudget:
    """A clinic's SMS cap, count and spend for a day: what the reports (Issue 90) read."""
    cfg = settings or get_settings()
    when = day or business_date()
    sent, spend = db.execute(
        select(
            func.count(Notification.id), func.coalesce(func.sum(Notification.cost), 0)
        ).where(*_sent_on(when), Notification.site_id == site_id)
    ).one()
    return SmsBudget(
        site_id=site_id,
        day=when,
        cap=site_cap(db, site_id, cfg),
        sent=int(sent),
        spend=Decimal(spend),
        currency=cfg.notification_cost_currency,
    )


def set_site_cap(db: Session, site_id: str, cap: int | None) -> None:
    """Give a clinic its own daily cap, or ``None`` for the default. The caller commits."""
    site = db.get(Site, site_id)
    if site is not None:
        site.sms_daily_cap = cap
        db.flush()


def _serialise_site(db: Session, site_id: str) -> None:
    """Hold this clinic's SMS budget until the transaction ends (PostgreSQL); a no-op elsewhere."""
    if db.get_bind().dialect.name != "postgresql":
        return
    key = int.from_bytes(
        hashlib.sha256(f"sms-budget:{site_id}".encode()).digest()[:8],
        "big",
        signed=True,
    )
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _alert_once(
    db: Session,
    reason: SmsBlockReason,
    subject_id: str,
    day: date,
    message: str,
    alert: Alert | None,
) -> None:
    """Post ``message`` unless this cap was already reported for this subject today."""
    try:
        with db.begin_nested():
            db.add(
                SmsCapAlert(reason=reason.value, subject_id=subject_id, service_day=day)
            )
            db.flush()
    except IntegrityError:
        return
    (alert or post_team_alert)(message)


def check_sms(
    db: Session,
    notification: Notification,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    alert: Alert | None = None,
) -> SmsBlockReason | None:
    """Why this SMS must not be sent now, or ``None`` when it may. Alerts the team when a cap is first hit.

    Called just before the gateway, with the ledger row the message belongs to. The deployment's
    ``SMS_ENABLED`` flag is checked first, then the operators' kill switch, then the caps.
    """
    cfg = settings or get_settings()
    if not cfg.sms_enabled:
        return SmsBlockReason.DISABLED
    if sms_stopped(db):
        return SmsBlockReason.KILL_SWITCH
    day = business_date(now or now_sast())
    if notification.site_id is not None:
        _serialise_site(db, notification.site_id)
        cap = site_cap(db, notification.site_id, cfg)
        if sent_count(db, day=day, site_id=notification.site_id) >= cap:
            name = db.execute(
                select(Site.name).where(Site.id == notification.site_id)
            ).scalar_one_or_none()
            _alert_once(
                db,
                SmsBlockReason.SITE_DAILY_CAP,
                notification.site_id,
                day,
                f"💸 SMS cap reached: {name or notification.site_id} has sent its {cap} SMS for "
                f"{day.isoformat()}. Further SMS to its patients are not sent today; web push still is "
                "(docs/CICD/RUNBOOK_ALERTS.md, “An SMS cap was reached”).",
                alert,
            )
            return SmsBlockReason.SITE_DAILY_CAP
    if (
        notification.patient_id is not None
        and sent_count(db, day=day, patient_id=notification.patient_id)
        >= cfg.sms_patient_daily_cap
    ):
        _alert_once(
            db,
            SmsBlockReason.PATIENT_DAILY_CAP,
            notification.patient_id,
            day,
            f"💸 SMS cap reached for one patient ({notification.patient_id}): "
            f"{cfg.sms_patient_daily_cap} SMS on {day.isoformat()}. A loop is the usual cause "
            "(docs/CICD/RUNBOOK_ALERTS.md, “An SMS cap was reached”).",
            alert,
        )
        return SmsBlockReason.PATIENT_DAILY_CAP
    return None
