"""The kiosk device registry's API (Issue 61): a clinic's boards, and the operator's view of every board.

* ``GET /sites/{site_id}/display-devices``: the clinic's boxes, with their status.
* ``POST /sites/{site_id}/display-devices``: pair the box showing a code (``pairing_code``) with this
  clinic, with a label and, optionally, the queues it shows.
* ``PATCH /sites/{site_id}/display-devices/{device_id}``: rename a box or change its queues.
* ``POST /sites/{site_id}/display-devices/{device_id}/revoke``: remove a box; it stops showing the board.
* ``GET /display-devices``: every paired box on the platform, for the operator.

Pairing, changing and removing a box change what a public screen shows, so they need the clinic's
``sites.display`` grant at ``update``, the clinic manager's, the grant that already governs the board's
display settings (Issue 27). Reading needs ``read``, which the front desk has. Each change writes an
audit row. The operator's list needs the ``business``-tier grant on ``sites``, like the verification
console (Issue 29), and reads across clinics on purpose.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.api.rbac_deps import DbSession, require
from src.commons.enums import (
    AuditAction,
    AuditEntityType,
    DisplayDeviceStatus,
    GrantScope,
)
from src.core.audit import record_audit_event
from src.core.client_ip import resolve_client_ip
from src.core.site_scope import SiteAccess, require_site_access
from src.database.models import DisplayDevice
from src.modules.display import devices
from src.modules.display.devices import MAX_LABEL_LENGTH

router = APIRouter(tags=["display"])

DisplayRead = Annotated[
    SiteAccess, Depends(require_site_access("sites.display", "read"))
]
DisplayUpdate = Annotated[
    SiteAccess, Depends(require_site_access("sites.display", "update"))
]
PlatformDirectory = Annotated[
    None, Depends(require("sites", "read", scope=GrantScope.BUSINESS))
]

#: Said for a wrong, expired or used code alike, so the answer does not help anyone guess.
CODE_NOT_FOUND = "No screen is waiting with that code. Check the code on the screen; if it has changed, type the new one."


class DisplayDevicePairIn(BaseModel):
    """What a manager types to pair a screen."""

    pairing_code: str = Field(min_length=6, max_length=12)
    label: str | None = Field(default=None, max_length=MAX_LABEL_LENGTH)
    #: The queues this screen shows; omitted or empty for all of the clinic's open queues.
    queue_ids: list[str] | None = None


class DisplayDeviceUpdateIn(BaseModel):
    """A screen's label and queues."""

    label: str | None = Field(default=None, max_length=MAX_LABEL_LENGTH)
    queue_ids: list[str] | None = None


class DisplayDeviceOut(BaseModel):
    """One screen, as a manager or an operator sees it. Never its secret or its code."""

    id: str
    site_id: str | None
    label: str | None
    queue_ids: list[str] | None
    status: DisplayDeviceStatus
    paired_at: datetime | None
    last_seen_at: datetime | None
    revoked_at: datetime | None
    app_version: str | None
    user_agent: str | None


class DisplayDeviceListOut(BaseModel):
    """A clinic's screens."""

    site_id: str
    total: int
    items: list[DisplayDeviceOut]


class PlatformDisplayDeviceOut(DisplayDeviceOut):
    """One screen on the operator's list, with its clinic's name."""

    site_name: str


class PlatformDisplayDeviceListOut(BaseModel):
    """Every paired screen on the platform."""

    total: int
    silent: int
    items: list[PlatformDisplayDeviceOut]


def device_out(device: DisplayDevice) -> DisplayDeviceOut:
    """A screen's public fields and its status now."""
    return DisplayDeviceOut(
        id=device.id,
        site_id=device.site_id,
        label=device.label,
        queue_ids=device.queue_ids,
        status=devices.status_of(device),
        paired_at=device.paired_at,
        last_seen_at=device.last_seen_at,
        revoked_at=device.revoked_at,
        app_version=device.app_version,
        user_agent=device.user_agent,
    )


def _audit(
    db: DbSession,
    request: Request,
    access: SiteAccess,
    device: DisplayDevice,
    what: str,
) -> None:
    record_audit_event(
        db,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.DISPLAY_DEVICE,
        entity_id=device.id,
        actor=access.user.email,
        actor_id=str(access.user.id),
        ip_address=resolve_client_ip(request),
        site_id=access.site_id,
        context=what,
    )


@router.get(
    "/sites/{site_id}/display-devices",
    response_model=DisplayDeviceListOut,
    operation_id="displayListDevices",
)
def list_devices(access: DisplayRead, db: DbSession) -> DisplayDeviceListOut:
    """This clinic's screens, the ones in use first."""
    rows = devices.devices_at(db, access)
    return DisplayDeviceListOut(
        site_id=access.site_id, total=len(rows), items=[device_out(d) for d in rows]
    )


@router.post(
    "/sites/{site_id}/display-devices",
    response_model=DisplayDeviceOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="displayPairDevice",
)
def pair(
    payload: DisplayDevicePairIn, request: Request, access: DisplayUpdate, db: DbSession
) -> DisplayDeviceOut:
    """Pair the screen showing ``pairing_code`` with this clinic. It opens the board by itself."""
    try:
        device = devices.pair_device(
            db,
            access,
            code=payload.pairing_code,
            label=payload.label,
            queue_ids=payload.queue_ids,
        )
    except devices.PairingCodeNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, CODE_NOT_FOUND) from exc
    except devices.UnknownQueueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "A chosen queue is not one of this clinic's.",
        ) from exc
    _audit(
        db,
        request,
        access,
        device,
        f"display board paired: {device.label or 'unnamed'}",
    )
    db.commit()
    return device_out(device)


@router.patch(
    "/sites/{site_id}/display-devices/{device_id}",
    response_model=DisplayDeviceOut,
    operation_id="displayUpdateDevice",
)
def update(
    device_id: str,
    payload: DisplayDeviceUpdateIn,
    request: Request,
    access: DisplayUpdate,
    db: DbSession,
) -> DisplayDeviceOut:
    """Rename a screen or change the queues it shows."""
    device = devices.device_at(db, access, device_id)
    try:
        changed = devices.update_device(
            db, access, device, label=payload.label, queue_ids=payload.queue_ids
        )
    except devices.UnknownQueueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "A chosen queue is not one of this clinic's.",
        ) from exc
    if changed:
        _audit(
            db, request, access, device, "display board changed: " + "; ".join(changed)
        )
    db.commit()
    return device_out(device)


@router.post(
    "/sites/{site_id}/display-devices/{device_id}/revoke",
    response_model=DisplayDeviceOut,
    operation_id="displayRevokeDevice",
)
def revoke(
    device_id: str, request: Request, access: DisplayUpdate, db: DbSession
) -> DisplayDeviceOut:
    """Remove a screen from the clinic. It stops showing the board and goes back to pairing."""
    device = devices.device_at(db, access, device_id)
    if devices.revoke_device(db, access, device):
        _audit(
            db,
            request,
            access,
            device,
            f"display board removed: {device.label or 'unnamed'}",
        )
    db.commit()
    return device_out(device)


@router.get(
    "/display-devices",
    response_model=PlatformDisplayDeviceListOut,
    operation_id="displayPlatformDevices",
)
def platform_devices(
    _authz: PlatformDirectory, db: DbSession
) -> PlatformDisplayDeviceListOut:
    """Every paired screen on the platform with its clinic and status, for the operator (Issue 61)."""
    items = [
        PlatformDisplayDeviceOut(**device_out(device).model_dump(), site_name=site.name)
        for device, site in devices.platform_devices(db)
    ]
    return PlatformDisplayDeviceListOut(
        total=len(items),
        silent=sum(1 for item in items if item.status is DisplayDeviceStatus.SILENT),
        items=items,
    )
