"""Paystack webhooks: signature verification and exactly-once processing (Issue #79).

The route is thin on purpose — see :mod:`src.core.webhook_gateways`. This module verifies the
HMAC-SHA512 signature, claims the event id, and dispatches to whatever your modules registered
with :func:`on`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from src.commons.exceptions import PaystackNotConfiguredError, PaystackSignatureError
from src.core.config import Settings
from src.database.models.paystack_event import PaystackEvent

logger = logging.getLogger(__name__)

#: ``(db, settings, event_id, event_type, data) -> None`` — see :data:`HANDLERS`.
Handler = Callable[[Session, Settings, str, str, Any], None]


def is_enabled(settings: Settings) -> bool:
    """Return whether the Paystack gateway is turned on for this deployment."""
    return settings.paystack_enabled


def _require_enabled(settings: Settings) -> None:
    """Raise :class:`PaystackNotConfiguredError` unless the gateway is enabled."""
    if not settings.paystack_enabled:
        raise PaystackNotConfiguredError()


def _secret_key(settings: Settings) -> str:
    """Return the configured secret key, or raise when the gateway is misconfigured.

    Kept tiny and separate so the key is read at the call site and never stored on a logger, an
    exception message, or an HTTP client's ``repr``.
    """
    _require_enabled(settings)
    if not settings.paystack_secret_key:
        raise PaystackNotConfiguredError()
    return settings.paystack_secret_key


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
) -> dict[str, Any]:
    """Verify a webhook's HMAC-SHA512 signature and return the parsed event, or raise (Issue #79).

    Paystack signs the **raw** request body with an HMAC-SHA512 keyed on the secret key and sends the
    hex digest in ``x-paystack-signature``. The signature is checked *before* the JSON is trusted, so
    an unsigned, wrongly-signed or tampered payload never reaches the ledger. A missing header is a
    verification failure.

    Args:
        payload: The exact raw request body bytes (Paystack signs the bytes, not a re-serialisation).
        signature_header: The ``x-paystack-signature`` header value, or ``None`` when absent.
        settings: Runtime settings carrying the secret (signing) key.

    Returns:
        The verified event as a parsed mapping.

    Raises:
        PaystackNotConfiguredError: The gateway is disabled or missing its key.
        PaystackSignatureError: The payload is unsigned, the signature does not verify, or it is
            malformed JSON.
    """
    secret_key = _secret_key(settings)
    if not signature_header:
        raise PaystackSignatureError("missing x-paystack-signature header")
    expected = hmac.new(secret_key.encode(), payload, hashlib.sha512).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise PaystackSignatureError("invalid signature")
    try:
        event = json.loads(payload)
    except ValueError as exc:
        raise PaystackSignatureError("malformed payload") from exc
    if not isinstance(event, dict):
        raise PaystackSignatureError("malformed payload")
    return event


def process_event(
    db: Session,
    settings: Settings,
    event: dict[str, Any],
) -> ProcessedEvent:
    """Apply a verified Paystack event exactly once (Issue #79).

    Paystack's envelope carries no id of its own, so the key is the event type plus the
    transaction reference it concerns — the pair a retry repeats and a distinct event does not.
    Recorded in the **same transaction** as the handler's effect, so an event is marked processed
    if and only if that effect committed.
    """
    event_type = str(event.get("event") or "")
    data = event.get("data") or {}
    reference = str(data.get("reference") or "")
    event_id = f"{event_type}:{reference}"

    if reference and db.get(PaystackEvent, event_id) is not None:
        logger.info("paystack.event.duplicate", extra={"event_id": event_id})
        return ProcessedEvent(outcome="duplicate", event_id=event_id)

    handler = HANDLERS.get(event_type)
    if handler is None:
        logger.info(
            "paystack.event.ignored",
            extra={"event_id": event_id, "event_type": event_type},
        )
        return ProcessedEvent(outcome="ignored", event_id=event_id)

    db.add(PaystackEvent(id=event_id, event_type=event_type, reference=reference))
    handler(db, settings, event_id, event_type, data)
    return ProcessedEvent(outcome="processed", event_id=event_id)
