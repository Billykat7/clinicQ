"""Messaging service: threads anchored to a record, participant derivation, posting, receipts.

The one place the rules the issue (Issue #68) states are enforced:

* **Participants are derived from the anchor, never hand-set.** :func:`derive_participants` reads a
  thread's anchor — a unit, lease, work order or application — and computes who is in the
  conversation from *live* state: the tenant(s) occupying a unit, the owner of its property, the
  vendor *currently* assigned to a work order, the applicant on an application. There is no stored
  recipient list, which is exactly why unassigning a vendor drops their access **going forward,
  not retroactively** (the set is recomputed each time) while their past messages remain.
* **A participant sees only their threads.** Every read/post is gated by
  :func:`ensure_participant`; a non-participant is treated as if the thread does not exist.
* **New messages notify the other participants once.** :func:`notify_new_message` de-duplicates by
  user, excludes the author, and honours a per-user preference seam (Issue #72) before handing each
  mail to the notification service — best-effort and post-commit, so a mail failure never undoes a
  posted message.

All timestamps are Africa/Johannesburg (the business timezone).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.commons.enums import NotificationTemplate
from src.commons.exceptions import (
    MessageAnchorNotFoundError,
    NotAThreadParticipantError,
)
from src.core import email_send
from src.core.s3_logging import APP_TIMEZONE
from src.database.models import (
    Message,
    MessageParticipant,
    MessageReceipt,
    MessageThread,
    User,
)
from src.modules.messaging import audience as audience_service
from src.modules.messaging import peers
from src.modules.messaging.enums import AudienceType, MessageAnchorType
from src.modules.notifications import preferences

logger = logging.getLogger(__name__)


def _now() -> datetime:
    """Return the current time in the project business timezone (Africa/Johannesburg)."""
    return datetime.now(APP_TIMEZONE)


# --------------------------------------------------------------------------------------
# Participants — derived from the anchor's live state, never stored.
# --------------------------------------------------------------------------------------

# How a participant is described in the UI. Descriptive only — never an RBAC role: it says why
# someone is in the conversation, not what they may do. Announcement and peer participants are
# *stored* rather than derived from a live record, so these two are the labels the kernel writes;
# a record anchor of your own adds the labels its own deriver assigns.
ROLE_SENDER = "sender"
ROLE_RECIPIENT = "recipient"


@dataclass(frozen=True)
class Participant:
    """One derived participant: the user, plus how they were derived from the anchor."""

    user_id: str
    email: str
    name: str | None
    role: str


def _display_name(user: User) -> str | None:
    """Return ``"First Last"`` (either part optional), or ``None`` when the user has neither."""
    parts = [p for p in (user.first_name, user.last_name) if p]
    return " ".join(parts) if parts else None


def _participant(user: User | None, role: str) -> Participant | None:
    """Build a :class:`Participant` from a resolved user, or ``None`` when there is no account."""
    if user is None:
        return None
    return Participant(
        user_id=user.id, email=user.email, name=_display_name(user), role=role
    )


def _derive_for_announcement(db: Session, anchor_id: str) -> list[Participant]:
    """Return an announcement thread's **stored** audience (Issue #112).

    Unlike the record anchors, an announcement has no single live record to derive from — its
    audience (the sender and each resolved recipient) was authorised and stored once at send time.
    The thread's own id is its ``anchor_id`` (set at creation), so the stored rows are read directly
    by ``thread_id``. A thread with no stored rows yet resolves to an empty list rather than raising.
    """
    rows = (
        db.execute(
            select(MessageParticipant).where(MessageParticipant.thread_id == anchor_id)
        )
        .scalars()
        .all()
    )
    out: list[Participant] = []
    for row in rows:
        participant = _participant(db.get(User, row.user_id), row.role)
        if participant is not None:
            out.append(participant)
    return out


def _derive_for_peer(db: Session, anchor_id: str) -> list[Participant]:
    """Return a peer thread's two **stored** participants (Issue #126).

    A tenant↔tenant thread has no live record to derive from — its two participants are stored once
    at creation and read back by ``thread_id`` (which is the ``anchor_id``), exactly like an
    announcement's audience. Reusing the stored-participant read keeps the participation gate
    (:func:`ensure_participant`) working unchanged: each peer sees only the threads they are in.
    """
    return _derive_for_announcement(db, anchor_id)


# How a thread's participants are derived, by anchor type. The two below need no domain records:
# an announcement's audience and a peer thread's two sides are *stored* at creation, so they are
# read back rather than recomputed.
#
# A **record anchor** — a thread about an order, a ticket, a case — is the interesting kind, and
# yours to add: write `_derive_for_<record>(db, anchor_id) -> list[Participant]` returning who is
# on that record right now, add a `MessageAnchorType` member, and add the pair here. Deriving from
# live state on every call is the point: revoke someone's role on the record and their access to
# the conversation about it goes with it, without a recipient list to keep in sync.
_DERIVERS = {
    MessageAnchorType.ANNOUNCEMENT: _derive_for_announcement,
    MessageAnchorType.PEER: _derive_for_peer,
}


def derive_participants(
    db: Session, anchor_type: MessageAnchorType, anchor_id: str
) -> list[Participant]:
    """Return the participants of the conversation anchored to ``(anchor_type, anchor_id)``.

    For record anchors this is computed from live state on every call; for an ``announcement`` it
    reads the audience stored at send time (Issue #112). Either way the result is de-duplicated by
    user id (a person who is both, say, tenant and owner appears once, under the first role derived).

    Raises:
        MessageAnchorNotFoundError: the anchored record does not exist.
    """
    derived = _DERIVERS[anchor_type](db, anchor_id)
    seen: set[str] = set()
    unique: list[Participant] = []
    for participant in derived:
        if participant.user_id in seen:
            continue
        seen.add(participant.user_id)
        unique.append(participant)
    return unique


def is_participant(db: Session, thread: MessageThread, user_id: str) -> bool:
    """Return whether ``user_id`` is currently a participant of ``thread``."""
    anchor_type = MessageAnchorType(thread.anchor_type)
    return any(
        p.user_id == user_id
        for p in derive_participants(db, anchor_type, thread.anchor_id)
    )


def ensure_participant(db: Session, thread: MessageThread, user_id: str) -> None:
    """Raise :class:`NotAThreadParticipantError` unless ``user_id`` participates in ``thread``."""
    if not is_participant(db, thread, user_id):
        raise NotAThreadParticipantError(thread.id)


# --------------------------------------------------------------------------------------
# Threads and messages.
# --------------------------------------------------------------------------------------


def get_or_create_thread(
    db: Session,
    *,
    anchor_type: MessageAnchorType,
    anchor_id: str,
    actor_user_id: str,
    subject: str | None = None,
    require_participant: bool = True,
) -> MessageThread:
    """Return the canonical thread for an anchor, creating it if absent (idempotent).

    ``actor_user_id`` must be a participant of the anchor — deriving participants also validates
    the anchored record exists. A ``subject`` is applied only when creating; opening an existing
    thread returns it unchanged.

    ``require_participant`` (default true) enforces that check. The manager console's Compose
    (Issue #132 follow-up) sets it false, after the router has authorised the caller by their
    management grant, so a manager may start a conversation on a record they oversee without
    already being a derived participant of it — mirroring the bypass :func:`post_message` already
    grants managers replying to an existing thread.

    Raises:
        MessageAnchorNotFoundError: the anchored record does not exist.
        NotAThreadParticipantError: the actor is not a participant of the anchor.
    """
    participants = derive_participants(db, anchor_type, anchor_id)
    if require_participant and not any(
        p.user_id == actor_user_id for p in participants
    ):
        # Reuse the thread-scoped error; the anchor's own thread id is not known yet.
        raise NotAThreadParticipantError(f"{anchor_type.value}:{anchor_id}")

    thread = db.execute(
        select(MessageThread).where(
            MessageThread.anchor_type == anchor_type.value,
            MessageThread.anchor_id == anchor_id,
        )
    ).scalar_one_or_none()
    if thread is not None:
        return thread

    thread = MessageThread(
        anchor_type=anchor_type.value,
        anchor_id=anchor_id,
        subject=subject,
    )
    db.add(thread)
    db.flush()
    return thread


def create_announcement_thread(
    db: Session,
    *,
    sender: User,
    audience_type: AudienceType,
    audience_ref: str | None,
    subject: str | None,
) -> MessageThread:
    """Resolve an audience, then open a fresh announcement thread with that audience stored.

    The audience is authorised and resolved by :func:`audience.resolve_recipients` (RBAC +
    ownership) *before* any thread is created — so an unauthorised or empty audience never leaves a
    stray thread behind. The resolved recipients and the sender are stored as
    :class:`~src.database.models.message_participant.MessageParticipant` rows (the broadcast's
    audience is fixed at send time, not re-derived). Each announcement is its own thread — there is
    no get-or-create — so its ``anchor_id`` is set to the thread's own id, which is what
    :func:`_derive_for_announcement` reads the stored audience by.

    Raises:
        AudienceNotAuthorisedError: the sender may not target this audience (mapped to 403).
        EmptyAudienceError: the audience resolves to no reachable recipient (mapped to 409).
    """
    recipient_ids = audience_service.resolve_recipients(
        db,
        sender=sender,
        audience_type=audience_type,
        audience_ref=audience_ref,
    )

    thread = MessageThread(
        anchor_type=MessageAnchorType.ANNOUNCEMENT.value,
        anchor_id=str(uuid4()),  # replaced with the thread's own id below
        subject=subject,
        audience_type=audience_type.value,
        audience_ref=audience_ref,
    )
    db.add(thread)
    db.flush()
    # Anchor the announcement to itself so the stored audience is read by thread id (a record
    # anchor points at another table; a broadcast points at its own participant rows).
    thread.anchor_id = thread.id

    db.add(MessageParticipant(thread_id=thread.id, user_id=sender.id, role=ROLE_SENDER))
    for recipient_id in recipient_ids:
        db.add(
            MessageParticipant(
                thread_id=thread.id, user_id=recipient_id, role=ROLE_RECIPIENT
            )
        )
    db.flush()
    return thread


def get_or_create_peer_thread(
    db: Session,
    *,
    sender: User,
    recipient_user_id: str,
    subject: str | None = None,
) -> MessageThread:
    """Return the direct tenant↔tenant thread between ``sender`` and a neighbour, creating it once.

    Authorised by a server-checked **co-occupancy** relationship: the two must share a property
    (:func:`src.modules.messaging.peers.ensure_can_reach`). The RBAC ``communications.messages``
    grant is enforced separately by the caller (the API dependency / web route). The pairing is
    idempotent — a second message to the same neighbour reuses the existing thread rather than
    opening a duplicate — and the two participants are stored once (both as ``tenant``), with the
    thread anchored to its own id so :func:`_derive_for_peer` reads them back.

    Raises:
        PeerNotReachableError: the recipient is not a reachable neighbour (mapped to 403).
    """
    peers.ensure_can_reach(db, sender.id, recipient_user_id)

    # Reuse the canonical thread for this unordered pair, if one already exists.
    sender_threads = select(MessageParticipant.thread_id).where(
        MessageParticipant.user_id == sender.id
    )
    recipient_threads = select(MessageParticipant.thread_id).where(
        MessageParticipant.user_id == recipient_user_id
    )
    existing = db.execute(
        select(MessageThread)
        .where(
            MessageThread.anchor_type == MessageAnchorType.PEER.value,
            MessageThread.id.in_(sender_threads),
            MessageThread.id.in_(recipient_threads),
        )
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    thread = MessageThread(
        anchor_type=MessageAnchorType.PEER.value,
        anchor_id=str(uuid4()),  # replaced with the thread's own id below
        subject=subject,
    )
    db.add(thread)
    db.flush()
    # Anchor the peer thread to itself so its two stored participants are read by thread id.
    thread.anchor_id = thread.id
    db.add(
        MessageParticipant(thread_id=thread.id, user_id=sender.id, role=ROLE_RECIPIENT)
    )
    db.add(
        MessageParticipant(
            thread_id=thread.id, user_id=recipient_user_id, role=ROLE_RECIPIENT
        )
    )
    db.flush()
    return thread


def get_thread(db: Session, thread_id: str) -> MessageThread | None:
    """Return one thread by id, or ``None`` when it does not exist."""
    return db.get(MessageThread, thread_id)


def list_threads_for_user(db: Session, user_id: str) -> list[MessageThread]:
    """Return every non-deleted thread ``user_id`` currently participates in, newest activity first.

    Participation is recomputed per thread from live anchor state, so a thread a user has just
    lost access to (e.g. a vendor unassigned from a work order) drops out here too. A manager's
    soft-delete (Issue #132 follow-up) also drops a thread out of a participant's own view — it is
    a console housekeeping action, not something a non-manager can undo.
    """
    threads = (
        db.execute(select(MessageThread).where(MessageThread.is_deleted.is_(False)))
        .scalars()
        .all()
    )
    mine = [t for t in threads if is_participant(db, t, user_id)]
    mine.sort(key=lambda t: last_message_at(db, t.id) or t.created_at, reverse=True)
    return mine


def list_all_threads(
    db: Session,
    *,
    offset: int = 0,
    limit: int = 20,
    authored_by: str | None = None,
    deleted: bool = False,
    participant_user_id: str | None = None,
) -> tuple[list[MessageThread], int]:
    """Return a page of threads (newest activity first) and the total, for the managed console.

    The managed console (Issue #87) surfaces threads for the records a manager oversees, not only
    the ones they were explicitly added to. Ordering matches :func:`list_threads_for_user`
    (most-recently-active first); ``total`` is the full count.

    ``deleted`` selects the soft-deleted set instead of the live one (the console's Deleted tab,
    Issue #132 follow-up). ``authored_by`` narrows to threads the given user has written at least
    one message in — the console's Sent tab; ordinary threads carry no stored "direction," so "you
    wrote in it" is the most honest definition available, not a stored sender flag.

    ``participant_user_id`` is the **scope narrowing** (Issue #157, M28): the caller's own
    participation, applied when their grant on this console resolves to
    :data:`~src.commons.enums.GrantScope.OWN` rather than ``business``. Left ``None`` — a
    business-scoped caller — the listing spans the whole business exactly as before. Participation
    is recomputed live per thread by :func:`is_participant`, the same definition
    :func:`list_threads_for_user` uses, so the narrowed set is that function's set, paginated and
    foldered to this endpoint's shape. There is deliberately **no** participant-scoped Deleted
    view: a soft-delete is a console housekeeping action a participant cannot see or undo
    (:func:`list_threads_for_user` never returns one), so that folder resolves empty for a narrowed
    caller rather than newly exposing deleted threads.
    """
    if participant_user_id is not None and deleted:
        return [], 0
    stmt = select(MessageThread).where(MessageThread.is_deleted.is_(deleted))
    if authored_by is not None:
        stmt = stmt.where(
            MessageThread.id.in_(
                select(Message.thread_id).where(Message.author_id == authored_by)
            )
        )
    threads = list(db.execute(stmt).scalars().all())
    if participant_user_id is not None:
        threads = [t for t in threads if is_participant(db, t, participant_user_id)]
    threads.sort(key=lambda t: last_message_at(db, t.id) or t.created_at, reverse=True)
    total = len(threads)
    return threads[offset : offset + limit], total


def list_announcement_threads(
    db: Session,
    *,
    user_id: str,
    role: str,
    deleted: bool = False,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[MessageThread], int]:
    """Return a page of the caller's announcement threads for one console tab, newest first.

    Unlike a record-anchored thread, an announcement's participants are stored rows with a real
    ``role`` (Issue #112), so — unlike ordinary Messages' "sent" (§:func:`list_all_threads`, which
    has to infer direction from who wrote a message) — Inbox (``role='recipient'``) and Sent
    (``role='sender'``) read straight off the data (Issue #132 follow-up).
    """
    stmt = (
        select(MessageThread)
        .join(MessageParticipant, MessageParticipant.thread_id == MessageThread.id)
        .where(
            MessageThread.anchor_type == MessageAnchorType.ANNOUNCEMENT.value,
            MessageParticipant.user_id == user_id,
            MessageParticipant.role == role,
            MessageThread.is_deleted.is_(deleted),
        )
    )
    threads = list(db.execute(stmt).scalars().all())
    threads.sort(key=lambda t: t.created_at, reverse=True)
    total = len(threads)
    return threads[offset : offset + limit], total


def soft_delete_thread(db: Session, thread: MessageThread) -> None:
    """Move a thread to the console's Deleted tab (Issue #132 follow-up). Reversible — see restore."""
    thread.is_deleted = True


def restore_thread(db: Session, thread: MessageThread) -> None:
    """Undo :func:`soft_delete_thread`, returning a thread to its inbox/sent listing."""
    thread.is_deleted = False


def post_message(
    db: Session,
    thread: MessageThread,
    *,
    author_id: str,
    body: str,
    require_participant: bool = True,
) -> Message:
    """Append one message authored by ``author_id`` to ``thread``.

    The body is stored verbatim; escaping happens at render time. The author's own read receipt is
    recorded immediately (you have read what you just wrote), so their unread count is unaffected.

    ``require_participant`` (default true) enforces that the author participates in the thread. The
    manager console (Issue #87) sets it false only after the router has authorised the caller by
    their management grant, so a manager may reply to a thread they oversee without being a derived
    participant; every other caller stays participant-gated.
    """
    if require_participant:
        ensure_participant(db, thread, author_id)
    message = Message(thread_id=thread.id, author_id=author_id, body=body)
    db.add(message)
    db.flush()
    _record_receipt(db, message_id=message.id, user_id=author_id, now=_now())
    return message


def list_messages(db: Session, thread_id: str) -> list[Message]:
    """Return a thread's messages in chronological order."""
    return list(
        db.execute(
            select(Message)
            .where(Message.thread_id == thread_id)
            .order_by(Message.created_at.asc())
        )
        .scalars()
        .all()
    )


def last_message_at(db: Session, thread_id: str) -> datetime | None:
    """Return when the thread's most recent message was posted, or ``None`` when empty."""
    return db.execute(
        select(func.max(Message.created_at)).where(Message.thread_id == thread_id)
    ).scalar_one_or_none()


# --------------------------------------------------------------------------------------
# Read receipts.
# --------------------------------------------------------------------------------------


def _record_receipt(
    db: Session, *, message_id: str, user_id: str, now: datetime
) -> bool:
    """Insert a read receipt for ``(message_id, user_id)`` unless one exists. True if inserted."""
    existing = db.execute(
        select(MessageReceipt.id).where(
            MessageReceipt.message_id == message_id,
            MessageReceipt.user_id == user_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    db.add(MessageReceipt(message_id=message_id, user_id=user_id, read_at=now))
    return True


def unread_count(db: Session, thread_id: str, user_id: str) -> int:
    """Return how many of ``thread_id``'s messages ``user_id`` has not yet read."""
    read_subq = select(MessageReceipt.message_id).where(
        MessageReceipt.user_id == user_id
    )
    return (
        db.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.thread_id == thread_id,
                Message.id.not_in(read_subq),
            )
        ).scalar_one()
        or 0
    )


def total_unread_for_user(db: Session, user_id: str) -> int:
    """Return the user's total unread message count across every thread they participate in.

    The single number the M19 notification bell (Issue #113) reads: the sum of per-thread unread
    counts over the user's live inbox. Participation is recomputed per thread from live anchor state
    (or, for an announcement, its stored audience), so a thread the user has just lost access to
    contributes nothing — the badge only ever counts messages they may actually read.
    """
    return sum(
        unread_count(db, thread.id, user_id)
        for thread in list_threads_for_user(db, user_id)
    )


def total_unread_for_managed_inbox(db: Session, user_id: str) -> int:
    """Return the sum of unread counts across every live thread in the manager console's Inbox.

    Distinct from :func:`total_unread_for_user` (participant-only, the bell): this is manager-wide,
    matching every non-deleted thread ``GET /managed-threads?folder=inbox`` lists — the Messages
    console's own Inbox unread total (Issue #132 follow-up), not just threads the caller happens to
    participate in.
    """
    threads = list(
        db.execute(select(MessageThread).where(MessageThread.is_deleted.is_(False)))
        .scalars()
        .all()
    )
    return sum(unread_count(db, thread.id, user_id) for thread in threads)


def mark_thread_read(
    db: Session,
    thread: MessageThread,
    *,
    user_id: str,
    now: datetime | None = None,
    require_participant: bool = True,
) -> int:
    """Mark every message in ``thread`` read by ``user_id``; return how many were newly read.

    Idempotent: an already-read message is skipped, so calling twice marks nothing the second
    time. ``require_participant`` (default true) keeps the action participant-gated; the manager
    console (Issue #87) sets it false only after the router has authorised the caller as a manager.
    """
    if require_participant:
        ensure_participant(db, thread, user_id)
    now = now or _now()
    marked = 0
    for message in list_messages(db, thread.id):
        if _record_receipt(db, message_id=message.id, user_id=user_id, now=now):
            marked += 1
    db.flush()
    return marked


# --------------------------------------------------------------------------------------
# Notifications — one email per other participant, post-commit and best-effort.
# --------------------------------------------------------------------------------------


def _preferences_allow_message_email(db: Session, participant: Participant) -> bool:
    """Whether ``participant`` wants email for a new message — honours preferences (Issue #72).

    Delegates to the notification preference layer: a participant who has turned the ``messages``
    category off (or onto another channel) is skipped here, so a suppressed message is never even
    enqueued. A deferred (quiet-hours) message is still "allowed" — the service queues it for later.
    Fail-open: a lookup error opts the participant in rather than dropping a message.
    """
    try:
        return preferences.email_allowed(
            db, participant.email, NotificationTemplate.NEW_MESSAGE
        )
    except Exception:  # a preferences error must never silence a legitimate message
        logger.exception(
            "Message-preference check failed for %s — notifying anyway",
            participant.email,
        )
        return True


def notify_new_message(
    db: Session,
    thread: MessageThread,
    message: Message,
    *,
    author_id: str,
) -> None:
    """Email the thread's *other* participants that a new message was posted — once each.

    De-duplicated by the participant derivation (a person appears once) and by excluding the
    author. Best-effort and intended to run post-commit: a send failure is logged and swallowed so
    it can never turn a committed message into an error. Delivery/retry is owned by the notification
    service (Issue #67); ``send`` no-ops in development with no SMTP configured.
    """
    anchor_type = MessageAnchorType(thread.anchor_type)
    try:
        recipients = [
            p
            for p in derive_participants(db, anchor_type, thread.anchor_id)
            if p.user_id != author_id and _preferences_allow_message_email(db, p)
        ]
    except MessageAnchorNotFoundError:
        # The anchor vanished between posting and notifying; nothing to notify about.
        return

    subject_line = thread.subject or _anchor_label(anchor_type)
    for recipient in recipients:
        text = (
            f"Hi{(' ' + recipient.name) if recipient.name else ''},\n\n"
            f'There is a new message on "{subject_line}".\n\n'
            "Sign in to your portal to read and reply."
        )
        try:
            email_send.send(
                to=recipient.email,
                subject=f"New message: {subject_line}",
                text=text,
                template=NotificationTemplate.NEW_MESSAGE,
            )
        except Exception:  # best-effort: a mail failure never undoes a posted message
            logger.exception(
                "New-message notification failed for %s on thread %s",
                recipient.email,
                thread.id,
            )


def _anchor_label(anchor_type: MessageAnchorType) -> str:
    """A human label for a subject line when a thread has no explicit subject."""
    return {
        MessageAnchorType.ANNOUNCEMENT: "an announcement",
        MessageAnchorType.PEER: "a peer",
    }.get(anchor_type, "a conversation")
