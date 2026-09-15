"""Web push: a patient's browser subscriptions, and sending one encrypted message (Issue 64).

Two halves, kept apart from the transport adapter (:mod:`src.modules.notifications.transports.webpush`)
so the adapter stays free of database sessions:

* **Subscriptions.** :func:`subscribe` stores what a browser hands the page after the patient allowed
  notifications: its push service endpoint and the keys to encrypt to. The server will later POST to that
  endpoint, so :func:`endpoint_allowed` accepts only a known push service host over https: an arbitrary URL
  here would let anyone make the server send requests wherever they liked. A dead subscription (the push
  service answers ``404`` or ``410``) is deleted by :func:`forget` on the first failed send.
* **Sending.** :class:`VapidSender` does the three steps of the Web Push protocol: encrypt the payload to
  the browser's keys (RFC 8291, ``aes128gcm``, with ``http-ece``), sign a VAPID token for the push service
  (RFC 8292, with ``py-vapid``), and POST it (``httpx``). The push service cannot read the message.

The private VAPID key is a secret from the environment (``WEB_PUSH_VAPID_PRIVATE_KEY``) and is never
logged; neither is an endpoint, which is itself a credential to message that browser.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final
from urllib.parse import urlsplit

import http_ece
import httpx
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.commons.time import now_sast, stored_sast
from src.core.config import Settings, get_settings
from src.database.models.push_subscription import PushSubscription
from src.modules.notifications.transport_errors import (
    PermanentTransportError,
    TransportError,
)

logger = logging.getLogger(__name__)

#: How long a signed VAPID token is good for. RFC 8292 allows at most 24 hours.
VAPID_TOKEN_SECONDS: Final = 12 * 3600
#: The most a user agent string is kept to.
USER_AGENT_LENGTH: Final = 200
#: What a browser's keys decode to: a 65-byte uncompressed P-256 point, and a 16-byte secret.
P256DH_BYTES: Final = 65
AUTH_BYTES: Final = 16


class PushUrgency(StrEnum):
    """The ``Urgency`` header (RFC 8030 §5.3): how hard the push service tries to wake the phone."""

    HIGH = "high"
    NORMAL = "normal"


class SubscriptionGoneError(PermanentTransportError):
    """The push service says this subscription no longer exists (``404``/``410``). Delete it."""

    def __init__(self, subscription_id: str, detail: str) -> None:
        """Name the subscription to forget; ``detail`` is safe to log (no endpoint)."""
        super().__init__(detail)
        self.subscription_id = subscription_id


class InvalidSubscriptionError(ValueError):
    """A browser's subscription the server will not store: a bad key, or an endpoint it will not call."""


@dataclass(frozen=True, slots=True)
class PushTarget:
    """One subscription as the sender needs it."""

    id: str
    endpoint: str
    p256dh: str
    auth: str


def _b64decode(value: str) -> bytes:
    """Decode base64url with or without padding."""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def endpoint_hash(endpoint: str) -> str:
    """The unique key of an endpoint: its SHA-256, hex."""
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def endpoint_allowed(endpoint: str, settings: Settings | None = None) -> bool:
    """Whether the server may POST to ``endpoint``: a known push service host, over https.

    A host matches when it is one of ``WEB_PUSH_ALLOWED_HOSTS`` or a subdomain of one. No user info, no
    port other than the scheme's, no fragment: nothing that could steer the request somewhere else.
    """
    cfg = settings or get_settings()
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme not in ("https", "http") or parts.username or parts.fragment:
        return False
    if parts.scheme == "http" and cfg.web_push_require_https:
        return False
    if port is not None and cfg.web_push_require_https and port != 443:
        return False
    host = (parts.hostname or "").lower()
    return any(
        host == allowed or host.endswith(f".{allowed}")
        for allowed in (entry.lower() for entry in cfg.web_push_allowed_hosts)
    )


def _validate_keys(p256dh: str, auth: str) -> None:
    """Refuse keys that are not a P-256 point and a 16-byte secret; the encryption would fail later."""
    try:
        point = _b64decode(p256dh)
        secret = _b64decode(auth)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    except ValueError as exc:
        raise InvalidSubscriptionError(
            "The subscription's keys are not valid."
        ) from exc
    if len(point) != P256DH_BYTES or len(secret) != AUTH_BYTES:
        raise InvalidSubscriptionError("The subscription's keys are not valid.")


def subscribe(
    db: Session,
    patient_id: str,
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    expires_at: datetime | None = None,
    user_agent: str | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[PushSubscription, bool]:
    """Store a browser's subscription for ``patient_id``; return it and whether it is new. The caller commits.

    Subscribing again from the same browser updates its keys. A browser another patient subscribed moves to
    this one, because the person holding the phone now is the one who agreed.

    Raises:
        InvalidSubscriptionError: The endpoint is not an allowed push service, or the keys are not valid.
    """
    if not endpoint_allowed(endpoint, settings):
        raise InvalidSubscriptionError(
            "That is not a push service this clinic can send to."
        )
    _validate_keys(p256dh, auth)
    moment = now or now_sast()
    key = endpoint_hash(endpoint)
    row = db.execute(
        select(PushSubscription).where(PushSubscription.endpoint_hash == key)
    ).scalar_one_or_none()
    created = row is None
    if row is None:
        row = PushSubscription(endpoint=endpoint, endpoint_hash=key)
        db.add(row)
    row.patient_id = patient_id
    row.p256dh = p256dh
    row.auth = auth
    row.expires_at = expires_at
    row.user_agent = (user_agent or "")[:USER_AGENT_LENGTH] or None
    row.modified_at = moment
    db.flush()
    return row, created


def unsubscribe(db: Session, patient_id: str, endpoint: str) -> bool:
    """Delete this patient's subscription at ``endpoint``; whether there was one. The caller commits."""
    row = db.execute(
        select(PushSubscription).where(
            PushSubscription.endpoint_hash == endpoint_hash(endpoint),
            PushSubscription.patient_id == patient_id,
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    db.delete(row)
    return True


def targets_for(
    db: Session, patient_id: str, *, now: datetime | None = None
) -> tuple[PushTarget, ...]:
    """The patient's usable subscriptions, newest first. An expired one is left out."""
    moment = now or now_sast()
    rows = db.execute(
        select(PushSubscription)
        .where(PushSubscription.patient_id == patient_id)
        .order_by(PushSubscription.created_at.desc(), PushSubscription.id.desc())
    ).scalars()
    return tuple(
        PushTarget(id=row.id, endpoint=row.endpoint, p256dh=row.p256dh, auth=row.auth)
        for row in rows
        if row.expires_at is None or stored_sast(row.expires_at) > moment
    )


def forget(db: Session, subscription_id: str) -> None:
    """Delete a subscription the push service says is gone. The caller commits."""
    db.execute(delete(PushSubscription).where(PushSubscription.id == subscription_id))
    logger.info(
        "Web push subscription %s removed: the push service says it is gone.",
        subscription_id,
    )


def mark_sent(
    db: Session, subscription_id: str, *, now: datetime | None = None
) -> None:
    """Record that a push service accepted a message for this subscription. The caller commits."""
    row = db.get(PushSubscription, subscription_id)
    if row is not None:
        row.last_sent_at = now or now_sast()


class VapidSender:
    """Encrypt, sign and POST one web push message (RFC 8030, 8291, 8292)."""

    def __init__(
        self, settings: Settings | None = None, *, client: httpx.Client | None = None
    ) -> None:
        """Use the configured VAPID keys; ``client`` lets a test point the POST at a local push service."""
        self.settings = settings or get_settings()
        if not self.settings.web_push_enabled:
            raise ValueError("web push is not configured (WEB_PUSH_VAPID_* are unset)")
        self._vapid = Vapid02.from_raw(
            self.settings.web_push_vapid_private_key.encode()
        )
        self._client = client or httpx.Client(
            timeout=self.settings.web_push_timeout_seconds
        )

    def send(
        self,
        target: PushTarget,
        payload: dict[str, Any],
        *,
        urgency: PushUrgency = PushUrgency.HIGH,
    ) -> str:
        """Deliver ``payload`` to one browser through its push service; return the push service's message id.

        Raises:
            SubscriptionGoneError: ``404`` or ``410``: the browser unsubscribed or the subscription expired.
            PermanentTransportError: The endpoint is not allowed, or the push service refused the message
                itself (``400``, ``413``): sending it again cannot work.
            TransportError: A timeout, ``429`` or a ``5xx``: worth trying again.
        """
        if not endpoint_allowed(target.endpoint, self.settings):
            raise PermanentTransportError(
                "the subscription's endpoint is not an allowed push service"
            )
        body = self._encrypt(
            target, json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        parts = urlsplit(target.endpoint)
        headers = self._vapid.sign(
            {
                "aud": f"{parts.scheme}://{parts.netloc}",
                "exp": int(time.time()) + VAPID_TOKEN_SECONDS,
                "sub": self.settings.web_push_vapid_subject,
            }
        )
        headers.update(
            {
                "TTL": str(self.settings.web_push_ttl_seconds),
                "Urgency": urgency.value,
                "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
            }
        )
        try:
            response = self._client.post(target.endpoint, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise TransportError(
                f"push service unreachable: {type(exc).__name__}"
            ) from exc
        code = response.status_code
        if code in (httpx.codes.NOT_FOUND, httpx.codes.GONE):
            raise SubscriptionGoneError(
                target.id, f"push service answered {code}: subscription gone"
            )
        if code in (httpx.codes.BAD_REQUEST, httpx.codes.REQUEST_ENTITY_TOO_LARGE):
            raise PermanentTransportError(f"push service refused the message ({code})")
        if code >= httpx.codes.BAD_REQUEST:
            raise TransportError(f"push service answered {code}")
        location = response.headers.get("Location", "")
        return (
            location.rsplit("/", 1)[-1] if location else f"push-{os.urandom(8).hex()}"
        )

    @staticmethod
    def _encrypt(target: PushTarget, plaintext: bytes) -> bytes:
        """Encrypt to the browser's keys with a fresh server key and salt (RFC 8291, aes128gcm)."""
        return http_ece.encrypt(
            plaintext,
            salt=os.urandom(16),
            private_key=ec.generate_private_key(ec.SECP256R1()),
            dh=_b64decode(target.p256dh),
            auth_secret=_b64decode(target.auth),
            version="aes128gcm",
        )
