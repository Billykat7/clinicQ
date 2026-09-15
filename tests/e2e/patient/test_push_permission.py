"""Web push on the ticket page, in a real browser: when the permission prompt may appear (Issue 64).

Chromium without a Google account cannot subscribe to a real push service, so the browser's push
subscription is replaced by one made in the test (its keys are real P-256 keys); everything else is the
page's own code, the real service worker registration and the real subscription route. What is shown:

* **the permission prompt never appears when the page loads**, only when the patient presses the button;
* **a patient who allows it is subscribed**: the subscription reaches the server and is stored;
* **a patient who declines is told they will get an SMS**, and nothing is stored;
* **a phone that was only sent the link is not offered push at all.**
"""

from __future__ import annotations

import base64
import os
import time
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from src.database.models import PushSubscription

pytestmark = pytest.mark.postgres

_BROWSER = """({answer, endpoint, p256dh, auth}) => {
    window.__permissionAsks = 0;
    let permission = 'default';
    Object.defineProperty(Notification, 'permission', { get: () => permission, configurable: true });
    Notification.requestPermission = () => {
        window.__permissionAsks += 1;
        permission = answer;
        return Promise.resolve(answer);
    };
    const subscription = { endpoint, toJSON: () => ({ endpoint, expirationTime: null, keys: { p256dh, auth } }) };
    PushManager.prototype.getSubscription = () => Promise.resolve(null);
    PushManager.prototype.subscribe = () => Promise.resolve(subscription);
}"""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _browser_keys() -> dict[str, str]:
    key = ec.generate_private_key(ec.SECP256R1()).public_key()
    point = key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return {"p256dh": _b64(point), "auth": _b64(os.urandom(16))}


def _prepare(page: Any, answer: str, endpoint: str) -> None:
    page.add_init_script(
        script=f"({_BROWSER})({{answer: {answer!r}, endpoint: {endpoint!r}, "
        + ", ".join(f"{k}: {v!r}" for k, v in _browser_keys().items())
        + "})"
    )


def _until(page: Any, predicate: str, timeout: float = 15.0) -> None:
    started = time.monotonic()
    while not page.evaluate(predicate):
        assert time.monotonic() - started < timeout, (
            f"timed out waiting for {predicate}"
        )
        time.sleep(0.05)


def _subscriptions(
    patient_day: SimpleNamespace, patient_id: str
) -> list[PushSubscription]:
    with patient_day.clinic.session() as db:
        return list(
            db.scalars(
                select(PushSubscription).where(
                    PushSubscription.patient_id == patient_id
                )
            )
        )


def test_the_prompt_waits_for_the_button_and_allowing_subscribes_the_phone(
    patient_day: SimpleNamespace,
) -> None:
    """How to verify, step 2: no prompt on load; one when pressed; the subscription is stored."""
    mine = patient_day.ticket(ahead=2)
    page = patient_day.owner_page(mine)
    _prepare(page, "granted", "https://fcm.googleapis.com/fcm/send/e2e-allow")
    page.goto(mine.path)
    _until(page, "() => document.getElementById('tk-live').textContent === 'Live'")
    page.wait_for_timeout(
        1500
    )  # long enough for any script that would ask on load to have asked

    assert page.evaluate("() => window.__permissionAsks") == 0
    assert page.locator("#tk-push-start").is_visible()
    assert _subscriptions(patient_day, mine.patient_id) == []

    page.locator("#tk-push-start").click()
    _until(page, "() => !document.getElementById('tk-push-note').hidden")
    assert page.evaluate("() => window.__permissionAsks") == 1
    assert (
        page.locator("#tk-push-note")
        .inner_text()
        .startswith("Done: this phone will be told")
    )
    stored = _subscriptions(patient_day, mine.patient_id)
    assert [row.endpoint for row in stored] == [
        "https://fcm.googleapis.com/fcm/send/e2e-allow"
    ]
    worker = page.evaluate(
        "async () => (await navigator.serviceWorker.getRegistration('/t/'))?.active?.scriptURL || null"
    )
    assert worker and worker.endswith("/patient-sw.js")


def test_declining_says_sms_instead_and_stores_nothing(
    patient_day: SimpleNamespace,
) -> None:
    """Declining push falls back to SMS: the page says so, and the server has no subscription."""
    mine = patient_day.ticket(ahead=1)
    page = patient_day.owner_page(mine)
    _prepare(page, "denied", "https://fcm.googleapis.com/fcm/send/e2e-deny")
    page.goto(mine.path)
    _until(page, "() => document.getElementById('tk-live').textContent === 'Live'")
    page.locator("#tk-push-start").click()
    _until(page, "() => !document.getElementById('tk-push-note').hidden")
    assert "SMS instead" in page.locator("#tk-push-note").inner_text()
    assert _subscriptions(patient_day, mine.patient_id) == []


def test_a_phone_that_was_only_sent_the_link_is_not_offered_push(
    patient_day: SimpleNamespace,
) -> None:
    """A family member following along never sees the button or a prompt."""
    mine = patient_day.ticket(ahead=1)
    page = patient_day.follower_page()
    _prepare(page, "granted", "https://fcm.googleapis.com/fcm/send/e2e-follow")
    page.goto(mine.path)
    _until(page, "() => document.getElementById('tk-live').textContent === 'Live'")
    page.wait_for_timeout(1000)
    assert page.locator("#tk-push").count() == 0
    assert page.evaluate("() => window.__permissionAsks") == 0
