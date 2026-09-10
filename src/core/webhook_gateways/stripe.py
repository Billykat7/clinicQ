"""Stripe webhooks: signature verification and exactly-once processing (Issue #76).

The route is thin on purpose — see :mod:`src.core.webhook_gateways`. This module verifies the
signature, claims the event id, and dispatches to whatever your modules registered with
:func:`on`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import stripe
from sqlalchemy.orm import Session

from src.commons.exceptions import StripeNotConfiguredError, StripeSignatureError
from src.core.config import Settings
from src.database.models.stripe_event import StripeEvent

logger = logging.getLogger(__name__)

#: ``(db, settings, event_id, event_type, obj) -> None`` — see :data:`HANDLERS`.
Handler = Callable[[Session, Settings, str, str, Any], None]


def is_enabled(settings: Settings) -> bool:
    """Return whether the Stripe gateway is turned on for this deployment."""
    return settings.stripe_enabled


def _require_enabled(settings: Settings) -> None:
    """Raise :class:`StripeNotConfiguredError` unless the gateway is enabled."""
    if not settings.stripe_enabled:
        raise StripeNotConfiguredError()


@dataclass(frozen=True, slots=True)
class ProcessedEvent:
    """The outcome of :func:`process_event` — what the handlers did, and which event it was.

    ``outcome`` is ``processed`` (a handler ran and its effect committed), ``duplicate`` (a replay
    of an already-recorded event; nothing changed) or ``ignored`` (an event type nothing is
    registered for). The route turns all three into a ``200``, so the gateway stops retrying an
    event that has been accepted — or that this deployment deliberately does not act on.
    """

    outcome: str
    event_id: str


#: Event type -> what to do about it. Empty in the kernel: verifying and de-duplicating an event is
#: infrastructure, but what a payment *means* is domain. Register from your own module's import
#: side, so the handler ships with the code that understands it::
#:
#:     from src.core.webhook_gateways import stripe
#:
#:     @stripe.on("payment_intent.succeeded")
#:     def _record_payment(db, settings, event_id, event_type, obj) -> None:
#:         ...
#:
#: A handler runs inside the caller's transaction, alongside the row that claims the event id, so
#: raising rolls both back and the gateway's next retry is a fresh attempt rather than a duplicate.
HANDLERS: dict[str, Handler] = {}


def on(event_type: str) -> Callable[[Handler], Handler]:
    """Register a handler for ``event_type``. Returns the function, so it stacks as a decorator."""

    def register(handler: Handler) -> Handler:
        HANDLERS[event_type] = handler
        return handler

    return register


def verify_and_construct_event(
    payload: bytes,
    signature_header: str | None,
    settings: Settings,
) -> stripe.Event:
    """Verify a webhook's signature and return the parsed event, or raise (Issue #76).

    The signature is checked against ``STRIPE_WEBHOOK_SECRET`` *before* the JSON is trusted, so an
    unsigned, wrongly-signed or tampered payload never reaches the ledger. A missing signature
    header is treated as a verification failure.

    Args:
        payload: The exact raw request body bytes (Stripe signs the bytes, not a re-serialisation).
        signature_header: The ``Stripe-Signature`` header value, or ``None`` when absent.
        settings: Runtime settings carrying the webhook signing secret.

    Returns:
        The verified :class:`stripe.Event`.

    Raises:
        StripeNotConfiguredError: The gateway is disabled or missing its webhook secret.
        StripeSignatureError: The payload is unsigned or the signature does not verify.
    """
    _require_enabled(settings)
    if not settings.stripe_webhook_secret:
        raise StripeNotConfiguredError()
    if not signature_header:
        raise StripeSignatureError("missing Stripe-Signature header")
    try:
        return stripe.Webhook.construct_event(
            payload=payload,
            sig_header=signature_header,
            secret=settings.stripe_webhook_secret,
        )
    except stripe.SignatureVerificationError as exc:
        raise StripeSignatureError("invalid signature") from exc
    except ValueError as exc:
        # construct_event raises ValueError on a payload it cannot parse.
        raise StripeSignatureError("malformed payload") from exc


def process_event(
    db: Session,
    settings: Settings,
    event: stripe.Event | dict,
) -> ProcessedEvent:
    """Apply a verified Stripe event exactly once (Issue #76).

    Idempotency is the contract, and the event's id is the key. If it is already in
    ``stripe_event`` the event is a replay: nothing runs and the outcome is ``duplicate``.
    Otherwise the id is recorded in the **same transaction** as the handler's effect, so an event
    is marked processed if and only if that effect committed. An event type nothing is registered
    for returns ``ignored`` and records nothing — a gateway sends far more types than any one
    deployment acts on.
    """
    event_id = str(event["id"])
    event_type = str(event["type"])

    if db.get(StripeEvent, event_id) is not None:
        logger.info("stripe.event.duplicate", extra={"event_id": event_id})
        return ProcessedEvent(outcome="duplicate", event_id=event_id)

    handler = HANDLERS.get(event_type)
    if handler is None:
        logger.info(
            "stripe.event.ignored",
            extra={"event_id": event_id, "event_type": event_type},
        )
        return ProcessedEvent(outcome="ignored", event_id=event_id)

    db.add(StripeEvent(id=event_id, event_type=event_type))
    handler(db, settings, event_id, event_type, event["data"]["object"])
    return ProcessedEvent(outcome="processed", event_id=event_id)
