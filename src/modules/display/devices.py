"""The kiosk device registry: pairing a box to a clinic, knowing it is alive, and removing it (Issue 61).

**Pairing, with no address typed at the clinic.** Every box is provisioned with the same start address,
``/display`` (``docs/OPS/KIOSK_SETUP.md``). The first time a box opens it, :func:`start_device` makes an
unpaired row, gives the box a long random secret in an httpOnly cookie and puts a short code on its
screen. A clinic manager types that code into the dashboard (:func:`pair_device`), which binds the row
to their clinic; the box, polling, sees it is paired and opens its board. The code is drawn from the
ticket reference alphabet (no ``0``/``O``, no ``1``/``I``/``L``), so it survives being read off a TV
across a room.

**The secret is never stored.** Like a refresh token (:func:`~src.core.security.hash_refresh_token`),
only its SHA-256 digest is kept, and it is looked up by that digest. A board's address is
``/display/{site_id}``, but opening it anywhere without a paired box's cookie (or a signed-in staff
member of that clinic) shows nothing: the address is not a credential and cannot be shared.

**Removing a box** sets ``revoked_at``. A revoked box's cookie is refused by every board route at once,
its open stream ends at its next access check, and its heartbeat tells it to go back to the pairing
screen.

**Knowing it is alive.** The board page reports every minute (:func:`record_heartbeat`).
:func:`watch_devices`, run every minute by the scheduler, alerts the team once when a paired box has
been silent for ``DISPLAY_DEVICE_SILENT_MINUTES``, naming the box and its clinic, and once more when it
is heard from again (:mod:`src.core.team_alerts`, Issue 14's channel).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import DisplayDeviceStatus
from src.commons.time import now_sast, stored_sast, to_sast
from src.core.config import Settings, get_settings
from src.core.security import hash_refresh_token
from src.core.site_scope import (
    SiteAccess,
    get_in_site_or_404,
    scoped_select,
    select_in_scope,
)
from src.core.team_alerts import post_team_alert
from src.database.models import DisplayDevice, Queue, Site
from src.modules.queue.sequence import REFERENCE_ALPHABET

#: How many characters a pairing code has: 31**6, about 887 million, and it lives ten minutes.
PAIRING_CODE_LENGTH: Final = 6
#: Bytes of randomness in a box's secret (43 URL-safe characters).
DEVICE_SECRET_BYTES: Final = 32
#: How long an unpaired box's row is kept before the sweep removes it.
UNPAIRED_KEPT_FOR: Final = timedelta(days=1)
#: The longest label a manager may give a box.
MAX_LABEL_LENGTH: Final = 80
#: Characters a person may type between the two halves of a code.
_SEPARATORS: Final = frozenset("- ")


class PairingCodeNotFoundError(LookupError):
    """No box is waiting to be paired with that code: wrong, expired, or already used.

    One error for all three, so the answer does not help anyone guess codes.
    """


class UnknownQueueError(ValueError):
    """A queue chosen for a box is not one of this clinic's queues."""


@dataclass(frozen=True, slots=True)
class StartedDevice:
    """A box just given its row: the row, the secret for its cookie, and the code for its screen."""

    device: DisplayDevice
    secret: str
    code: str


def format_code(code: str) -> str:
    """``K7M4PQ`` → ``K7M 4PQ``: two halves, easier to read across a room and to type."""
    half = PAIRING_CODE_LENGTH // 2
    return f"{code[:half]} {code[half:]}"


def normalise_code(raw: str) -> str | None:
    """The code a manager typed, as stored, or ``None`` when it cannot be one.

    Spaces and dashes are ignored and lower case is accepted, because a person copies what they see.
    """
    code = "".join(c for c in raw.strip().upper() if c not in _SEPARATORS)
    if len(code) != PAIRING_CODE_LENGTH or any(
        c not in REFERENCE_ALPHABET for c in code
    ):
        return None
    return code


def _new_code() -> str:
    return "".join(
        secrets.choice(REFERENCE_ALPHABET) for _ in range(PAIRING_CODE_LENGTH)
    )


def _code_expiry(moment: datetime, settings: Settings) -> datetime:
    return moment + timedelta(minutes=settings.display_pairing_code_minutes)


def start_device(
    db: Session,
    *,
    user_agent: str | None,
    moment: datetime | None = None,
    settings: Settings | None = None,
) -> StartedDevice:
    """Create an unpaired box with a new secret and pairing code. The caller commits."""
    cfg = settings or get_settings()
    now = moment or now_sast()
    secret = secrets.token_urlsafe(DEVICE_SECRET_BYTES)
    code = _new_code()
    device = DisplayDevice(
        token_hash=hash_refresh_token(secret),
        pairing_code_hash=hash_refresh_token(code),
        pairing_expires_at=_code_expiry(now, cfg),
        user_agent=(user_agent or "")[:200] or None,
    )
    db.add(device)
    db.flush()
    return StartedDevice(device=device, secret=secret, code=code)


def renew_code(
    db: Session,
    device: DisplayDevice,
    *,
    moment: datetime | None = None,
    settings: Settings | None = None,
) -> str:
    """Give an unpaired box a fresh code (its old one expired, or its screen was reloaded). The caller commits."""
    cfg = settings or get_settings()
    code = _new_code()
    device.pairing_code_hash = hash_refresh_token(code)
    device.pairing_expires_at = _code_expiry(moment or now_sast(), cfg)
    db.flush()
    return code


def device_for_secret(db: Session, secret: str | None) -> DisplayDevice | None:
    """The box whose cookie holds ``secret``, in whatever state it is in, or ``None``."""
    if not secret:
        return None
    return db.execute(
        select(DisplayDevice).where(
            DisplayDevice.token_hash == hash_refresh_token(secret)
        )
    ).scalar_one_or_none()


def is_paired(device: DisplayDevice | None) -> bool:
    """Whether ``device`` may show a board: paired to a clinic and not revoked."""
    return bool(
        device and device.site_id and device.paired_at and not device.revoked_at
    )


def status_of(
    device: DisplayDevice,
    *,
    moment: datetime | None = None,
    settings: Settings | None = None,
) -> DisplayDeviceStatus:
    """What a manager and an operator are told about a box right now."""
    if device.revoked_at is not None:
        return DisplayDeviceStatus.REVOKED
    if not is_paired(device):
        return DisplayDeviceStatus.PAIRING
    cfg = settings or get_settings()
    now = moment or now_sast()
    last = stored_sast(device.last_seen_at or device.paired_at)  # type: ignore[arg-type]
    if now - last > timedelta(minutes=cfg.display_device_silent_minutes):
        return DisplayDeviceStatus.SILENT
    return DisplayDeviceStatus.ONLINE


def _pending_by_code(db: Session, code: str, moment: datetime) -> DisplayDevice | None:
    """The unpaired box showing ``code`` now. Unpaired boxes belong to no clinic yet, so no site filter."""
    device = db.execute(
        select(DisplayDevice).where(
            DisplayDevice.pairing_code_hash == hash_refresh_token(code),
            DisplayDevice.site_id.is_(None),
            DisplayDevice.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if device is None or device.pairing_expires_at is None:
        return None
    if stored_sast(device.pairing_expires_at) <= moment:
        return None
    return device


def _clinic_queue_ids(
    db: Session, access: SiteAccess, queue_ids: Collection[str] | None
) -> list[str] | None:
    """``queue_ids`` checked against this clinic's queues, in its order; ``None`` means every queue."""
    if not queue_ids:
        return None
    wanted = set(queue_ids)
    mine = [
        queue.id
        for queue in db.execute(
            scoped_select(Queue, access)
            .where(Queue.is_deleted.is_(False))
            .order_by(Queue.display_order, Queue.name)
        ).scalars()
    ]
    if not wanted <= set(mine):
        raise UnknownQueueError(sorted(wanted - set(mine)))
    return [queue_id for queue_id in mine if queue_id in wanted]


def pair_device(
    db: Session,
    access: SiteAccess,
    *,
    code: str,
    label: str | None,
    queue_ids: Collection[str] | None = None,
    moment: datetime | None = None,
) -> DisplayDevice:
    """Bind the box showing ``code`` to the caller's clinic. The caller audits and commits.

    Raises:
        PairingCodeNotFoundError: No box is waiting with that code.
        UnknownQueueError: A chosen queue is not this clinic's.
    """
    now = moment or now_sast()
    normalised = normalise_code(code)
    device = _pending_by_code(db, normalised, now) if normalised else None
    if device is None:
        raise PairingCodeNotFoundError
    device.queue_ids = _clinic_queue_ids(db, access, queue_ids)
    device.site_id = access.site_id
    device.label = (label or "").strip()[:MAX_LABEL_LENGTH] or None
    device.paired_at = now
    device.paired_by = str(access.user.id)
    device.pairing_code_hash = None
    device.pairing_expires_at = None
    device.last_seen_at = now
    db.flush()
    return device


def devices_at(db: Session, access: SiteAccess) -> Sequence[DisplayDevice]:
    """A clinic's boxes, the ones still in use first, newest pairing first."""
    rows = db.execute(
        scoped_select(DisplayDevice, access).order_by(DisplayDevice.paired_at.desc())
    ).scalars()
    return sorted(rows, key=lambda d: d.revoked_at is not None)


def device_at(db: Session, access: SiteAccess, device_id: str) -> DisplayDevice:
    """One of the clinic's boxes by id, or the site guard's 404."""
    return get_in_site_or_404(db, DisplayDevice, device_id, access)


def update_device(
    db: Session,
    access: SiteAccess,
    device: DisplayDevice,
    *,
    label: str | None,
    queue_ids: Collection[str] | None,
) -> list[str]:
    """Rename a box or change its queues; what changed, for the audit row. The caller commits."""
    changed: list[str] = []
    new_label = (label or "").strip()[:MAX_LABEL_LENGTH] or None
    new_queues = _clinic_queue_ids(db, access, queue_ids)
    if new_label != device.label:
        changed.append(f"label: {device.label or '—'} -> {new_label or '—'}")
        device.label = new_label
    if new_queues != device.queue_ids:
        changed.append(
            f"queues: {len(device.queue_ids or [])} -> {len(new_queues or [])} (0 means all)"
        )
        device.queue_ids = new_queues
    db.flush()
    return changed


def revoke_device(
    db: Session,
    access: SiteAccess,
    device: DisplayDevice,
    *,
    moment: datetime | None = None,
) -> bool:
    """Remove a box from the clinic; ``False`` when it was already removed. The caller audits and commits."""
    if device.revoked_at is not None:
        return False
    device.revoked_at = moment or now_sast()
    device.revoked_by = str(access.user.id)
    db.flush()
    return True


def record_heartbeat(
    db: Session,
    device: DisplayDevice,
    *,
    app_version: str | None,
    user_agent: str | None,
    moment: datetime | None = None,
) -> None:
    """A paired box has reported in. The caller commits."""
    device.last_seen_at = moment or now_sast()
    if app_version:
        device.app_version = app_version[:40]
    if user_agent:
        device.user_agent = user_agent[:200]
    db.flush()


def _watched(db: Session) -> Sequence[DisplayDevice]:
    """Every paired, unrevoked box on the platform. The watch is a system job with no clinic to scope by."""
    return (
        db.execute(
            select(DisplayDevice).where(
                DisplayDevice.site_id.is_not(None),
                DisplayDevice.paired_at.is_not(None),
                DisplayDevice.revoked_at.is_(None),
            )
        )
        .scalars()
        .all()
    )


@dataclass(frozen=True, slots=True)
class WatchResult:
    """What one watch did: boxes newly reported silent, and boxes reported back."""

    silent: tuple[str, ...]
    back: tuple[str, ...]


def _describe(db: Session, device: DisplayDevice) -> str:
    site = db.get(Site, device.site_id) if device.site_id else None
    name = f"“{device.label}”" if device.label else "An unnamed board"
    return f"{name} at {site.name if site else 'an unknown clinic'}"


def watch_devices(
    db: Session,
    *,
    moment: datetime | None = None,
    settings: Settings | None = None,
    alert: Callable[[str], object] = post_team_alert,
) -> WatchResult:
    """Alert once for each paired box silent past the threshold, and once when each is back. The caller commits.

    ``silent_alerted_at`` makes an alert once per silence: set when the alert is sent, cleared (with the
    "back" message) the first watch after the box is heard from again.
    """
    cfg = settings or get_settings()
    now = moment or now_sast()
    threshold = timedelta(minutes=cfg.display_device_silent_minutes)
    silent: list[str] = []
    back: list[str] = []
    for device in _watched(db):
        last = stored_sast(device.last_seen_at or device.paired_at)  # type: ignore[arg-type]
        quiet = now - last > threshold
        if quiet and device.silent_alerted_at is None:
            minutes = int((now - last).total_seconds() // 60)
            alert(
                f"📺 Waiting-room board silent: {_describe(db, device)} has not been heard from since "
                f"{to_sast(last).strftime('%H:%M')} ({minutes} min). Its screen may be dark: check its "
                "power and network (docs/CICD/RUNBOOK_ALERTS.md, “A waiting-room board is silent”)."
            )
            device.silent_alerted_at = now
            silent.append(device.id)
        elif not quiet and device.silent_alerted_at is not None:
            alert(
                f"✅ Waiting-room board back: {_describe(db, device)} is reporting again."
            )
            device.silent_alerted_at = None
            back.append(device.id)
    db.flush()
    return WatchResult(silent=tuple(silent), back=tuple(back))


def purge_unpaired(db: Session, *, moment: datetime | None = None) -> int:
    """Delete boxes that were never paired and whose last code ran out a day ago; how many. The caller commits.

    Judged by the code's expiry, which the start page renews every time the box shows a new code, so a
    box still sitting on its pairing screen is never removed from under it.
    """
    cutoff = (moment or now_sast()) - UNPAIRED_KEPT_FOR
    stale = [
        device
        for device in db.execute(
            select(DisplayDevice).where(
                DisplayDevice.site_id.is_(None), DisplayDevice.paired_at.is_(None)
            )
        ).scalars()
        if device.pairing_expires_at is None
        or stored_sast(device.pairing_expires_at) < cutoff
    ]
    for device in stale:
        db.delete(device)
    db.flush()
    return len(stale)


def platform_devices(db: Session) -> Sequence[tuple[DisplayDevice, Site]]:
    """Every paired box on the platform with its clinic, for the operator's console."""
    rows = db.execute(
        select_in_scope(DisplayDevice, None)
        .join(Site, Site.id == DisplayDevice.site_id)
        .add_columns(Site)
        .order_by(Site.name, DisplayDevice.paired_at.desc())
    ).all()
    return [(device, site) for device, site in rows]
