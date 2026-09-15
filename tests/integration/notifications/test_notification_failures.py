"""Every way a patient notification can fail ends in a terminal ledger status, never in limbo (Issue 71).

Re-verified from the shipped code, with the real transports and the real gateway adapters, and only the
network faked (``httpx.MockTransport``), so no provider is contacted and no credential is needed:

* **a provider timeout** and **a provider error** retry with backoff and are dead-lettered at the attempt
  budget, each reason on the row;
* **a malformed number** the gateway refuses is dead-lettered at once, with no retry to pay for;
* **a revoked push subscription** is dead-lettered at once, deleted, and the message goes by SMS instead;
  the next message does not try the dead subscription again;
* **an error nobody planned for** (a bug while preparing the message) is recorded on the row and retried
  like any other failure, and does not stop the sweep delivering everything else;
* **a delivery that never ran** (the process died after the commit) is picked up by the sweep.

After each story the retry sweep is run, moving the clock past every backoff, until nothing is due, and
every row the story wrote must be terminal: ``sent``, ``delivered``, ``dead`` or ``suppressed``.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select
from sqlalchemy.orm import Session

from scripts.generate_vapid_keys import generate
from src.commons.enums import (
    NOTIFICATION_TERMINAL_STATUSES,
    ConsentPurpose,
    NotificationChannel,
    NotificationStatus,
    PatientChannel,
    PatientEvent,
    SmsProviderKind,
)
from src.commons.time import now_sast, stored_sast
from src.core.config import Settings
from src.database.models import Notification, Patient, PushSubscription
from src.modules.notifications import dispatch, service, webpush
from src.modules.notifications.sms import AfricasTalkingSmsProvider
from src.modules.notifications.transports import use_transports
from src.modules.notifications.transports.sms import SmsTransport
from src.modules.notifications.transports.webpush import WebPushTransport
from src.modules.notifications.webpush import VapidSender
from src.modules.patients.consent import record_consent
from tests.factories import PatientFactory
from tests.integration.queue.conftest import SITE_A, queue_settings

_TERMINAL = {status.value for status in NOTIFICATION_TERMINAL_STATUSES}
_PUSH_ENDPOINT = "https://fcm.googleapis.com/fcm/send/e2e-revoked"


def _settings(**overrides: object) -> Settings:
    keys = {
        name.lower(): value
        for name, value in generate("mailto:ops@clinicq.example").items()
    }
    return queue_settings(
        sms_provider=SmsProviderKind.AFRICAS_TALKING,
        africas_talking_api_key="not-a-real-key",
        notification_max_attempts=4,
        **keys,
        **overrides,
    )


def _gateway(
    answer: Callable[[httpx.Request], httpx.Response],
) -> AfricasTalkingSmsProvider:
    """The real Africa's Talking adapter, with the network answering as ``answer`` says."""
    return AfricasTalkingSmsProvider(
        _settings(), client=httpx.Client(transport=httpx.MockTransport(answer))
    )


def _recipient(
    status: str, code: int, cost: str = "ZAR 0.2500"
) -> Callable[[httpx.Request], httpx.Response]:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={
                "SMSMessageData": {
                    "Message": "Sent to 1/1",
                    "Recipients": [
                        {
                            "statusCode": code,
                            "status": status,
                            "messageId": "ATXid_1",
                            "cost": cost,
                        }
                    ],
                }
            },
        )

    return answer


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("the gateway did not answer in time", request=request)


def _server_error(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, text="upstream failure")


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Settings every module on the delivery path reads: the gateway configured, no real key, 4 attempts."""
    chosen = _settings()
    for module in (
        "src.modules.notifications.service",
        "src.modules.notifications.webpush",
        "src.modules.notifications.budget",
        "src.modules.notifications.transports.registry",
    ):
        monkeypatch.setattr(f"{module}.get_settings", lambda: chosen)
    yield chosen


def _patient(db: Session) -> Patient:
    patient = PatientFactory.create(db)
    record_consent(
        db,
        patient,
        ConsentPurpose.NOTIFICATIONS,
        granted=True,
        channel=PatientChannel.WEB,
    )
    db.flush()
    return patient


def _tell(db: Session, patient: Patient, key: str) -> Notification:
    """One "please come in" message, committed and delivered after the commit as the queue does it."""
    row = service.notify(
        db,
        patient_id=patient.id,
        event=PatientEvent.CALLED,
        context={
            "number": "T004",
            "clinic": "Zola Clinic",
            "queue": "Triage",
            "room": "Room 2",
        },
        site_id=SITE_A,
        dedupe_key=key,
        now=now_sast(),
    )
    assert row is not None
    db.commit()
    return row


def _settle(
    desk: SimpleNamespace, *, start: datetime | None = None, rounds: int = 12
) -> None:
    """Run the retry sweep, jumping the clock past every backoff, until no row is due."""
    moment = start or now_sast()
    for _ in range(rounds):
        with desk.session() as db:
            waiting = db.scalars(
                select(Notification.next_attempt_at).where(
                    Notification.status.in_(
                        [
                            NotificationStatus.QUEUED.value,
                            NotificationStatus.FAILED.value,
                        ]
                    )
                )
            ).all()
            if not waiting:
                return
            due = [stored_sast(at) for at in waiting if at is not None]
            moment = max([moment, *due]) + timedelta(seconds=1)
            service.run_retry_sweep(db, now=moment)
            db.commit()
    raise AssertionError(
        "rows were still waiting after the sweep ran out of rounds: limbo"
    )


def _ledger(desk: SimpleNamespace, patient_id: str) -> list[Notification]:
    with desk.session() as db:
        return list(
            db.scalars(
                select(Notification)
                .where(Notification.patient_id == patient_id)
                .order_by(Notification.created_at, Notification.channel)
            )
        )


def _all_terminal(desk: SimpleNamespace) -> None:
    with desk.session() as db:
        limbo = db.scalars(
            select(Notification).where(Notification.status.not_in(_TERMINAL))
        ).all()
    assert limbo == [], [
        (row.id, row.status, row.attempts, row.next_attempt_at) for row in limbo
    ]


# --- the gateway -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (_timeout, "gateway unreachable: ReadTimeout"),
        (_server_error, "gateway answered HTTP 500"),
    ],
    ids=["provider-timeout", "provider-error"],
)
def test_a_provider_timeout_or_error_retries_then_is_dead_lettered_with_its_reason(
    desk: SimpleNamespace,
    settings: Settings,
    answer: Callable[[httpx.Request], httpx.Response],
    reason: str,
) -> None:
    """How to verify, step 1: a timeout and an error each end in a terminal status."""
    with use_transports(SmsTransport(_gateway(answer))), desk.session() as db:
        patient = _patient(db)
        _tell(db, patient, f"failure-{reason}")
        _settle(desk)

    (row,) = _ledger(desk, patient.id)
    assert (row.status, row.attempts, row.max_attempts) == (
        NotificationStatus.DEAD.value,
        4,
        4,
    )
    assert row.last_error == reason and row.next_attempt_at is None
    _all_terminal(desk)


def test_a_malformed_number_the_gateway_refuses_is_dead_lettered_at_once(
    desk: SimpleNamespace, settings: Settings
) -> None:
    """How to verify, step 1: no retry is paid for a number that can never work."""
    calls: list[httpx.Request] = []

    def refuses(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _recipient("InvalidPhoneNumber", 403, cost="0")(request)

    with use_transports(SmsTransport(_gateway(refuses))), desk.session() as db:
        patient = _patient(db)
        _tell(db, patient, "malformed-number")
        _settle(desk)

    (row,) = _ledger(desk, patient.id)
    assert (row.status, row.attempts) == (NotificationStatus.DEAD.value, 1)
    assert "InvalidPhoneNumber" in (row.last_error or "")
    assert len(calls) == 1
    _all_terminal(desk)


# --- web push --------------------------------------------------------------------------------------------------


def _browser_keys() -> tuple[str, str]:
    point = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    encode = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # noqa: E731
    return encode(point), encode(os.urandom(16))


def test_a_revoked_push_subscription_is_dead_lettered_deleted_and_the_message_goes_by_sms(
    desk: SimpleNamespace, settings: Settings
) -> None:
    """How to verify, step 1: the push service says the subscription is gone (410)."""
    pushes: list[httpx.Request] = []

    def gone(request: httpx.Request) -> httpx.Response:
        pushes.append(request)
        return httpx.Response(410, text="subscription has unsubscribed or expired")

    sms_calls: list[httpx.Request] = []

    def accepts(request: httpx.Request) -> httpx.Response:
        sms_calls.append(request)
        return _recipient("Success", 101)(request)

    push = WebPushTransport(
        VapidSender(settings, client=httpx.Client(transport=httpx.MockTransport(gone)))
    )
    with use_transports(push, SmsTransport(_gateway(accepts))), desk.session() as db:
        patient = _patient(db)
        p256dh, auth = _browser_keys()
        webpush.subscribe(
            db,
            patient.id,
            endpoint=_PUSH_ENDPOINT,
            p256dh=p256dh,
            auth=auth,
            settings=settings,
        )
        db.commit()
        _tell(db, patient, "revoked-1")
        _settle(desk)
        _tell(db, patient, "revoked-2")
        _settle(desk)

    rows = _ledger(desk, patient.id)
    assert sorted((row.channel, row.status, row.attempts) for row in rows) == [
        (NotificationChannel.SMS.value, NotificationStatus.SENT.value, 1),
        (NotificationChannel.SMS.value, NotificationStatus.SENT.value, 1),
        (NotificationChannel.WEB_PUSH.value, NotificationStatus.DEAD.value, 1),
    ]
    (dead_push,) = [
        row for row in rows if row.channel == NotificationChannel.WEB_PUSH.value
    ]
    assert "gone" in (dead_push.last_error or "").lower() or "410" in (
        dead_push.last_error or ""
    )
    assert [row.fallback_of_id for row in rows if row.fallback_of_id] == [
        dead_push.id
    ], "the first message fell back from the dead push; the second went straight to SMS"
    assert len(pushes) == 1, "the dead subscription was tried again"
    assert len(sms_calls) == 2
    with desk.session() as db:
        assert (
            db.scalars(
                select(PushSubscription).where(
                    PushSubscription.patient_id == patient.id
                )
            ).all()
            == []
        )
    _all_terminal(desk)


# --- what nobody planned for --------------------------------------------------------------------------------------


def test_an_unplanned_error_is_recorded_and_retried_and_the_sweep_delivers_everything_else(
    desk: SimpleNamespace, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    sms_calls: list[httpx.Request] = []

    def accepts(request: httpx.Request) -> httpx.Response:
        sms_calls.append(request)
        return _recipient("Success", 101)(request)

    real_message_for = service._message_for
    broken: set[str] = set()

    def message_for(
        db: Session, notification: Notification, context: dict | None
    ) -> object:
        if notification.id in broken:
            raise KeyError("a template variable went missing")
        return real_message_for(db, notification, context)

    monkeypatch.setattr(service, "_message_for", message_for)
    # The process "dies" after each commit: the post-commit delivery never runs, so the sweep takes both.
    monkeypatch.setattr(dispatch, "submit", lambda job: None)
    with use_transports(SmsTransport(_gateway(accepts))), desk.session() as db:
        unlucky, lucky = _patient(db), _patient(db)
        db.commit()
        first = _tell(db, unlucky, "unplanned-broken")
        second = _tell(db, lucky, "unplanned-fine")
        broken.add(first.id)
        assert {
            row.status for row in (*_ledger(desk, unlucky.id), *_ledger(desk, lucky.id))
        } == {"queued"}
        _settle(desk, start=now_sast() + timedelta(minutes=5))

    (dead,) = _ledger(desk, unlucky.id)
    (sent,) = _ledger(desk, lucky.id)
    assert sent.status == NotificationStatus.SENT.value and sent.id == second.id
    assert dead.status == NotificationStatus.DEAD.value and dead.attempts == 4
    assert (
        dead.last_error
        == "unexpected error: KeyError: 'a template variable went missing'"
    )
    assert len(sms_calls) == 1
    _all_terminal(desk)


# --- no provider, no credentials --------------------------------------------------------------------------------


#: Every setting that would let the application reach, or be reached by, a message provider.
PROVIDER_CREDENTIALS = (
    "africas_talking_api_key",
    "sms_webhook_token",
    "smtp_password",
    "web_push_vapid_private_key",
    "notification_webhook_secret",
    "team_webhook_url",
)


def test_the_suite_runs_with_no_provider_credentials_and_cannot_reach_a_provider() -> (
    None
):
    """How to verify, step 3: the fixture settings carry no credential, and a real gateway call is stopped."""
    settings = queue_settings()
    assert {
        name: getattr(settings, name) for name in PROVIDER_CREDENTIALS
    } == dict.fromkeys(PROVIDER_CREDENTIALS, "")
    unfaked = AfricasTalkingSmsProvider(_settings())
    with pytest.raises(
        AssertionError, match=r"tried to reach api\.sandbox\.africastalking\.com"
    ):
        unfaked.send_message(to="+27821234567", text="hello", sender="ClinicQ")
