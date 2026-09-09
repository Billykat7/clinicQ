"""Request/response models for the in-app messaging API (Issue #68).

The surface is entirely session-gated and authorised by *participation*, not an RBAC verb: who
may read or post a thread is derived from the thread's anchor (its unit's tenant and owner, a work
order's assigned vendor, an application's applicant). These projections therefore never carry a
hand-set recipient list — participants are computed and returned read-only.

Message bodies are returned verbatim; escaping is a render-time concern handled by the portal
template, so the JSON here is the exact text the author typed.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.modules.messaging.enums import AudienceType, MessageAnchorType


class ThreadCreateIn(BaseModel):
    """Request body to open (or re-open) the conversation anchored to a record.

    Thread creation is an idempotent get-or-create: opening the thread for an anchor that already
    has one returns the existing thread rather than a duplicate. The caller must be a participant
    of the anchor, or the request is refused.
    """

    model_config = ConfigDict(extra="forbid")

    anchor_type: MessageAnchorType = Field(
        description="Kind of record the thread hangs off (unit, lease, work order, application)."
    )
    anchor_id: str = Field(
        min_length=1,
        max_length=36,
        description="Id of the anchored record.",
    )
    subject: str | None = Field(
        default=None,
        max_length=200,
        description="Optional human-readable title for the conversation.",
    )


class MessageCreateIn(BaseModel):
    """Request body for posting one message to a thread."""

    model_config = ConfigDict(extra="forbid")

    body: str = Field(
        min_length=1,
        max_length=8000,
        description="The message text; stored verbatim and escaped when rendered.",
    )


class ParticipantRead(BaseModel):
    """One derived participant of a thread (never hand-set)."""

    model_config = ConfigDict(from_attributes=True)

    user_id: str = Field(description="The participant's user id.")
    name: str | None = Field(
        default=None, description="Display name, when the user has one."
    )
    role: str = Field(
        description="How this participant is derived from the anchor "
        "(tenant, owner, vendor or applicant)."
    )


class MessageRead(BaseModel):
    """One message in a thread."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    thread_id: str
    author_id: str | None = Field(
        default=None,
        description="Author user id; null if the author's account was removed.",
    )
    author_name: str | None = Field(
        default=None, description="Author display name at read time, when resolvable."
    )
    body: str = Field(description="The message text, verbatim.")
    created_at: datetime


class ThreadRead(BaseModel):
    """A thread with its anchor, derived participants and the caller's unread count."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    anchor_type: MessageAnchorType
    anchor_id: str
    subject: str | None = None
    created_at: datetime
    last_message_at: datetime | None = Field(
        default=None, description="When the most recent message was posted, if any."
    )
    unread_count: int = Field(
        description="How many messages in this thread the caller has not yet read."
    )
    participants: list[ParticipantRead] = Field(
        default_factory=list,
        description="Participants derived from the anchor's live state.",
    )


class ThreadDetailRead(ThreadRead):
    """A thread plus its full, chronologically ordered message list."""

    messages: list[MessageRead] = Field(default_factory=list)


class ThreadListOut(BaseModel):
    """Paginated thread listing for the manager-scoped console view (Issue #87)."""

    items: list[ThreadRead] = Field(description="The page of threads.")
    total: int = Field(ge=0, description="Total threads in scope (pre-paging).")


class MarkReadResult(BaseModel):
    """Result of marking a thread read: how many messages became read for the caller."""

    marked_read: int = Field(
        description="Number of previously-unread messages marked read by this call."
    )


class UnreadTotalOut(BaseModel):
    """The caller's total unread message count across their inbox — the bell badge (Issue #113)."""

    unread_total: int = Field(
        ge=0,
        description="Sum of unread messages across every thread the caller participates in.",
    )


# --------------------------------------------------------------------------------------
# Drafts & announcements (Issue #112).
# --------------------------------------------------------------------------------------


class DraftCreateIn(BaseModel):
    """Request body to save a new draft — an author-private, unsent message being composed.

    Every field is optional so a draft can be saved the instant composing begins. A draft names
    *where it will go* — either an existing ``thread_id`` (a reply) or an ``audience_type`` (an
    announcement) — but the destination is only validated and authorised when the draft is sent.
    """

    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = Field(
        default=None,
        max_length=36,
        description="Existing thread this draft will reply to, when it is a reply.",
    )
    audience_type: AudienceType | None = Field(
        default=None,
        description="Audience this draft will broadcast to, when it is an announcement.",
    )
    audience_ref: str | None = Field(
        default=None,
        max_length=36,
        description="Target the audience refers to when it needs one (e.g. a property id).",
    )
    subject: str | None = Field(
        default=None,
        max_length=200,
        description="Optional title carried onto the thread for a new announcement.",
    )
    body: str | None = Field(
        default=None,
        max_length=8000,
        description="The message text so far; stored verbatim and escaped only when rendered.",
    )


class DraftUpdateIn(BaseModel):
    """Partial update to a draft — only the fields present are changed (an explicit null clears).

    Uses ``exclude_unset`` at the router so an omitted field is left as-is while an explicit
    ``null`` clears it (e.g. switching a draft from a reply to an announcement).
    """

    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = Field(default=None, max_length=36)
    audience_type: AudienceType | None = Field(default=None)
    audience_ref: str | None = Field(default=None, max_length=36)
    subject: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=8000)


class DraftRead(BaseModel):
    """One of the author's private drafts."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    thread_id: str | None = None
    audience_type: AudienceType | None = None
    audience_ref: str | None = None
    subject: str | None = None
    body: str | None = None
    created_at: datetime
    modified_at: datetime


class PeerRead(BaseModel):
    """One tenant the caller may message peer-to-peer — a same-property neighbour (Issue #126)."""

    model_config = ConfigDict(from_attributes=True)

    user_id: str = Field(
        description="The neighbour's user id (the opaque recipient handle)."
    )
    name: str | None = Field(
        default=None, description="Display name, when the neighbour has one."
    )


class PeerListOut(BaseModel):
    """The tenants the caller may reach peer-to-peer — only same-property neighbours (Issue #126).

    The compose entry point renders **only** this list, so a tenant can never see — or enumerate —
    a tenant of a property they do not share.
    """

    items: list[PeerRead] = Field(
        default_factory=list, description="Reachable neighbours (same property)."
    )


class PeerMessageCreateIn(BaseModel):
    """Request body to message a neighbour peer-to-peer (Issue #126).

    The recipient is named by their opaque ``recipient_user_id`` and validated **server-side**
    against the caller's co-occupancy set — a recipient who is not a reachable neighbour is refused
    identically to a non-existent one, so the negative case leaks nothing. Sending get-or-creates
    the direct thread and posts the message in one step.
    """

    model_config = ConfigDict(extra="forbid")

    recipient_user_id: str = Field(
        min_length=1,
        max_length=36,
        description="The neighbour to message (an id from GET /messaging/peers).",
    )
    subject: str | None = Field(
        default=None,
        max_length=200,
        description="Optional title, applied only when the thread is first created.",
    )
    body: str = Field(
        min_length=1,
        max_length=8000,
        description="The message text; stored verbatim and escaped when rendered.",
    )


class AnnouncementCreateIn(BaseModel):
    """Request body to compose and send an announcement to a resolved audience in one step.

    The audience is a *rule* (all users, an owner's tenants, a property's occupants, a managed
    scope), resolved and authorised server-side by RBAC + ownership — the sender never enumerates
    recipients. A body is required (unlike a draft, this posts immediately).
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
    subject: str | None = Field(
        default=None,
        max_length=200,
        description="Optional human-readable title for the announcement.",
    )
    body: str = Field(
        min_length=1,
        max_length=8000,
        description="The announcement text; stored verbatim and escaped when rendered.",
    )
