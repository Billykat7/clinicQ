"""StripeEvent model: the idempotency log of processed Stripe webhook events (Issue #76).

Stripe delivers a webhook *at least once*: a network hiccup, a slow ``200``, or a manual replay
from the dashboard all resend the same event, and the same ``payment_intent.succeeded`` arriving
twice must never credit a lease twice. This table is the guard. Every event the gateway has
already applied is recorded here keyed on Stripe's own event id, and processing begins by claiming
that id: if the row already exists the event is a replay and is acknowledged without touching the
ledger again.

The id is Stripe's ``evt_…`` event id used verbatim as the primary key, so the uniqueness of a
processed event is enforced by the **database itself** — two concurrent deliveries of one event
cannot both insert it. The row is written for every handled event (success, failure and refund)
inside the same transaction that applies the event's ledger effect, so an event is marked
processed *if and only if* its effect committed.

The table is append-only: a processed event is a historical fact and is never updated or deleted.
"""

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin


class StripeEvent(Base, TimestampMixin):
    """A processed Stripe webhook event: its id (the idempotency key), type and payment intent.

    Append-only — never updated or deleted. Carries only
    :class:`~src.database.models.mixins.TimestampMixin` (``created_at`` is when the event was
    processed); it omits the active/soft-delete mixins because a processed event is never toggled
    or soft-removed.
    """

    __tablename__ = "stripe_event"
    __table_args__ = (
        # Reconciliation and support look an event up by the payment intent it concerned.
        Index("ix_clinicq_stripe_event_payment_intent_id", "payment_intent_id"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    """Stripe's ``evt_…`` event id, used verbatim as the primary key. Being the primary key makes
    the "process each event once" rule a guarantee of the database: a replay cannot insert it a
    second time."""
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    """The Stripe event ``type``; one of :class:`~src.modules.payments.enums.StripeEventType` for
    the events the gateway acts on."""
    payment_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """The ``pi_…`` PaymentIntent the event concerned, when the event carries one; null otherwise.
    Indexed via ``ix_clinicq_stripe_event_payment_intent_id`` for reconciliation lookups."""
