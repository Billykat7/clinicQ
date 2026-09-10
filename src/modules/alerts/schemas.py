"""Pydantic schemas for the alerts API (Issue #132 follow-up)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.modules.messaging.enums import (
    AlertKind,
    AlertSeverity,
    AlertSource,
    AudienceType,
)


class AlertRead(BaseModel):
    """One alert, with the caller's own read state."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    source: AlertSource
    severity: AlertSeverity
    kind: AlertKind
    author_id: str | None = Field(
        default=None,
        description="Composing staff member's user id; null for a system-raised alert.",
    )
    author_name: str | None = Field(
        default=None, description="Author display name at read time, when resolvable."
    )
    subject: str
    body: str = Field(description="The alert text, verbatim.")
    audience_type: AudienceType | None = None
    audience_ref: str | None = None
    created_at: datetime
    is_read: bool = Field(description="Whether the caller has read this alert.")
    read_at: datetime | None = None


class AlertListOut(BaseModel):
    """Paginated alert listing for one of the console's Inbox/Sent/Deleted tabs."""

    items: list[AlertRead] = Field(description="The page of alerts.")
    total: int = Field(ge=0, description="Total alerts in scope (pre-paging).")


class AlertCreateIn(BaseModel):
    """Request body to compose and send a staff alert to a resolved audience in one step.

    The audience is a *rule* (all users, an owner's tenants, a property's occupants, a managed
    scope), resolved and authorised server-side exactly like an announcement's — the sender never
    enumerates recipients. Every alert composed this way is source ``staff`` / kind ``custom``.
    """

    model_config = ConfigDict(extra="forbid")

    audience_type: AudienceType = Field(
        description="How the recipient set is resolved from the sender's authority."
    )
    audience_ref: str | None = Field(
        default=None,
        max_length=36,
        description="Target the audience refers to when it needs one (e.g. a property id).",
    )
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(
        min_length=1,
        max_length=8000,
        description="The alert text; stored verbatim and escaped when rendered.",
    )
    severity: AlertSeverity = Field(description="How urgent this alert is.")


class AlertDraftCreateIn(BaseModel):
    """Request body to save a new alert draft — every field optional, mid-thought is fine."""

    model_config = ConfigDict(extra="forbid")

    audience_type: AudienceType | None = Field(default=None)
    audience_ref: str | None = Field(default=None, max_length=36)
    subject: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=8000)
    severity: AlertSeverity | None = Field(default=None)


class AlertDraftUpdateIn(BaseModel):
    """Partial update to an alert draft — only the fields present are changed (null clears)."""

    model_config = ConfigDict(extra="forbid")

    audience_type: AudienceType | None = Field(default=None)
    audience_ref: str | None = Field(default=None, max_length=36)
    subject: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=8000)
    severity: AlertSeverity | None = Field(default=None)


class AlertDraftRead(BaseModel):
    """One of the author's private alert drafts."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    audience_type: AudienceType | None = None
    audience_ref: str | None = None
    subject: str | None = None
    body: str | None = None
    severity: AlertSeverity | None = None
    created_at: datetime
    modified_at: datetime


class AlertMarkReadResult(BaseModel):
    """Result of marking an alert read/unread for the caller."""

    is_read: bool
