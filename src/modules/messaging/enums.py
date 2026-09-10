"""Enumerations for the in-app messaging module (Issue #68).

A thread is always *anchored* to a domain record, and that anchor is what its participants —
and therefore who may read and post — are derived from. :class:`MessageAnchorType` names the
kind of record a thread hangs off; the row also carries the anchor's id (``anchor_id``). Keeping
the kind in an enum (rather than a free string) means the participant-derivation switch in
``service`` is exhaustive and a new anchor kind is a compile-time, one-place change.
"""

from __future__ import annotations

from enum import StrEnum


class MessageAnchorType(StrEnum):
    """The kind of thing a :class:`~src.database.models.message_thread.MessageThread` is anchored
    to. Stored in ``message_thread.anchor_type``.

    The anchor is the single source of truth for a thread's participants (Issue #68): a message
    about a thing lives on the *record* it concerns, so the history is where the next person will
    look, and access follows the record rather than a hand-kept recipient list.

    * ``ANNOUNCEMENT`` — a broadcast to a resolved *audience* (Issue #112). Its recipients cannot
      be re-derived from one live record, so the audience is **resolved once at send time** and
      stored as explicit participant rows (see
      :class:`~src.database.models.message_participant.MessageParticipant`). The ``anchor_id`` is a
      synthetic id minted for the broadcast, and the thread records the :class:`AudienceType` it
      was resolved from, for display and audit.
    * ``PEER`` — a direct conversation between two people. Like an announcement it has no live
      record to derive from — its two participants are **stored** and the ``anchor_id`` is the
      thread's own id — but unlike a broadcast it is a get-or-create between exactly two people,
      authorised by :mod:`src.modules.messaging.peers`. Neither party is a "sender": both rows
      carry the same role.

    **Record anchors are yours to add.** One member per kind of record a conversation can be about
    — an order, a ticket, a case — plus a deriver in :data:`~src.modules.messaging.service._DERIVERS`
    that returns who is on that record *right now*. Deriving from live state on every call is what
    makes access follow the record: take someone off it and the conversation about it closes to
    them, with no recipient list to remember to update.
    """

    ANNOUNCEMENT = "announcement"
    PEER = "peer"


class AudienceType(StrEnum):
    """How an announcement's recipient set is resolved from the sender's authority (Issue #112).

    An audience is never an enumerated recipient list the sender hands in — it is a *rule* the
    service resolves server-side to concrete users, authorised so a sender can never target people
    outside their scope. Stored on ``message_thread.audience_type`` for the broadcast threads.

    * ``GLOBAL`` — every user. Gated by the ``communications.announcements:broadcast_global``
      named action, which is granted to nobody until you grant it.

    Add a member per audience your domain has, and resolve it in
    :func:`src.modules.messaging.audience.resolve_recipients` — that function's docstring has the
    two rules an audience must keep.
    """

    GLOBAL = "global"


class AlertSource(StrEnum):
    """Where an :class:`~src.database.models.alert.Alert` originates.

    * ``STAFF`` — a person composed it: an urgent broadcast a manager/admin sends to a resolved
      :class:`AudienceType` (the same audience machinery announcements use), flagged by
      :class:`AlertSeverity`. Has an ``author_id`` and supports drafts (Sent/Drafts folders).
    * ``SYSTEM`` — the platform raised it from a domain condition (rent overdue, a lease expiring,
      a failed payment). No author; it links the record it concerns via ``entity_type``/``entity_id``
      and lands in the recipient's Inbox to be acknowledged, never composed or "sent" by a user.
    """

    STAFF = "staff"
    SYSTEM = "system"


class AlertSeverity(StrEnum):
    """How urgent an alert is — orders the Inbox and drives its badge colour.

    ``INFO`` for awareness, ``WARNING`` for something that needs attention soon, ``CRITICAL`` for
    something requiring immediate action (a failed payment, a lease lapsing today).
    """

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertKind(StrEnum):
    """What an alert is about — the trigger for a ``SYSTEM`` alert, or ``CUSTOM`` for a staff one.

    The ``SYSTEM`` kinds are the conditions the generator (Issue: alerts) raises from live domain
    state; each new automated alert is a one-place addition here so the generator switch stays
    exhaustive. ``CUSTOM`` is every :data:`AlertSource.STAFF` alert (free-form subject/body).
    """

    CUSTOM = "custom"
    RENT_OVERDUE = "rent_overdue"
    LEASE_EXPIRING = "lease_expiring"
    PAYMENT_FAILED = "payment_failed"
    LEASE_TERMINATION_REQUESTED = "lease_termination_requested"
    MAINTENANCE_UNASSIGNED = "maintenance_unassigned"
