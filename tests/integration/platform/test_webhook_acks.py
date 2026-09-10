"""Payment-gateway webhook acknowledgements (Issue 1, found while making ``mypy src/`` clean).

Both routes build their acknowledgement from the gateway's ``ProcessedEvent``, whose ``event_id`` is
what the operator greps the logs for when a payment goes missing. The ack schemas had lost that field
while the routes still passed it, and both schemas are ``extra="forbid"``: every *verified* Stripe and
Paystack webhook raised inside the route and answered ``500``, so the gateway retried an event that
had already been accepted. Nothing covered the routes, which is how it shipped.

These drive the real path end to end: a correctly signed payload through signature verification,
de-duplication against the in-memory database, and the acknowledgement the gateway receives.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from src.core.config import Settings, get_settings
from src.database.session import get_db
from src.main import create_app

STRIPE_WEBHOOK_SECRET = "whsec_test_issue1"
PAYSTACK_SECRET_KEY = "sk_test_issue1"


@pytest.fixture
def gateway_client(session_factory: sessionmaker[Session]) -> Generator[TestClient]:
    """The app with both gateways enabled on test keys and ``get_db`` on the SQLite fixture."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        stripe_enabled=True,
        stripe_secret_key="sk_test_issue1",
        stripe_webhook_secret=STRIPE_WEBHOOK_SECRET,
        paystack_enabled=True,
        paystack_secret_key=PAYSTACK_SECRET_KEY,
    )

    def _db() -> Generator[Session]:
        db = session_factory()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _db
    with TestClient(app) as client:
        yield client


def _stripe_signature(payload: bytes) -> str:
    """A ``Stripe-Signature`` header the way Stripe computes it: HMAC-SHA256 over ``t.payload``."""
    timestamp = int(time.time())
    signed = f"{timestamp}.".encode() + payload
    digest = hmac.new(
        STRIPE_WEBHOOK_SECRET.encode(), signed, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def _paystack_signature(payload: bytes) -> str:
    """An ``x-paystack-signature`` header: HMAC-SHA512 of the raw body with the secret key."""
    return hmac.new(PAYSTACK_SECRET_KEY.encode(), payload, hashlib.sha512).hexdigest()


def test_verified_stripe_webhook_is_acknowledged_with_its_event_id(
    gateway_client: TestClient,
) -> None:
    payload = json.dumps(
        {
            "id": "evt_issue1",
            "object": "event",
            "type": "customer.created",
            "data": {"object": {}},
        }
    ).encode()

    response = gateway_client.post(
        "/api/v1/webhooks/stripe",
        content=payload,
        headers={
            "Stripe-Signature": _stripe_signature(payload),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    # No handler is registered for the type, so the kernel acknowledges and ignores it.
    assert response.json() == {
        "received": True,
        "outcome": "ignored",
        "event_id": "evt_issue1",
    }


def test_verified_paystack_webhook_is_acknowledged_with_its_event_id(
    gateway_client: TestClient,
) -> None:
    payload = json.dumps(
        {"event": "charge.success", "data": {"reference": "ref_issue1"}}
    ).encode()

    response = gateway_client.post(
        "/api/v1/webhooks/paystack",
        content=payload,
        headers={
            "x-paystack-signature": _paystack_signature(payload),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    # Paystack's envelope has no id of its own: the key is the event type plus the reference.
    assert response.json() == {
        "received": True,
        "outcome": "ignored",
        "event_id": "charge.success:ref_issue1",
    }


def test_unsigned_paystack_webhook_is_rejected_before_parsing(
    gateway_client: TestClient,
) -> None:
    payload = json.dumps(
        {"event": "charge.success", "data": {"reference": "ref_issue1"}}
    ).encode()

    response = gateway_client.post(
        "/api/v1/webhooks/paystack",
        content=payload,
        headers={"x-paystack-signature": "0" * 128, "Content-Type": "application/json"},
    )

    assert response.status_code == 400
