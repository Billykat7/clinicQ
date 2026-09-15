"""Web push, end to end against a local push service that decrypts what it is sent (Issue 64).

No network and no real push service: a small HTTP server on 127.0.0.1 plays one, and the test holds the
browser's private key, so it can prove what a phone would receive. What is shown:

* **a patient subscribes their own browser**, and only to a real push service: the endpoint is checked
  against ``WEB_PUSH_ALLOWED_HOSTS`` because the server POSTs to it later;
* **Call next pushes within 5 seconds**, as a message only the browser can decrypt, signed with the
  server's VAPID key, marked urgent and short-lived;
* **the payload carries the ticket number and the clinic's name and nothing else**: not the queue (a
  queue can be "HIV clinic"), not the room, not the reason, not a name;
* **a dead subscription is removed at the first failed send**, and SMS takes over for that message;
* **declining push means SMS**, for a patient with a phone number (every patient has one);
* **a push service outage is not mistaken for a dead subscription**: the subscription stays.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

import http_ece
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette import status

from scripts.generate_vapid_keys import generate as generate_vapid_keys
from src.commons.enums import (
    ConsentPurpose,
    NotificationChannel,
    NotificationStatus,
    PatientChannel,
)
from src.core.config import Settings, get_settings
from src.database.models import Notification, Patient, PushSubscription, Site
from src.modules.notifications.transports import NoopTransport, use_transports
from src.modules.notifications.transports.webpush import PAYLOAD_KEYS, WebPushTransport
from src.modules.notifications.webpush import VapidSender
from src.modules.patients.consent import record_consent
from src.modules.queue import ticket_page
from tests.integration.queue.conftest import SITE_A, queue_settings

_SUBJECT = "mailto:ops@clinicq.example"
#: A key pair made for this run, never a key written down anywhere.
_KEYS = generate_vapid_keys(_SUBJECT)
_PUBLIC = _KEYS["WEB_PUSH_VAPID_PUBLIC_KEY"]
_PRIVATE = _KEYS["WEB_PUSH_VAPID_PRIVATE_KEY"]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass
class Browser:
    """A browser's push keys: what it gives the page, and the private half only it holds."""

    private_key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )
    auth: bytes = field(default_factory=lambda: os.urandom(16))

    @property
    def p256dh(self) -> str:
        return _b64(
            self.private_key.public_key().public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
        )

    def subscription(self, endpoint: str) -> dict[str, Any]:
        """``PushSubscription.toJSON()``, as the page sends it."""
        return {
            "endpoint": endpoint,
            "expirationTime": None,
            "keys": {"p256dh": self.p256dh, "auth": _b64(self.auth)},
        }

    def read(self, body: bytes) -> dict[str, Any]:
        """Decrypt a push the way the browser does (RFC 8291, aes128gcm)."""
        plain = http_ece.decrypt(
            body,
            private_key=self.private_key,
            auth_secret=self.auth,
            version="aes128gcm",
        )
        return json.loads(plain)


@dataclass
class PushService:
    """A push service on 127.0.0.1 that keeps what it receives and answers what it is told to."""

    base: str
    received: list[tuple[str, dict[str, str], bytes, float]] = field(
        default_factory=list
    )
    answers: dict[str, int] = field(default_factory=dict)

    def endpoint(self, name: str) -> str:
        return f"{self.base}/push/{name}"


@pytest.fixture
def push_service() -> Iterator[PushService]:
    """A local push service, stopped afterwards."""
    service = PushService(base="")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            service.received.append(
                (self.path, dict(self.headers), body, time.monotonic())
            )
            code = service.answers.get(self.path.rsplit("/", 1)[-1], 201)
            self.send_response(code)
            self.send_header(
                "Location", f"{service.base}/message/{len(service.received)}"
            )
            self.end_headers()

        def log_message(self, *_: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    service.base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield service
    server.shutdown()


def _push_settings(**overrides: object) -> Settings:
    """The queue fixture's settings with web push on, allowed to reach the local push service."""
    return queue_settings(
        web_push_vapid_public_key=_PUBLIC,
        web_push_vapid_private_key=_PRIVATE,
        web_push_vapid_subject=_SUBJECT,
        web_push_allowed_hosts=["127.0.0.1"],
        web_push_require_https=False,
        **overrides,
    )


@pytest.fixture
def pushing(
    desk: SimpleNamespace, push_service: PushService, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SimpleNamespace]:
    """The clinic with web push configured: the app, the page data, and the transports."""
    settings = _push_settings()
    desk.app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(ticket_page, "get_settings", lambda: settings)
    sms = NoopTransport(channel=NotificationChannel.SMS, address="+27820000001")
    with use_transports(WebPushTransport(VapidSender(settings)), sms):
        yield SimpleNamespace(
            desk=desk, service=push_service, settings=settings, sms=sms
        )


def _patient_joined(desk: SimpleNamespace) -> tuple[TestClient, str, dict[str, Any]]:
    """A patient who agreed to messages joins Triage from the web."""
    client, patient_id = desk.patient()
    with desk.session() as db:
        record_consent(
            db,
            db.get_one(Patient, patient_id),
            ConsentPurpose.NOTIFICATIONS,
            granted=True,
            channel=PatientChannel.WEB,
        )
        db.commit()
    joined = client.post(desk.join_path(desk.triage), json={}).json()
    return client, patient_id, joined


_SUBSCRIBE = "/api/v1/notifications/web-push/subscriptions"


# --- subscribing -------------------------------------------------------------------------------------


def test_a_patient_subscribes_their_own_browser_to_a_real_push_service_only(
    pushing: SimpleNamespace,
) -> None:
    """The endpoint is where the server will POST, so only an allowed push service host is stored."""
    desk, service = pushing.desk, pushing.service
    client, patient_id, _ = _patient_joined(desk)
    browser = Browser()

    key = TestClient(desk.app).get("/api/v1/notifications/web-push/key").json()
    assert key == {"enabled": True, "public_key": _PUBLIC}

    anonymous = TestClient(desk.app).post(
        _SUBSCRIBE, json=browser.subscription(service.endpoint("a"))
    )
    assert anonymous.status_code == status.HTTP_401_UNAUTHORIZED

    first = client.post(_SUBSCRIBE, json=browser.subscription(service.endpoint("a")))
    again = client.post(_SUBSCRIBE, json=browser.subscription(service.endpoint("a")))
    assert (first.status_code, first.json()["created"]) == (201, True)
    assert (again.status_code, again.json()["created"]) == (200, False)
    assert first.json()["id"] == again.json()["id"]

    for endpoint in (
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1.evil.example/push/x",
        "http://user@127.0.0.1/push/x",
        "file:///etc/passwd",
    ):
        refused = client.post(_SUBSCRIBE, json=browser.subscription(endpoint))
        assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, endpoint
    bad_keys = browser.subscription(service.endpoint("b"))
    bad_keys["keys"]["p256dh"] = _b64(b"\x04" + b"\x01" * 64)
    assert client.post(_SUBSCRIBE, json=bad_keys).status_code == 422

    with desk.session() as db:
        rows = db.scalars(
            select(PushSubscription).where(PushSubscription.patient_id == patient_id)
        ).all()
    assert len(rows) == 1

    gone = client.request(
        "DELETE", _SUBSCRIBE, json={"endpoint": service.endpoint("a")}
    )
    assert gone.status_code == status.HTTP_204_NO_CONTENT
    with desk.session() as db:
        assert db.scalars(select(PushSubscription)).all() == []


def test_https_is_required_outside_a_local_test_push_service(
    desk: SimpleNamespace,
) -> None:
    """With the production defaults an http endpoint, or any host but a push service's, is refused."""
    from src.modules.notifications.webpush import endpoint_allowed

    production = Settings(_env_file=None)  # type: ignore[call-arg]
    assert endpoint_allowed("https://fcm.googleapis.com/fcm/send/abc", production)
    assert endpoint_allowed("https://web.push.apple.com/QK1", production)
    assert endpoint_allowed(
        "https://updates.push.services.mozilla.com/wpush/v2/x", production
    )
    for refused in (
        "http://fcm.googleapis.com/fcm/send/abc",
        "https://fcm.googleapis.com.evil.example/x",
        "https://fcm.googleapis.com:8443/x",
        "https://evil.example/fcm.googleapis.com",
        "https://10.0.0.5/push",
    ):
        assert not endpoint_allowed(refused, production), refused


def test_web_push_off_refuses_subscriptions_and_offers_no_button(
    desk: SimpleNamespace,
) -> None:
    """With no VAPID keys the key route says so and a subscription is 409."""
    client, _, joined = _patient_joined(desk)
    assert TestClient(desk.app).get("/api/v1/notifications/web-push/key").json() == {
        "enabled": False,
        "public_key": None,
    }
    refused = client.post(
        _SUBSCRIBE, json=Browser().subscription("https://fcm.googleapis.com/fcm/send/x")
    )
    assert refused.status_code == status.HTTP_409_CONFLICT
    page = client.get(
        f"/api/v1/tickets/{joined['page_url'].removeprefix('/t/')}"
    ).json()
    assert page["push_key"] is None and page["push_subscribe_url"] is None


def test_only_the_tickets_own_patient_is_offered_push_on_the_page(
    pushing: SimpleNamespace,
) -> None:
    """A phone that was only sent the link cannot sign itself up for someone else's messages."""
    desk = pushing.desk
    client, _, joined = _patient_joined(desk)
    api = f"/api/v1/tickets/{joined['page_url'].removeprefix('/t/')}"
    mine = client.get(api).json()
    assert mine["push_key"] == _PUBLIC and mine["push_subscribe_url"] == _SUBSCRIBE
    theirs = TestClient(desk.app).get(api).json()
    assert theirs["push_key"] is None and theirs["push_subscribe_url"] is None


# --- sending ---------------------------------------------------------------------------------------------


def _verify_vapid(authorization: str, audience: str) -> dict[str, Any]:
    """Check a ``vapid t=…,k=…`` header's ES256 signature and return its claims."""
    scheme, _, fields = authorization.partition(" ")
    assert scheme == "vapid"
    parts = dict(item.split("=", 1) for item in fields.split(","))
    assert parts["k"] == _PUBLIC
    header, claims, signature = parts["t"].split(".")
    raw = _unb64(signature)
    public = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), _unb64(parts["k"])
    )
    public.verify(
        encode_dss_signature(int.from_bytes(raw[:32]), int.from_bytes(raw[32:])),
        f"{header}.{claims}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )
    decoded = json.loads(_unb64(claims))
    assert decoded["aud"] == audience and decoded["sub"] == _SUBJECT
    assert decoded["exp"] > time.time()
    return decoded


def test_call_next_pushes_a_message_only_the_browser_can_read_within_5_seconds(
    pushing: SimpleNamespace,
) -> None:
    """How to verify, step 1, with a local push service in place of Google's."""
    desk, service = pushing.desk, pushing.service
    client, _, joined = _patient_joined(desk)
    browser = Browser()
    assert (
        client.post(
            _SUBSCRIBE, json=browser.subscription(service.endpoint("phone"))
        ).status_code
        == 201
    )

    pressed = time.monotonic()
    called = desk.staff("desk.a").post(
        f"/api/v1/sites/{SITE_A}/queues/{desk.triage.id}/tickets/call-next"
    )
    assert called.status_code == status.HTTP_200_OK
    deadline = pressed + 5
    while not service.received and time.monotonic() < deadline:
        time.sleep(0.01)
    assert service.received, "no push within 5 seconds of Call next"
    path, headers, body, arrived = service.received[0]
    print(f"\npush received {arrived - pressed:.3f} s after Call next")  # noqa: T201

    assert path == "/push/phone"
    assert headers["Content-Encoding"] == "aes128gcm"
    assert headers["Urgency"] == "high" and headers["TTL"] == "900"
    _verify_vapid(headers["Authorization"], service.base)
    assert joined["ticket"]["number"].encode() not in body, (
        "the push service can read the message"
    )

    payload = browser.read(body)
    print(f"decrypted by the browser: {json.dumps(payload, ensure_ascii=False)}")  # noqa: T201
    assert set(payload) <= PAYLOAD_KEYS
    assert payload["title"] == "Please come in now"
    assert payload["body"] == f"Ticket {joined['ticket']['number']} at {_clinic(desk)}."
    assert payload["url"] == joined["page_url"]
    assert pushing.sms.sent == []

    with desk.session() as db:
        row = db.scalars(
            select(Notification).where(
                Notification.channel == NotificationChannel.WEB_PUSH.value
            )
        ).one()
        subscription = db.scalars(select(PushSubscription)).one()
    assert row.status == NotificationStatus.SENT.value and row.cost == 0
    assert subscription.last_sent_at is not None


def _clinic(desk: SimpleNamespace) -> str:
    """Clinic A's name."""
    with desk.session() as db:
        return db.get_one(Site, SITE_A).name


def test_a_dead_subscription_is_removed_at_the_first_failed_send_and_sms_takes_over(
    pushing: SimpleNamespace,
) -> None:
    """How to verify, step 3: the push service says 410, the row is gone, and the patient gets an SMS."""
    desk, service = pushing.desk, pushing.service
    client, patient_id, _ = _patient_joined(desk)
    client.post(_SUBSCRIBE, json=Browser().subscription(service.endpoint("expired")))
    service.answers["expired"] = 410

    desk.staff("desk.a").post(
        f"/api/v1/sites/{SITE_A}/queues/{desk.triage.id}/tickets/call-next"
    )

    with desk.session() as db:
        remaining = db.scalars(
            select(PushSubscription).where(PushSubscription.patient_id == patient_id)
        ).all()
        rows = {
            row.channel: row
            for row in db.scalars(
                select(Notification).where(Notification.patient_id == patient_id)
            )
        }
    assert remaining == [], "the dead subscription was not removed"
    push, sms = (
        rows[NotificationChannel.WEB_PUSH.value],
        rows[NotificationChannel.SMS.value],
    )
    assert (push.status, push.attempts) == (NotificationStatus.DEAD.value, 1)
    assert "410" in (push.last_error or "")
    assert (sms.status, sms.fallback_of_id) == (NotificationStatus.SENT.value, push.id)
    assert len(pushing.sms.sent) == 1


def test_a_patient_who_declined_push_is_sent_an_sms(pushing: SimpleNamespace) -> None:
    """No subscription (the patient said no, or never pressed the button): SMS, and web push is not tried."""
    desk = pushing.desk
    _, patient_id, _ = _patient_joined(desk)
    desk.staff("desk.a").post(
        f"/api/v1/sites/{SITE_A}/queues/{desk.triage.id}/tickets/call-next"
    )
    with desk.session() as db:
        channels = db.scalars(
            select(Notification.channel).where(Notification.patient_id == patient_id)
        ).all()
    assert channels == [NotificationChannel.SMS.value]
    assert pushing.service.received == [] and len(pushing.sms.sent) == 1


def test_a_push_service_outage_keeps_the_subscription_and_still_reaches_the_patient(
    pushing: SimpleNamespace,
) -> None:
    """A 503 is the push service's problem, not the browser's: keep the subscription, send SMS this time."""
    desk, service = pushing.desk, pushing.service
    client, patient_id, _ = _patient_joined(desk)
    client.post(_SUBSCRIBE, json=Browser().subscription(service.endpoint("busy")))
    service.answers["busy"] = 503

    desk.staff("desk.a").post(
        f"/api/v1/sites/{SITE_A}/queues/{desk.triage.id}/tickets/call-next"
    )

    with desk.session() as db:
        kept = db.scalars(
            select(PushSubscription).where(PushSubscription.patient_id == patient_id)
        ).all()
    assert len(kept) == 1
    assert len(pushing.sms.sent) == 1
