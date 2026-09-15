"""Notification model: the delivery ledger for transactional email + SMS (Issue #67).

Before this, transactional mail was sent from several modules with no shared record of what
went out or whether it landed. This table is the memory of the one notification service that
now owns delivery: **one row per message**, carrying the channel it went out on, which template
it was, who it was addressed to, and — crucially — a *status* that moves from ``queued`` through
``sent`` to ``delivered`` (or ``failed`` → ``dead`` when a provider keeps rejecting it), so a
message is never lost silently.

The row is what makes retry-with-backoff and dead-lettering durable across restarts and safe on
more than one instance: the retry sweep (``src.core.scheduler``) selects rows that are due
(``status`` in ``queued``/``failed`` and ``next_attempt_at`` in the past), re-attempts delivery,
bumps ``attempts`` with an exponential ``next_attempt_at``, and flips the row to ``dead`` once
``attempts`` reaches ``max_attempts``. A delivery-status webhook maps a provider's
``provider_message_id`` back to its row and advances it to ``delivered``.

``payload`` snapshots the render context (recipient name, unit label, links, …) so a queued
message can be re-rendered by the template registry on a later attempt without re-reading the
originating domain rows, which may have since changed. ``subject`` is stored for email only (SMS
has none). ``created_at`` / ``next_attempt_at`` / ``sent_at`` / ``delivered_at`` / ``failed_at``
are in Africa/Johannesburg terms, the project's business timezone.

**Patients (Issue 63).** A patient has no account and no email address, so a patient notification
is addressed by ``patient_id`` and reaches them on whichever transport can: ``recipient`` is then the
transport's own address (a phone number, a WhatsApp id, a push subscription id). ``event`` names what
happened to their ticket, ``site_id`` which clinic sent it (so cost is reportable per clinic),
``cost``/``cost_currency`` what the provider charged, ``dedupe_key`` makes one queue event one message
however often it is replayed, and ``fallback_of_id`` links a row to the free transport that failed
before it, so the ledger reads as the chain that was actually tried.

No new RBAC resource is introduced: the admin status-query endpoint reuses the ``logs`` READ verb
(operational data, like the S3 log viewer). Carries TimestampMixin (created_at/modified_at); as an
append-only delivery ledger it takes neither the active nor the soft-delete mixin.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema, NotificationStatus
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

SCHEMA = DbSchema.CLINICQ.value

#: Room for ``<ticket id>:<event>:<moment>``, the longest key the queue builds.
DEDUPE_KEY_LENGTH = 160


class Notification(Base, TimestampMixin):
    """One message (email, SMS, web push or WhatsApp) and its delivery status."""

    __tablename__ = "notification"
    __table_args__ = (
        # The retry sweep selects due rows by status + next_attempt_at; index both so it stays
        # cheap as the ledger grows.
        Index(
            "ix_clinicq_notification_status_next_attempt_at",
            "status",
            "next_attempt_at",
        ),
        # A delivery-status webhook looks a row up by the provider's message id.
        Index(
            "ix_clinicq_notification_provider_message_id",
            "provider_message_id",
        ),
        # A patient's messages, newest first (the ticket page, the preference gate, erasure).
        Index("ix_clinicq_notification_patient_id", "patient_id", "created_at"),
        # Cost and delivery rate per clinic per day (Issues 65, 71 and the M12 reports).
        Index("ix_clinicq_notification_site_id", "site_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    channel: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )
    """Delivery channel — one of :class:`NotificationChannel`, stored as text per the project's
    enum-as-text convention."""
    template_key: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    """Which transactional template this message is — one of :class:`NotificationTemplate`."""
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    """Destination address: an email address, an E.164 number (SMS), a WhatsApp id, or a push
    subscription id (web push)."""
    patient_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.patient.id", ondelete="SET NULL"),
        nullable=True,
    )
    """The patient this message is for (Issue 63); ``NULL`` for an account holder's message."""
    site_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.site.id", ondelete="SET NULL"),
        nullable=True,
    )
    """The clinic on whose behalf it was sent, so its cost is reportable per clinic (Issue 63)."""
    event: Mapped[str | None] = mapped_column(String(20), nullable=True)
    """What happened to the patient's ticket: one of :class:`~src.commons.enums.PatientEvent`."""
    dedupe_key: Mapped[str | None] = mapped_column(
        String(DEDUPE_KEY_LENGTH), nullable=True, unique=True
    )
    """One queue event, one message: a replay of the same event finds this row and sends nothing.
    Only the first row of a fallback chain carries it."""
    fallback_of_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.notification.id", ondelete="SET NULL"),
        nullable=True,
    )
    """The row on the transport tried before this one, when this row is its fallback (Issue 63)."""
    template_version_id: Mapped[str | None] = mapped_column(
        String(36),
        # Named by hand: the convention's name would be 66 characters, past PostgreSQL's 63.
        ForeignKey(
            f"{SCHEMA}.notification_template_version.id",
            ondelete="RESTRICT",
            name="fk_notification_template_version_id",
        ),
        nullable=True,
    )
    """The exact words this message was rendered from (Issue 66): an immutable template version, so the
    message can be reproduced later and a retry says what the first attempt said."""
    language: Mapped[str | None] = mapped_column(String(5), nullable=True)
    """The language the message went out in (a :class:`~src.commons.enums.BoardLanguage` code)."""
    cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    """What the provider charged for the message, in ``cost_currency``; ``0`` for a free transport,
    ``NULL`` until a provider has accepted it."""
    cost_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    """ISO 4217 currency of ``cost`` (``ZAR``)."""
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """Email subject line; ``NULL`` for SMS, which has no subject."""
    status: Mapped[str] = mapped_column(
        String(12),
        nullable=False,
        default=NotificationStatus.QUEUED.value,
    )
    """Delivery lifecycle state — one of :class:`NotificationStatus` (``queued`` on creation)."""
    provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    """Provider that handled the message (e.g. ``smtp``, an SMS gateway id); ``NULL`` until sent."""
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """The provider's id for this message, used to correlate a delivery-status webhook back
    to this row; ``NULL`` until a provider accepts it."""
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    """Render context snapshotted at enqueue time, so a queued message can be re-rendered on a
    later retry without re-reading (possibly since-changed) domain rows."""
    attempts: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
    )
    """How many delivery attempts have been made so far."""
    max_attempts: Mapped[int] = mapped_column(
        nullable=False,
    )
    """Attempt budget; once ``attempts`` reaches this the row is dead-lettered (``dead``)."""
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    """The most recent failure's message, kept for operators triaging a dead-lettered row."""
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    """When the row next becomes eligible for a retry (Africa/Johannesburg); ``NULL`` once the
    row reaches a terminal state (``sent``/``delivered``/``dead``/``suppressed``)."""
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    """When a provider accepted the message (Africa/Johannesburg); ``NULL`` until sent."""
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    """When a provider confirmed delivery via webhook (Africa/Johannesburg); ``NULL`` otherwise."""
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    """When the last delivery attempt failed (Africa/Johannesburg); ``NULL`` while never-failed."""
