"""Request and response models for the queues API (Issue 25).

The one decision worth naming: **``allows_remote_join`` is an ordinary field a client can set, and
an extraordinary one the server enforces.** A queue that says walk-ins only refuses a remote join in
:func:`src.modules.queues.service.ensure_remote_join_allowed`, not by a client politely declining to
show the button.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.commons.enums import QueueKind, TicketSource
from src.database.models.queue import MAX_DAILY_CAPACITY, MAX_EXPECTED_SERVICE_MINUTES

#: A slug is lowercase letters, digits and single hyphens: it appears in URLs and USSD menus.
SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
#: A ticket prefix is one to four capital letters: ``A``, ``T``, ``PH``.
PREFIX_PATTERN = r"^[A-Z]{1,4}$"


class QueueIn(BaseModel):
    """The fields a clinic manager may set when creating or updating a queue."""

    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(min_length=2, max_length=80, pattern=SLUG_PATTERN)
    kind: QueueKind = QueueKind.OTHER
    room_label: str | None = Field(default=None, max_length=60)
    ticket_prefix: str = Field(default="A", pattern=PREFIX_PATTERN)
    display_order: int = Field(default=0, ge=0, le=999)
    expected_service_minutes: int = Field(
        default=10, ge=1, le=MAX_EXPECTED_SERVICE_MINUTES
    )
    """Validated to a sensible range: zero would make the estimator divide by nothing, and a value
    above four hours is somebody typing hours into a minutes field."""
    max_daily_capacity: int | None = Field(default=None, ge=1, le=MAX_DAILY_CAPACITY)
    allows_remote_join: bool = True
    is_active: bool = True


class QueueOut(BaseModel):
    """One queue as the API returns it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    site_id: str
    name: str
    slug: str
    kind: QueueKind
    room_label: str | None
    ticket_prefix: str
    display_order: int
    expected_service_minutes: int
    max_daily_capacity: int | None
    allows_remote_join: bool
    is_active: bool
    created_at: datetime
    modified_at: datetime


class QueueListOut(BaseModel):
    """A clinic's queues, in the order the clinic put them in."""

    site_id: str
    total: int = Field(ge=0)
    items: list[QueueOut]


class QueueOrderIn(BaseModel):
    """A reordering: the queue ids of one clinic, in the order they should appear.

    The whole list rather than one queue's new position, because reordering is one decision and a
    per-queue update is how two queues end up sharing a position.
    """

    queue_ids: list[str] = Field(min_length=1, max_length=50)


class JoinableQueueOut(BaseModel):
    """One queue as a channel menu sees it, with the server's answer about joining it."""

    id: str
    name: str
    slug: str
    kind: QueueKind
    room_label: str | None
    expected_service_minutes: int
    #: The server's decision for the channel that asked, never the client's.
    joinable: bool
    #: What the patient is told when ``joinable`` is false.
    refusal: str | None


class JoinableQueueListOut(BaseModel):
    """What a given channel may join at a clinic right now."""

    site_id: str
    #: The channel that asked. Echoed back so a cached menu cannot be shown to the wrong one.
    source: TicketSource
    items: list[JoinableQueueOut]
