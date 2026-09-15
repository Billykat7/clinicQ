"""Inbound webhook routes — the signed callbacks external services make to us (Issues #76, #79).

The webhooks today are the payment gateways': Stripe's and Paystack's. Each gateway collects the
money and calls back to tell us what happened; these endpoints are where a card/bank payment turns
into a ledger fact. Two properties make them safe to expose unauthenticated:

* **Signature-verified.** The routes are not behind the app's RBAC — a gateway cannot present a
  bearer token — so trust comes entirely from the webhook signature. The raw request bytes are
  verified (Stripe against ``STRIPE_WEBHOOK_SECRET``; Paystack via HMAC-SHA512 under the secret key)
  *before* the payload is parsed; an unsigned, wrongly-signed or tampered call is rejected with
  ``400`` and no side effect.
* **Idempotent.** A gateway delivers each event at least once (retries, a slow ``200``, a manual
  dashboard replay). The handler claims the event's key before applying it, so a replay is
  acknowledged without moving the ledger a second time.

The routes stay thin: signature verification and idempotent processing live in
:mod:`src.core.webhook_gateways`, and what an event *means* lives in whichever module registered a
handler for it. Once the signature verifies the response is always ``200`` — with an ``outcome`` of
``processed`` / ``duplicate`` / ``ignored`` — so the gateway stops retrying an event this
deployment has already accepted, or deliberately does not act on.

Out of the box no handler is registered, so every event verifies, de-duplicates and returns
``ignored``. That is the correct empty state: the endpoint is safe to point a gateway at before
you have written the code that acts on it.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from src.commons.exceptions import (
    PaystackNotConfiguredError,
    PaystackSignatureError,
    StripeNotConfiguredError,
    StripeSignatureError,
)
from src.core.config import Settings, get_settings
from src.core.webhook_gateways import africastalking as sms_receipts
from src.core.webhook_gateways import paystack as paystack_gateway
from src.core.webhook_gateways import stripe as stripe_gateway
from src.database.session import get_db
from src.schemas.webhooks import PaystackWebhookAck, SmsReceiptAck, StripeWebhookAck

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/stripe",
    response_model=StripeWebhookAck,
    summary="Stripe webhook",
    operation_id="stripeWebhook",
)
async def stripe_webhook(
    db: DbSession,
    settings: SettingsDep,
    request: Request,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> JSONResponse:
    """Receive, verify and idempotently process a Stripe webhook event (Issue #76).

    Verifies the signature over the *raw* body before parsing, then applies the event exactly once:
    ``payment_intent.succeeded`` posts the gross to the lease ledger (recording the Stripe fee
    separately), ``payment_intent.payment_failed`` marks the attempt failed, and ``charge.refunded``
    reverses the ledger entry. A bad or missing signature is ``400``; the gateway being disabled is
    ``503``; otherwise the response is ``200`` with the outcome so Stripe stops retrying.
    """
    payload = await request.body()
    try:
        event = stripe_gateway.verify_and_construct_event(
            payload, stripe_signature, settings
        )
    except StripeSignatureError as exc:
        # Never log the payload or secret; the reason is a fixed, safe string.
        logger.warning("stripe.webhook.rejected", extra={"reason": exc.reason})
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )
    except StripeNotConfiguredError as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc)},
        )

    processed = stripe_gateway.process_event(db, settings, event)
    ack = StripeWebhookAck(
        received=True, outcome=processed.outcome, event_id=processed.event_id
    )
    return JSONResponse(status_code=status.HTTP_200_OK, content=ack.model_dump())


@router.post(
    "/paystack",
    response_model=PaystackWebhookAck,
    summary="Paystack webhook",
    operation_id="paystackWebhook",
)
async def paystack_webhook(
    db: DbSession,
    settings: SettingsDep,
    request: Request,
    x_paystack_signature: Annotated[
        str | None, Header(alias="x-paystack-signature")
    ] = None,
) -> JSONResponse:
    """Receive, verify and idempotently process a Paystack webhook event (Issue #79).

    Verifies the HMAC-SHA512 signature over the *raw* body before parsing, then applies the event
    exactly once: ``charge.success`` posts the gross to the lease ledger (recording the Paystack fee
    separately), ``refund.processed`` reverses the ledger entry, and ``transfer.failed`` is logged as
    an operational alert. A bad or missing signature is ``400``; the gateway being disabled is
    ``503``; otherwise the response is ``200`` with the outcome so Paystack stops retrying.
    """
    payload = await request.body()
    try:
        event = paystack_gateway.verify_and_construct_event(
            payload, x_paystack_signature, settings
        )
    except PaystackSignatureError as exc:
        # Never log the payload or secret; the reason is a fixed, safe string.
        logger.warning("paystack.webhook.rejected", extra={"reason": exc.reason})
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )
    except PaystackNotConfiguredError as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc)},
        )

    processed = paystack_gateway.process_event(db, settings, event)
    ack = PaystackWebhookAck(
        received=True,
        outcome=processed.outcome,
        event_id=processed.event_id,
    )
    return JSONResponse(status_code=status.HTTP_200_OK, content=ack.model_dump())


@router.post(
    "/sms/africastalking/{token}",
    response_model=SmsReceiptAck,
    summary="SMS delivery receipt (Africa's Talking)",
    operation_id="smsDeliveryReceipt",
)
async def sms_delivery_receipt(
    token: str, db: DbSession, settings: SettingsDep, request: Request
) -> JSONResponse:
    """Receive, verify and idempotently apply an SMS delivery receipt (Issue 65).

    The gateway does not sign callbacks, so the secret in the path (``SMS_WEBHOOK_TOKEN``) is checked in
    constant time before the body is read; a wrong one is ``404``, like a path that does not exist, and the
    webhook being off is ``503``. A body that is not a receipt is ``400``. Otherwise ``200`` with the
    outcome, so the gateway stops retrying.
    """
    try:
        sms_receipts.verify_token(token, settings)
    except sms_receipts.SmsWebhookNotConfiguredError:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "SMS delivery receipts are not configured."},
        )
    except sms_receipts.SmsWebhookRefusedError:
        logger.warning("sms.receipt.rejected", extra={"reason": "token"})
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND, content={"detail": "Not Found"}
        )
    payload = await request.body()
    try:
        receipt = sms_receipts.parse_receipt(payload)
    except sms_receipts.SmsWebhookRefusedError as exc:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)}
        )
    processed = sms_receipts.process_receipt(db, receipt)
    db.commit()
    ack = SmsReceiptAck(outcome=processed.outcome, event_id=processed.event_id)
    return JSONResponse(status_code=status.HTTP_200_OK, content=ack.model_dump())
