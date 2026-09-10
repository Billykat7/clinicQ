"""PaystackEvent model: the idempotency log of processed Paystack webhook events (Issue #79).

Paystack retries a webhook until it gets a ``200``: a network hiccup, a slow response, or a manual
resend all deliver the same event again, and the same ``charge.success`` arriving twice must never
credit a lease twice. This table is the guard. Every event the gateway has already applied is
recorded here keyed on an idempotency key derived from the event, and processing begins by claiming
that key: if the row already exists the event is a replay and is acknowledged without touching the
ledger again.

Paystack (unlike Stripe) puts no unique event id on the envelope, so the key is the transaction
**reference** namespaced by the event type — ``"{event}:{reference}"``. Namespacing by type is what
lets ``charge.success`` and a later ``refund.processed`` on the *same* reference be two distinct
events (each applied once) rather than the refund being mistaken for a replay of the charge. The key
is the primary key, so uniqueness of a processed event is enforced by the **database itself** — two
concurrent deliveries of one event cannot both insert it. The row is written for every handled event
inside the same transaction that applies the event's ledger effect, so an event is marked processed
*if and only if* its effect committed.

The table is append-only: a processed event is a historical fact and is never updated or deleted.
"""

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin


class PaystackEvent(Base, TimestampMixin):
    """A processed Paystack webhook event: its idempotency key, type and transaction reference.

    Append-only — never updated or deleted. Carries only
    :class:`~src.database.models.mixins.TimestampMixin` (``created_at`` is when the event was
    processed); it omits the active/soft-delete mixins because a processed event is never toggled
    or soft-removed.
    """

    __tablename__ = "paystack_event"
    __table_args__ = (
        # Reconciliation and support look an event up by the transaction it concerned.
        Index("ix_clinicq_paystack_event_reference", "reference"),
    )

    id: Mapped[str] = mapped_column(String(320), primary_key=True)
    """The idempotency key ``"{event}:{reference}"``. Being the primary key makes the "process each
    event once" rule a guarantee of the database: a replay cannot insert it a second time, while a
    different event type on the same reference (a refund after a charge) has a distinct key."""
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    """The Paystack ``event`` string; one of :class:`~src.modules.payments.enums.PaystackEventType`
    for the events the gateway acts on."""
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """The transaction reference the event concerned, when it carries one; null otherwise. Indexed
    via ``ix_clinicq_paystack_event_reference`` for reconciliation lookups."""
