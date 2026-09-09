"""HTTP routes for in-app messaging — threads anchored to a record (Issue #68).

Every route is session-gated and authorised by *participation*, not an RBAC verb: who may read or
post a thread is derived from the thread's anchor (a unit's tenant and owner, a work order's
assigned vendor, an application's applicant). A caller who is not a participant is answered **404**
— they cannot even tell the thread exists — which is also what a genuinely missing thread returns.

* ``POST /messaging/threads`` — open (idempotent get-or-create) the conversation for an anchor.
* ``GET  /messaging/threads`` — list the threads the caller participates in.
* ``GET  /messaging/threads/{id}`` — a thread with its participants and full message list.
* ``POST /messaging/threads/{id}/messages`` — post a message; the other participants are emailed
  once, post-commit and best-effort (a mail failure never undoes the message).
* ``POST /messaging/threads/{id}/read`` — mark the thread's messages read for the caller.

Message bodies are stored and returned verbatim; escaping is the render layer's job (the portal
template autoescapes), so no body is HTML-injected and none is double-escaped.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.rbac_deps import require
from src.commons.enums import GrantScope, PermissionVerb
from src.commons.exceptions import (
    AudienceNotAuthorisedError,
    EmptyAudienceError,
    MessageAnchorNotFoundError,
    MessageDraftNotFoundError,
    NotAThreadParticipantError,
    PeerNotReachableError,
)
from src.core.rbac import (
    ensure_permission_key,
    get_effective_verb_for_role,
    granted_covers_required,
)
from src.core.scope import resolve_scope
from src.core.security import get_current_user
from src.database.models import Message, MessageThread, User
from src.database.session import get_db
from src.modules.account.users import find_user_id_by_email
from src.modules.messaging import drafts as drafts_service
from src.modules.messaging import peers as peers_service
from src.modules.messaging import service
from src.modules.messaging.enums import AudienceType, MessageAnchorType
from src.modules.messaging.schemas import (
    AnnouncementCreateIn,
    DraftCreateIn,
    DraftRead,
    DraftUpdateIn,
    MarkReadResult,
    MessageCreateIn,
    MessageRead,
    ParticipantRead,
    PeerListOut,
    PeerMessageCreateIn,
    PeerRead,
    ThreadCreateIn,
    ThreadDetailRead,
    ThreadListOut,
    ThreadRead,
    UnreadTotalOut,
)

router = APIRouter(prefix="/messaging", tags=["messaging"])

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[dict, Depends(get_current_user)]

# Issue #163: resolved through the generic ``require`` factory (src.api.rbac_deps) against the
# manifest-registered ``communications.messages`` resource, rather than the hand-written named
# functions the earlier ``communications`` work left behind (see Issue #149's Applications pilot).
CommunicationsMessagesReadDep = Annotated[
    None, Depends(require("communications.messages", "read"))
]
CommunicationsMessagesCreateDep = Annotated[
    None, Depends(require("communications.messages", "create"))
]


def _require_user_id(db: Session, current_user: dict) -> str:
    """Resolve the signed-in caller's user id, or raise 401 when it cannot be resolved.

    Messaging is authorised by *who* the caller is (participation), so an unresolvable caller —
    including the anonymous context when auth is disabled — cannot act on any thread.
    """
    email = (current_user.get("email") or "").strip()
    user_id = find_user_id_by_email(db, email) if email else None
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return user_id


def _require_user_row(db: Session, current_user: dict) -> User:
    """Resolve the signed-in caller's full ``User`` row, or raise 401.

    Announcement sending needs the sender's *role* (to authorise the audience), not just their id,
    so the audience layer can gate global/managed broadcasts. Reuses :func:`_require_user_id` for
    the resolve-or-401, then loads the row.
    """
    user_id = _require_user_id(db, current_user)
    user = db.get(User, user_id)
    if user is None:  # pragma: no cover — id came from a live lookup a line earlier
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return user


def _participant_reads(db: Session, thread: MessageThread) -> list[ParticipantRead]:
    """Project a thread's derived participants for the API."""
    anchor_type = MessageAnchorType(thread.anchor_type)
    return [
        ParticipantRead(user_id=p.user_id, name=p.name, role=p.role)
        for p in service.derive_participants(db, anchor_type, thread.anchor_id)
    ]


def _message_read(db: Session, message: Message) -> MessageRead:
    """Project one message, resolving the author's display name when still available."""
    author_name: str | None = None
    if message.author_id is not None:
        author = db.get(User, message.author_id)
        if author is not None:
            parts = [p for p in (author.first_name, author.last_name) if p]
            author_name = " ".join(parts) if parts else None
    return MessageRead(
        id=message.id,
        thread_id=message.thread_id,
        author_id=message.author_id,
        author_name=author_name,
        body=message.body,
        created_at=message.created_at,
    )


def _thread_read(db: Session, thread: MessageThread, *, user_id: str) -> ThreadRead:
    """Project a thread with its participants and the caller's unread count."""
    return ThreadRead(
        id=thread.id,
        anchor_type=MessageAnchorType(thread.anchor_type),
        anchor_id=thread.anchor_id,
        subject=thread.subject,
        created_at=thread.created_at,
        last_message_at=service.last_message_at(db, thread.id),
        unread_count=service.unread_count(db, thread.id, user_id),
        participants=_participant_reads(db, thread),
    )


@router.post(
    "/threads",
    response_model=ThreadRead,
    status_code=status.HTTP_201_CREATED,
)
def open_thread(
    body: ThreadCreateIn,
    db: DbSession,
    current_user: CurrentUser,
) -> ThreadRead:
    """Open (get-or-create) the conversation anchored to a record.

    Idempotent: an anchor that already has a thread returns it. The caller must be a participant
    of the anchor (404 otherwise, so a non-participant learns nothing) unless they hold the
    manager grant (``communications.messages`` READ) — the Messages console's Compose page
    (Issue #132 follow-up) lets a manager start a conversation on any record they oversee, not
    only ones they already participate in, mirroring the manager bypass replying to an existing
    thread already has. A missing anchored record is a 404 either way.
    """
    user_id = _require_user_id(db, current_user)
    try:
        thread = service.get_or_create_thread(
            db,
            anchor_type=body.anchor_type,
            anchor_id=body.anchor_id,
            actor_user_id=user_id,
            subject=body.subject,
            require_participant=not _is_manager(db, current_user),
        )
    except (MessageAnchorNotFoundError, NotAThreadParticipantError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    db.commit()
    return _thread_read(db, thread, user_id=user_id)


@router.get("/threads", response_model=list[ThreadRead])
def list_threads(
    db: DbSession,
    current_user: CurrentUser,
) -> list[ThreadRead]:
    """List the threads the caller participates in, most-recently-active first."""
    user_id = _require_user_id(db, current_user)
    return [
        _thread_read(db, thread, user_id=user_id)
        for thread in service.list_threads_for_user(db, user_id)
    ]


@router.get("/managed-threads", response_model=ThreadListOut)
def list_managed_threads(
    db: DbSession,
    current_user: CurrentUser,
    offset: Annotated[int, Query(ge=0, description="Row offset for pagination.")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Max rows per page.")] = 20,
    folder: Annotated[
        str,
        Query(
            description="``inbox`` (default, all live threads), ``sent`` (the caller has "
            "authored at least one message) or ``deleted`` (soft-deleted, Issue #132 follow-up)."
        ),
    ] = "inbox",
) -> ThreadListOut:
    """List threads for the console's managed scope (Issue #87), widened per caller (Issue #157).

    Gated per tab (Issue #145) — ``communications.messages.{folder}`` READ, e.g.
    ``communications.messages.sent`` for the Sent tab — via a pure permission check, so a role can
    be scoped to a single tab and a caller without it is 403; a coarse ``communications.messages``
    grant still reaches every tab, via inheritance. That grant alone decides *whether* this console
    opens for a caller — no role-name check anywhere in this authorisation.

    What it then *shows* is a second, separate question, and since M28 the **same grant** answers
    it: :func:`~src.core.scope.resolve_scope` reads the ``scope`` tier the caller's grant carries.
    ``business`` — every seeded staff role, unchanged from before — sees the whole business.
    ``own`` — a portal-scoped caller who reaches this console only because an admin granted them a
    tab — is narrowed to the threads they participate in, via ``list_all_threads``'
    ``participant_user_id`` filter (``communications.messages`` declares the
    :data:`~src.commons.enums.ScopeShape.THREAD_PARTICIPANT` shape on its manifest). Without that
    narrowing, granting any portal role a ``communications.messages`` tab would let them enumerate
    every thread in the business: the grant is what opens the door, the scope tier is what keeps
    that caller in their own room.

    This replaced (Issue #157, M28) a hand-written ``is_management_role(user.role)`` branch and a
    module-local ``_narrowed_managed_threads`` helper — the proof-of-concept the shared resolver
    was then generalised from. Same results, one shared mechanism. Paginated,
    most-recently-active first; ``total`` is the full count.
    """
    if folder not in ("inbox", "sent", "deleted"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="folder must be one of: inbox, sent, deleted.",
        )
    resource_key = f"communications.messages.{folder}"
    ensure_permission_key(db, current_user, resource_key, "read")
    user = _require_user_row(db, current_user)
    scope = resolve_scope(db, user, resource_key)
    threads, total = service.list_all_threads(
        db,
        offset=offset,
        limit=limit,
        authored_by=user.id if folder == "sent" else None,
        deleted=folder == "deleted",
        participant_user_id=None if scope is GrantScope.BUSINESS else scope.user_id,
    )
    return ThreadListOut(
        items=[_thread_read(db, thread, user_id=user.id) for thread in threads],
        total=total,
    )


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_thread(
    thread_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    """Soft-delete a thread — the console's Deleted tab (Issue #132 follow-up). Reversible; see restore.

    Manager-gated the same way the rest of this module's staff-console actions are — READ on
    whichever resource the loaded thread's own kind maps to (:func:`_manager_resource_for_thread`,
    via :func:`_is_manager`) — a participant alone may not delete the shared conversation. The
    thread is loaded first so the gate can key off its actual kind; a thread that does not exist is
    404 regardless of the caller's grants.
    """
    thread = service.get_thread(db, thread_id)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found"
        )
    if not _is_manager(db, current_user, _manager_resource_for_thread(thread)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Deleting a thread requires the communications management permission.",
        )
    service.soft_delete_thread(db, thread)
    db.commit()


@router.post("/threads/{thread_id}/restore", response_model=ThreadRead)
def restore_thread(
    thread_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> ThreadRead:
    """Undo a soft-delete, returning a thread to its inbox/sent listing (Issue #132 follow-up).

    Loaded first so the manager gate can key off the thread's actual kind
    (:func:`_manager_resource_for_thread`) — a thread that does not exist is 404 regardless of the
    caller's grants.
    """
    user_id = _require_user_id(db, current_user)
    thread = service.get_thread(db, thread_id)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found"
        )
    if not _is_manager(db, current_user, _manager_resource_for_thread(thread)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Restoring a thread requires the communications management permission.",
        )
    service.restore_thread(db, thread)
    db.commit()
    return _thread_read(db, thread, user_id=user_id)


@router.get("/announcement-threads", response_model=ThreadListOut)
def list_announcement_threads(
    db: DbSession,
    current_user: CurrentUser,
    folder: Annotated[
        str,
        Query(description="``inbox`` (default), ``sent`` or ``deleted``."),
    ] = "inbox",
    offset: Annotated[int, Query(ge=0, description="Row offset for pagination.")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Max rows per page.")] = 20,
) -> ThreadListOut:
    """List the caller's announcement threads for one console tab (Issue #132 follow-up).

    Participation-scoped like ``GET /threads``, not manager-wide like ``GET /managed-threads`` — an
    announcement's audience is who it actually reached, not everyone a manager oversees. Gated per
    tab (Issue #145) — ``communications.announcements.{folder}`` READ — rather than the coarse
    module verb Issue #144's follow-up first closed this endpoint's gap with, so a role can be
    scoped to a single tab; a coarse ``communications.announcements`` grant still reaches every tab
    unchanged, via inheritance. Unlike the base ``GET /threads``, the caller's own participation is
    not, by itself, sufficient reason to reach this endpoint — the console it backs is a management
    surface.
    """
    if folder not in ("inbox", "sent", "deleted"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="folder must be one of: inbox, sent, deleted.",
        )
    # Issue #166 (M28): ``business`` tier as well as the verb. This endpoint backs the staff
    # announcements console, whose nav destination and per-tab ``NavMeta`` both declare
    # ``business`` (Issue #165) — the endpoint must agree, which
    # ``tests/unit/security/test_nav_enforcement_parity.py`` now asserts. A recipient still reads
    # their own announcement thread: that goes through ``GET /threads``/``_load_accessible_thread``
    # on *participation*, which this gate does not touch.
    ensure_permission_key(
        db,
        current_user,
        f"communications.announcements.{folder}",
        "read",
        required_scope=GrantScope.BUSINESS,
    )
    user_id = _require_user_id(db, current_user)
    role = "sender" if folder in ("sent", "deleted") else "recipient"
    threads, total = service.list_announcement_threads(
        db,
        user_id=user_id,
        role=role,
        deleted=folder == "deleted",
        offset=offset,
        limit=limit,
    )
    return ThreadListOut(
        items=[_thread_read(db, thread, user_id=user_id) for thread in threads],
        total=total,
    )


def _manager_resource_for_thread(thread: MessageThread) -> str:
    """Return the resource that governs the manager-bypass check for one thread, by its kind.

    ``MessageThread`` rows back both ordinary record-anchored conversations and announcements
    (``anchor_type``); the two are console-gated on different resources
    (``communications.messages`` vs ``communications.announcements``). A per-thread action must
    check whichever resource the thread it is actually acting on belongs to — checking
    ``communications.messages`` unconditionally let a messages-only manager bypass into
    announcement threads they hold no announcements grant for, and refused an
    announcements-only manager the announcement threads they should be able to reach.
    """
    if MessageAnchorType(thread.anchor_type) == MessageAnchorType.ANNOUNCEMENT:
        return "communications.announcements"
    return "communications.messages"


def _is_manager(
    db: Session,
    current_user: dict,
    resource: str = "communications.messages",
) -> bool:
    """Return whether the caller holds READ on ``resource`` — the manager scope gate.

    The staff console (Issue #87) lets a *manager* see and act on threads for records they
    oversee, beyond the ones they were explicitly added to. Used to proxy that authority through
    ``leases`` READ (a lease-managing role) — a stand-in chosen before Communications had its own
    resource tree; fixed in Issue #144 to check the resource the console's own nav destination and
    ``/admin/rbac/permissions`` grant actually name, so a role scoped to messaging alone (without a
    leases grant) can now hold the manager scope, and vice versa. ``resource`` defaults to
    ``communications.messages`` for module-wide call sites with no single thread to key off of
    (``list_managed_threads``, ``get_managed_unread_total``); a call site acting on one loaded
    thread must instead pass :func:`_manager_resource_for_thread` so an announcement thread is
    checked against ``communications.announcements``, not messages. A caller without it stays
    strictly participant-scoped.
    """
    email = (current_user.get("email") or "").strip().lower()
    if not email:
        return False
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        return False
    role = (user.role or "").strip()
    return granted_covers_required(
        get_effective_verb_for_role(db, role, resource),
        PermissionVerb.READ,
    )


def _load_accessible_thread(
    db: Session, thread_id: str, user_id: str, current_user: dict
) -> tuple[MessageThread, bool]:
    """Load a thread the caller may access, and whether they are a *participant* of it.

    Access is granted to a participant **or** a manager, gated on whichever resource governs the
    loaded thread's own kind (:func:`_manager_resource_for_thread` — ``communications.messages``
    for a record-anchored thread, ``communications.announcements`` for an announcement). A
    non-participant non-manager is answered 404 — indistinguishable from a genuinely missing
    thread — so participant privacy is preserved for ordinary users. The returned bool lets write
    paths keep the participant check on for participants and skip it (already authorised) for a
    manager.
    """
    thread = service.get_thread(db, thread_id)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found"
        )
    is_participant = service.is_participant(db, thread, user_id)
    if not is_participant and not _is_manager(
        db, current_user, _manager_resource_for_thread(thread)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found"
        )
    return thread, is_participant


@router.get("/threads/{thread_id}", response_model=ThreadDetailRead)
def get_thread_detail(
    thread_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> ThreadDetailRead:
    """Return one thread with its participants and full, chronological message list."""
    user_id = _require_user_id(db, current_user)
    thread, _ = _load_accessible_thread(db, thread_id, user_id, current_user)
    base = _thread_read(db, thread, user_id=user_id)
    return ThreadDetailRead(
        **base.model_dump(),
        messages=[_message_read(db, m) for m in service.list_messages(db, thread.id)],
    )


@router.post(
    "/threads/{thread_id}/messages",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
)
def post_message(
    thread_id: str,
    body: MessageCreateIn,
    db: DbSession,
    current_user: CurrentUser,
) -> MessageRead:
    """Post a message to a thread (participants only); notify the other participants once.

    The notification is sent post-commit and best-effort — a mail failure is swallowed inside the
    service and never turns a saved message into a 500.
    """
    user_id = _require_user_id(db, current_user)
    thread, is_participant = _load_accessible_thread(
        db, thread_id, user_id, current_user
    )
    message = service.post_message(
        db,
        thread,
        author_id=user_id,
        body=body.body,
        require_participant=is_participant,
    )
    db.commit()
    db.refresh(message)
    # Post-commit, best-effort: the other participants are told a message landed.
    service.notify_new_message(db, thread, message, author_id=user_id)
    return _message_read(db, message)


@router.post("/threads/{thread_id}/read", response_model=MarkReadResult)
def mark_read(
    thread_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> MarkReadResult:
    """Mark every message in the thread read by the caller; return how many were newly read."""
    user_id = _require_user_id(db, current_user)
    thread, is_participant = _load_accessible_thread(
        db, thread_id, user_id, current_user
    )
    marked = service.mark_thread_read(
        db, thread, user_id=user_id, require_participant=is_participant
    )
    db.commit()
    return MarkReadResult(marked_read=marked)


@router.get("/unread-count", response_model=UnreadTotalOut)
def get_unread_total(
    db: DbSession,
    current_user: CurrentUser,
) -> UnreadTotalOut:
    """Return the caller's total unread count across their inbox — the notification bell badge.

    The single number the M19 bell (Issue #113) reads: summed over the threads the caller currently
    participates in, so a thread they have just lost access to never inflates the badge.
    """
    user_id = _require_user_id(db, current_user)
    return UnreadTotalOut(unread_total=service.total_unread_for_user(db, user_id))


@router.get("/managed-unread-count", response_model=UnreadTotalOut)
def get_managed_unread_total(
    db: DbSession,
    current_user: CurrentUser,
) -> UnreadTotalOut:
    """Return the sum of unread counts across the manager console's Inbox (Issue #132 follow-up).

    Distinct from ``/unread-count`` (the bell, participant-only): this is manager-wide, matching
    every thread ``GET /managed-threads?folder=inbox`` lists — what the Messages console's own
    Inbox tab shows as its unread total. Gated on the same manager grant as the rest of the console.
    """
    user_id = _require_user_id(db, current_user)
    if not _is_manager(db, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Managed unread count requires the communications.messages management permission.",
        )
    return UnreadTotalOut(
        unread_total=service.total_unread_for_managed_inbox(db, user_id)
    )


# --------------------------------------------------------------------------------------
# Drafts & announcements (Issue #112).
# --------------------------------------------------------------------------------------


def _draft_read(draft: object) -> DraftRead:
    """Project one draft for the API (author-private; already author-scoped when loaded)."""
    return DraftRead.model_validate(draft)


def _post_announcement(
    db: Session,
    *,
    sender: User,
    body: str,
    audience_type: AudienceType | str,
    audience_ref: str | None,
    subject: str | None,
) -> MessageRead:
    """Resolve+authorise an audience, open the broadcast thread, post ``body``, notify recipients.

    Shared by the direct announcement endpoint (which passes an :class:`AudienceType`) and draft-send
    (which passes the value stored on the draft as a string); both are normalised to the enum here.
    Maps the audience errors to HTTP: an unauthorised audience is 403, an empty one 409. Notification
    is post-commit and best-effort, exactly like a normal message — a mail failure never undoes the
    posted announcement.
    """
    try:
        thread = service.create_announcement_thread(
            db,
            sender=sender,
            audience_type=AudienceType(audience_type),
            audience_ref=audience_ref,
            subject=subject,
        )
    except AudienceNotAuthorisedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    except EmptyAudienceError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    message = service.post_message(db, thread, author_id=sender.id, body=body)
    db.commit()
    db.refresh(message)
    # Post-commit, best-effort: every resolved recipient is told a broadcast landed (once each,
    # excluding the sender), honouring per-category preferences.
    service.notify_new_message(db, thread, message, author_id=sender.id)
    return _message_read(db, message)


@router.post(
    "/announcements",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
)
def create_announcement(
    body: AnnouncementCreateIn,
    db: DbSession,
    current_user: CurrentUser,
) -> MessageRead:
    """Compose and send an announcement to a resolved audience in one step (Issue #112).

    Any signed-in user may send, but *whom* the broadcast reaches is authorised server-side by RBAC
    + ownership: an admin alone may go global, an owner to their own tenants or a property they own,
    a manager within their managed scope. Targeting outside that authority is 403; an audience that
    resolves to no one is 409. Deliberately **not** gated by ``communications.announcements``
    CREATE (that named dependency exists in ``rbac_deps.py`` but is intentionally unused here): a
    single flat grant cannot express "admin may go global, an owner only to their own tenants" —
    :mod:`src.modules.messaging.audience` already authorises each audience type at a finer grain
    than any one permission could, so layering a coarse gate on top would only add a second,
    redundant check to satisfy (found and confirmed deliberate during Issue #144's follow-up,
    which fixed the read-side gap on ``GET /announcement-threads`` instead).
    """
    sender = _require_user_row(db, current_user)
    return _post_announcement(
        db,
        sender=sender,
        body=body.body,
        audience_type=body.audience_type,
        audience_ref=body.audience_ref,
        subject=body.subject,
    )


@router.post("/drafts", response_model=DraftRead, status_code=status.HTTP_201_CREATED)
def create_draft(
    body: DraftCreateIn,
    db: DbSession,
    current_user: CurrentUser,
) -> DraftRead:
    """Save a new private draft for the caller. The destination is validated only at send time."""
    user_id = _require_user_id(db, current_user)
    draft = drafts_service.create_draft(
        db,
        author_id=user_id,
        thread_id=body.thread_id,
        audience_type=body.audience_type,
        audience_ref=body.audience_ref,
        subject=body.subject,
        body=body.body,
    )
    db.commit()
    db.refresh(draft)
    return _draft_read(draft)


@router.get("/drafts", response_model=list[DraftRead])
def list_drafts(
    db: DbSession,
    current_user: CurrentUser,
) -> list[DraftRead]:
    """List the caller's private drafts, most-recently-edited first."""
    user_id = _require_user_id(db, current_user)
    return [_draft_read(d) for d in drafts_service.list_drafts(db, user_id)]


@router.get("/drafts/{draft_id}", response_model=DraftRead)
def get_draft(
    draft_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> DraftRead:
    """Return one of the caller's drafts; a draft that is not theirs is 404 (leaks nothing)."""
    user_id = _require_user_id(db, current_user)
    try:
        draft = drafts_service.get_draft(db, draft_id, user_id)
    except MessageDraftNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return _draft_read(draft)


@router.patch("/drafts/{draft_id}", response_model=DraftRead)
def update_draft(
    draft_id: str,
    body: DraftUpdateIn,
    db: DbSession,
    current_user: CurrentUser,
) -> DraftRead:
    """Partially update one of the caller's drafts — only the fields sent are changed.

    An explicit ``null`` clears a field (e.g. switching a draft from a reply to an announcement);
    an omitted field is left unchanged.
    """
    user_id = _require_user_id(db, current_user)
    changes = body.model_dump(exclude_unset=True)
    try:
        draft = drafts_service.update_draft(db, draft_id, user_id, **changes)
    except MessageDraftNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(draft)
    return _draft_read(draft)


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(
    draft_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    """Discard one of the caller's drafts. A draft that is not theirs is 404."""
    user_id = _require_user_id(db, current_user)
    try:
        drafts_service.delete_draft(db, draft_id, user_id)
    except MessageDraftNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    db.commit()


@router.post(
    "/drafts/{draft_id}/send",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
)
def send_draft(
    draft_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> MessageRead:
    """Send one of the caller's drafts: post it as a real message, then discard the draft.

    A draft with an empty body is 422. A **reply** draft (``thread_id``) posts to that thread if the
    caller may access it (participant or manager), else 404. An **announcement** draft
    (``audience_type``) resolves and authorises its audience (403/409 as for a direct announcement).
    A draft with neither destination is 422. On success the draft is deleted so it cannot be resent.
    """
    sender = _require_user_row(db, current_user)
    try:
        draft = drafts_service.get_draft(db, draft_id, sender.id)
    except MessageDraftNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    clean_body = (draft.body or "").strip()
    if not clean_body:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A draft needs a non-empty body before it can be sent.",
        )

    if draft.thread_id is not None:
        thread, is_participant = _load_accessible_thread(
            db, draft.thread_id, sender.id, current_user
        )
        message = service.post_message(
            db,
            thread,
            author_id=sender.id,
            body=clean_body,
            require_participant=is_participant,
        )
        drafts_service.delete_draft(db, draft_id, sender.id)
        db.commit()
        db.refresh(message)
        service.notify_new_message(db, thread, message, author_id=sender.id)
        return _message_read(db, message)

    if draft.audience_type is not None:
        result = _post_announcement(
            db,
            sender=sender,
            body=clean_body,
            audience_type=draft.audience_type,
            audience_ref=draft.audience_ref,
            subject=draft.subject,
        )
        # The announcement committed inside _post_announcement; discard the draft in its own commit.
        drafts_service.delete_draft(db, draft_id, sender.id)
        db.commit()
        return result

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="A draft needs a destination (a thread to reply to or an audience) to be sent.",
    )


# --------------------------------------------------------------------------------------
# Peer-to-peer messaging (Issue #126) — a tenant messages a neighbour in the same property.
#
# Both peer routes gate on the ``communications.messages`` grant (Issue #125) *and* a server-checked
# co-occupancy relationship (both hold an active tenancy in the same property). The listing surfaces
# **only** reachable neighbours, so a tenant can never enumerate people they cannot message; the
# resolved recipient is validated identically on send, so the negative case leaks nothing.
# --------------------------------------------------------------------------------------


@router.get("/peers", response_model=PeerListOut)
def list_peers(
    db: DbSession,
    current_user: CurrentUser,
    _rbac: CommunicationsMessagesReadDep,
) -> PeerListOut:
    """List the users the caller may message peer-to-peer.

    Returns only reachable people — never someone the caller has no relationship with — so the
    compose entry point cannot be used to enumerate the user directory.
    """
    user_id = _require_user_id(db, current_user)
    return PeerListOut(
        items=[
            PeerRead(user_id=n.user_id, name=n.name)
            for n in peers_service.list_reachable_peers(db, user_id)
        ]
    )


@router.post(
    "/peers",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
)
def message_a_peer(
    body: PeerMessageCreateIn,
    db: DbSession,
    current_user: CurrentUser,
    _rbac: CommunicationsMessagesCreateDep,
) -> MessageRead:
    """Message a neighbour: get-or-create the direct thread, post the message, notify them once.

    The recipient is validated server-side against the caller's co-occupancy set — a recipient who
    is not a reachable neighbour (including a non-existent one) is refused ``403`` identically, so
    nothing about the directory leaks. The thread reuses the M19 machinery (receipts, unread → bell,
    preference-honouring notification), exactly like any other conversation.
    """
    sender = _require_user_row(db, current_user)
    try:
        thread = service.get_or_create_peer_thread(
            db,
            sender=sender,
            recipient_user_id=body.recipient_user_id,
            subject=body.subject,
        )
    except PeerNotReachableError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc

    message = service.post_message(db, thread, author_id=sender.id, body=body.body)
    db.commit()
    db.refresh(message)
    # Post-commit, best-effort: the neighbour is told a message landed, honouring their preferences.
    service.notify_new_message(db, thread, message, author_id=sender.id)
    return _message_read(db, message)
