"""EsignEvent model: the durable record that one provider webhook was processed (Issue #71).

A signature provider delivers terminal outcomes (completed / declined / expired) over webhooks, and
a webhook is delivered *at least once* — a provider retries until it gets a 2xx, so the same event
can arrive twice. This table is the handler's *memory*: one row per provider event id records that
the callback was already applied, so processing is **idempotent on replay** — a re-delivered event
never re-runs its side effects (storing the signed document twice, flipping a terminal status
again).

That guarantee is enforced by the **database itself**, not merely by application code: a
``UNIQUE (provider_event_id)`` constraint means a second attempt to record the same event — whether
a provider retry or a concurrent instance — fails on insert. The handler records the event *before*
it applies the outcome (see :func:`src.modules.documents.esign_service.handle_webhook`), so the
insert is the idempotency point: a duplicate trips the constraint and is skipped rather than
re-processed.

``event_type`` is the wire value the provider sent (an :class:`~src.commons.enums.EsignWebhookEvent`
for the ones we act on). This is an append-only ledger, so it carries only TimestampMixin — an
"event processed" fact is never deactivated or deleted.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from src.database.models.esign_envelope import EsignEnvelope


class EsignEvent(Base, TimestampMixin):
    """One record that a provider webhook event was processed for an envelope (idempotency key)."""

    __tablename__ = "esign_event"
    __table_args__ = (
        # The idempotency guarantee, enforced in the database: an event is processed at most once.
        # A provider retry (webhooks are delivered at-least-once), or a second instance handling the
        # same callback, trips this on insert instead of re-applying the outcome. Explicitly named
        # so the migration and autogenerate agree.
        UniqueConstraint(
            "provider_event_id",
            name="uq_esign_event_provider_event_id",
        ),
        # Events are read per envelope, so the FK column is indexed as a composite-friendly single.
        Index(
            "ix_clinicq_esign_event_envelope_id",
            "envelope_id",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    envelope_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("esign_envelope.id", ondelete="CASCADE"),
        nullable=False,
    )
    """The envelope the event applies to; ``CASCADE`` so an envelope's event ledger goes with it.
    Indexed (``ix_clinicq_esign_event_envelope_id``) because events are read per envelope."""
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    """The provider's unique id for the delivery/event — the natural key of "already processed".
    See the unique constraint that makes replay a no-op."""
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    """The wire event type the provider sent; an :class:`~src.commons.enums.EsignWebhookEvent` for
    the terminal events the handler acts on."""
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    """When the webhook was received and recorded, in Africa/Johannesburg terms (the business
    timezone)."""

    envelope: Mapped[EsignEnvelope] = relationship("EsignEnvelope")
