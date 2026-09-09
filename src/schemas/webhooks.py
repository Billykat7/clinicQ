"""Acknowledgements returned to an inbound payment-gateway webhook.

Deliberately minimal. The gateway is not a client of this API — it wants to know whether to retry,
and nothing else — so the body says only that the call was received and what became of the event.
Anything more would be telling an unauthenticated caller about the state of the system.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StripeWebhookAck(BaseModel):
    """The acknowledgement returned for a verified Stripe webhook (Issue #76).

    Always HTTP 200 once the signature is verified, so Stripe stops retrying: ``received`` is
    always true and ``outcome`` says what happened — ``processed`` (a handler ran and committed),
    ``duplicate`` (a replay of an already-processed event; nothing changed) or ``ignored`` (an
    event type nothing is registered for).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    received: bool = True
    outcome: str


class PaystackWebhookAck(BaseModel):
    """The acknowledgement returned for a verified Paystack webhook (Issue #79).

    Same contract as :class:`StripeWebhookAck`; see there for the meaning of ``outcome``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    received: bool = True
    outcome: str
