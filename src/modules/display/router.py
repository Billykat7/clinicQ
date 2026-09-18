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

import asyncio
import functools
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.api.rbac_deps import DbSession, require
from src.commons.enums import (
    AuditAction,
    AuditEntityType,
    DisplayDeviceKind,
    DisplayDeviceStatus,
    GrantScope,
)
from src.core.audit import record_audit_event
from src.core.client_ip import resolve_client_ip
from src.core.config import Settings, get_settings
from src.core.site_scope import SiteAccess, require_site_access
from src.database.models import DisplayDevice
from src.modules.display import casting, devices, discovery
from src.modules.display.devices import MAX_LABEL_LENGTH
from src.modules.display.discovery import CAST_PORT

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
#: The deployment's own settings, resolved like every other request's. Read through the dependency
#: rather than by calling ``get_settings()`` inside the handler, so that a deployment (or a test)
#: that injects its own settings governs whether this server searches its network at all.
SettingsDep = Annotated[Settings, Depends(get_settings)]

#: Said for a wrong, expired or used code alike, so the answer does not help anyone guess.
#: How an audit row names each kind of device (Issue 83).
_KIND_WORDS: dict[DisplayDeviceKind, str] = {
    DisplayDeviceKind.BOARD: "display board",
    DisplayDeviceKind.CHECK_IN: "check-in tablet",
}

CODE_NOT_FOUND = "No screen is waiting with that code. Check the code on the screen; if it has changed, type the new one."


class DisplayDevicePairIn(BaseModel):
    """What a manager types to pair a screen."""

    pairing_code: str = Field(min_length=6, max_length=12)
    label: str | None = Field(default=None, max_length=MAX_LABEL_LENGTH)
    #: What the device is: the waiting-room board, or the check-in tablet at the door (Issue 83).
    kind: DisplayDeviceKind = DisplayDeviceKind.BOARD
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
    kind: DisplayDeviceKind
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
        kind=device.kind_enum,
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
            kind=payload.kind,
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
        f"{_KIND_WORDS[device.kind_enum]} paired: {device.label or 'unnamed'}",
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


# --------------------------------------------------------------------------------------
# Screens the server can find and reach itself (Issue 237)
# --------------------------------------------------------------------------------------


class DiscoveredScreenOut(BaseModel):
    """One Chromecast-capable screen that answered on the server's network.

    Nothing here is a secret: it is what the screen shouts to anyone on the network who asks. The
    address comes back with the manager's choice (:class:`ScreenConnectIn`) rather than being kept
    on the server, because a search is a snapshot of a room, not a record worth storing.
    """

    uuid: str
    name: str
    model: str
    manufacturer: str
    address: str
    port: int
    suggested_label: str


class ScreenScanOut(BaseModel):
    """What a search of the network found, and a sentence when it found nothing."""

    total: int
    items: list[DiscoveredScreenOut]
    #: Why the list is empty, written for the manager. Empty when screens were found.
    note: str
    #: Whether the search actually ran; False when the deployment has it switched off.
    searched: bool


class ScreenConnectIn(BaseModel):
    """The screen a manager picked, and what the board on it should be."""

    address: str = Field(min_length=3, max_length=64)
    port: int = Field(default=CAST_PORT, ge=1, le=65535)
    name: str = Field(default="", max_length=120)
    uuid: str = Field(default="", max_length=120)
    label: str | None = Field(default=None, max_length=MAX_LABEL_LENGTH)
    kind: DisplayDeviceKind = DisplayDeviceKind.BOARD
    queue_ids: list[str] | None = None


class ScreenConnectOut(BaseModel):
    """What happened when the server tried to put the board on the chosen screen."""

    #: Whether the screen answered the server at all.
    reached: bool
    #: Whether it was handed the board and started opening it.
    showing: bool
    note: str
    #: The row now held for this screen. It reads as ``pairing`` until the screen takes it up, and
    #: is a real row either way, so a screen that never answers is visible and removable.
    device: DisplayDeviceOut


#: Hosts that mean "this machine" and therefore mean something different on a television. A screen
#: told to claim itself at ``localhost`` would ask *itself*, which is nobody.
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def _claim_url_for(request: Request) -> str:
    """The absolute address a screen should hand its claim code back to, or ``""`` for "your own".

    A manager working on the server itself reaches the dashboard at ``localhost``, and a television
    handed that address would call on itself. In that case nothing is sent and the receiver uses the
    address it was served from — which is the registered receiver URL, so it is by definition an
    address the screen could reach.
    """
    url = request.url_for("display_claim")
    if (url.hostname or "").lower() in _LOOPBACK_HOSTS:
        return ""
    return str(url)


@router.post(
    "/sites/{site_id}/display-devices/discover",
    response_model=ScreenScanOut,
    operation_id="displayDiscoverScreens",
)
async def discover_screens(
    access: DisplayUpdate, settings: SettingsDep
) -> ScreenScanOut:
    """Search the **server's** network for Chromecast-capable screens (Issue 237).

    A POST because it is an action with a cost — it holds the network open for several seconds — not
    a page that may be re-fetched and cached. It needs the grant that pairs a screen, because what
    it returns is the list a manager picks from to take a screen over.

    Run off the event loop: the search listens on a socket for :data:`Settings.smart_tv_discovery_seconds`
    and would otherwise stall every other request on this worker for that long.
    """
    outcome = await asyncio.to_thread(
        functools.partial(discovery.scan_for_screens, settings=settings)
    )
    return ScreenScanOut(
        total=outcome.found,
        items=[
            DiscoveredScreenOut(
                uuid=screen.uuid,
                name=screen.name,
                model=screen.model,
                manufacturer=screen.manufacturer,
                address=screen.address,
                port=screen.port,
                suggested_label=screen.label_suggestion,
            )
            for screen in outcome.screens
        ],
        note=outcome.note,
        searched=outcome.searched,
    )


@router.post(
    "/sites/{site_id}/display-devices/connect",
    response_model=ScreenConnectOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="displayConnectScreen",
)
async def connect_screen(
    payload: ScreenConnectIn,
    request: Request,
    access: DisplayUpdate,
    db: DbSession,
    settings: SettingsDep,
) -> ScreenConnectOut:
    """Hold a row for the chosen screen and ask it to open this clinic's board (Issue 237).

    The row is created **before** the screen is contacted and kept whatever the screen does, because
    the two outcomes a manager needs to tell apart — "the TV is off" and "the TV is showing the
    board" — both leave a screen in the clinic's list, one waiting and one live. A row nobody ever
    claims expires as a pairing code does and can be removed like any screen.

    Audited either way: taking over a screen in a waiting room is a change to what the public sees,
    whether or not the screen cooperated.
    """
    try:
        reserved = devices.start_claimable_device(
            db,
            access,
            label=payload.label,
            kind=payload.kind,
            queue_ids=payload.queue_ids,
        )
    except devices.UnknownQueueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "A chosen queue is not one of this clinic's.",
        ) from exc
    device = reserved.device
    outcome = await asyncio.to_thread(
        casting.send_board_to_screen,
        discovery.ScreenTarget(
            address=payload.address,
            port=payload.port,
            name=payload.name,
            uuid=payload.uuid,
        ),
        claim_code=reserved.code,
        board_url=_claim_url_for(request),
        settings=settings,
    )
    _audit(
        db,
        request,
        access,
        device,
        (
            f"{_KIND_WORDS[device.kind_enum]} sent to the screen at {payload.address}: "
            f"{'opening the board' if outcome.showing else 'not shown (' + outcome.note[:80] + ')'}"
        ),
    )
    db.commit()
    return ScreenConnectOut(
        reached=outcome.reached,
        showing=outcome.showing,
        note=outcome.note,
        device=device_out(device),
    )


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
