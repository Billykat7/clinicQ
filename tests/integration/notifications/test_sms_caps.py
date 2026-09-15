"""What SMS may cost: caps, the kill switch, receipts, the gateway and message length (Issue 65).

Against the queue fixture's clinics and the real notification service, with the gateway's HTTP answered
in the test (``httpx.MockTransport``): no account, no network, no spend. What is shown:

* **a cap stops sends and alerts**: a clinic with a cap of 3 sends 3 SMS; the 4th and 5th are recorded as
  not sent with the reason, and the team is alerted once, not twice;
* **a patient's own cap** stops a loop on one ticket the same way;
* **the kill switch stops every SMS at once, with no deploy**: the next SMS after the switch is flipped over
  HTTP is not sent, sign-in codes included, and flipping it back lets them go again;
* **delivery receipts reach a terminal status**, once however often the gateway repeats them, and only for
  a caller holding the callback's secret;
* **every message's cost is recorded and reportable per site**, from what the gateway said it charged;
* **no message silently splits**: an en dash is sent as a hyphen, and a message longer than
  ``SMS_MAX_SEGMENTS`` is refused rather than sent in three billable parts;
* **sandbox mode**: the gateway adapter talks to the sandbox with the sandbox account by default.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette import status

from src.commons.enums import (
    AppEnvironment,
    ConsentPurpose,
    NotificationStatus,
    NotificationTemplate,
    PatientChannel,
    PatientEvent,
    SmsBlockReason,
    SmsProviderKind,
    UserRole,
)
from src.core.config import Settings, get_settings
from src.database.models import Notification, Patient
from src.modules.notifications import budget, service
from src.modules.notifications.sms import (
    AfricasTalkingSmsProvider,
    FakeSmsProvider,
    SmsRecipientRejectedError,
    SmsSendError,
)
from src.modules.notifications.transports import use_transports
from src.modules.notifications.transports.sms import SmsTransport
from src.modules.patients.consent import record_consent
from tests.factories import FACTORY_STAFF_PASSWORD, PatientFactory, StaffFactory
from tests.integration.queue.conftest import SITE_A, queue_settings

_TOKEN = "cb" * 22  # the callback secret registered with the gateway, in this test only


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every team alert the budget posts, instead of posting it."""
    posted: list[str] = []
    monkeypatch.setattr(budget, "post_team_alert", posted.append)
    return posted


@pytest.fixture
def gateway() -> Iterator[FakeSmsProvider]:
    """A fake gateway charging R0.25 a message, installed as the SMS transport."""
    provider = FakeSmsProvider(cost_per_message=Decimal("0.2500"))
    with use_transports(SmsTransport(provider)):
        yield provider


def _patient(db: Session) -> Patient:
    patient = PatientFactory.create(db)
    record_consent(
        db,
        patient,
        ConsentPurpose.NOTIFICATIONS,
        granted=True,
        channel=PatientChannel.WEB,
    )
    return patient


def _tell(db: Session, patient: Patient, key: str) -> Notification:
    """One "please come in" SMS to ``patient`` from clinic A, committed and delivered."""
    row = service.notify(
        db,
        patient_id=patient.id,
        event=PatientEvent.CALLED,
        context={
            "number": "T001",
            "clinic": "Zola Clinic",
            "queue": "Triage",
            "room": "Room 2",
        },
        site_id=SITE_A,
        dedupe_key=key,
    )
    db.commit()
    assert row is not None
    db.refresh(row)
    return row


# --- caps --------------------------------------------------------------------------------------------


def test_a_site_cap_of_3_stops_the_4th_sms_and_alerts_once(
    desk: SimpleNamespace, gateway: FakeSmsProvider, alerts: list[str]
) -> None:
    """How to verify, step 1."""
    manager = desk.staff("manager.a")
    capped = manager.put(f"/api/v1/sites/{SITE_A}/sms-budget", json={"daily_cap": 3})
    assert capped.status_code == status.HTTP_200_OK, capped.text
    assert (capped.json()["cap"], capped.json()["own_cap"]) == (3, 3)

    with desk.session() as db:
        rows = [_tell(db, _patient(db), f"cap-{n}") for n in range(5)]

    assert [row.status for row in rows] == [NotificationStatus.SENT.value] * 3 + [
        NotificationStatus.SUPPRESSED.value
    ] * 2
    assert {row.last_error for row in rows[3:]} == {
        f"SMS not sent: {SmsBlockReason.SITE_DAILY_CAP.value}"
    }
    assert len(gateway.sent) == 3
    assert len(alerts) == 1 and "SMS cap reached" in alerts[0] and "3 SMS" in alerts[0]

    spend = manager.get(f"/api/v1/sites/{SITE_A}/sms-budget").json()
    assert (spend["sent"], spend["remaining"], spend["currency"]) == (3, 0, "ZAR")
    assert Decimal(spend["spend"]) == Decimal("0.7500")
    assert (
        desk.staff("desk.a")
        .put(f"/api/v1/sites/{SITE_A}/sms-budget", json={"daily_cap": 1000})
        .status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_a_patients_own_cap_stops_a_loop_on_one_ticket(
    desk: SimpleNamespace,
    gateway: FakeSmsProvider,
    alerts: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        budget, "get_settings", lambda: queue_settings(sms_patient_daily_cap=2)
    )
    with desk.session() as db:
        patient = _patient(db)
        rows = [_tell(db, patient, f"loop-{n}") for n in range(4)]
    assert [row.status for row in rows] == [NotificationStatus.SENT.value] * 2 + [
        NotificationStatus.SUPPRESSED.value
    ] * 2
    assert (
        rows[2].last_error == f"SMS not sent: {SmsBlockReason.PATIENT_DAILY_CAP.value}"
    )
    assert len(gateway.sent) == 2 and len(alerts) == 1


# --- the kill switch -----------------------------------------------------------------------------------


def _operator(desk: SimpleNamespace) -> TestClient:
    """A platform administrator: the kernel's ``admin`` role, which holds ``logs``."""
    with desk.session() as db:
        StaffFactory.create(db, email="ops@clinicq.example", role=UserRole.ADMIN)
        db.commit()
    client = TestClient(desk.app)
    client.post(
        "/api/v1/auth/password/login",
        json={"email": "ops@clinicq.example", "password": FACTORY_STAFF_PASSWORD},
    )
    token = client.cookies.get(desk.settings.csrf_cookie_name)
    if token:
        client.headers["X-CSRF-Token"] = token
    return client


def test_the_kill_switch_stops_every_sms_on_the_next_message_with_no_deploy(
    desk: SimpleNamespace, gateway: FakeSmsProvider, alerts: list[str]
) -> None:
    """How to verify, step 2: flip it over HTTP, and the very next SMS is not sent."""
    operator = _operator(desk)
    assert (
        desk.staff("manager.a")
        .put("/api/v1/notifications/sms/kill-switch", json={"enabled": True})
        .status_code
        == status.HTTP_403_FORBIDDEN
    )

    flipped_at = time.monotonic()
    on = operator.put(
        "/api/v1/notifications/sms/kill-switch",
        json={"enabled": True, "reason": "gateway billing looks wrong"},
    )
    assert on.status_code == status.HTTP_200_OK, on.text
    with desk.session() as db:
        queue_sms = _tell(db, _patient(db), "after-kill")
        code = service.send_sms(
            db,
            to="+27831112222",
            template=NotificationTemplate.OTP_SIGN_IN,
            context={"code": "123456", "ttl_minutes": 10},
            provider=gateway,
        )
        db.commit()
    stopped_within = time.monotonic() - flipped_at
    message = f"first SMS after the switch refused {stopped_within * 1000:.0f} ms after it was flipped"
    print("\n" + message)  # noqa: T201

    assert queue_sms.status == NotificationStatus.SUPPRESSED.value
    assert code.status == NotificationStatus.SUPPRESSED.value
    assert (
        queue_sms.last_error
        == code.last_error
        == f"SMS not sent: {SmsBlockReason.KILL_SWITCH.value}"
    )
    assert gateway.sent == [] and stopped_within < 60
    state = operator.get("/api/v1/notifications/sms/kill-switch").json()
    assert state["enabled"] is True and state["changed_by"] == "ops@clinicq.example"
    assert (
        alerts
        and "kill switch ON" in alerts[0]
        and "gateway billing looks wrong" in alerts[0]
    )

    operator.put("/api/v1/notifications/sms/kill-switch", json={"enabled": False})
    with desk.session() as db:
        again = _tell(db, _patient(db), "after-restore")
    assert again.status == NotificationStatus.SENT.value and len(gateway.sent) == 1


# --- receipts ------------------------------------------------------------------------------------------


@pytest.fixture
def receipts(desk: SimpleNamespace) -> SimpleNamespace:
    """The receipt webhook switched on with a callback secret."""
    settings = queue_settings(sms_webhook_token=_TOKEN)
    desk.app.dependency_overrides[get_settings] = lambda: settings
    return SimpleNamespace(
        client=TestClient(desk.app),
        path=f"/api/v1/webhooks/sms/africastalking/{_TOKEN}",
    )


def _receipt(client: TestClient, path: str, **fields: str) -> httpx.Response:
    return client.post(
        path,
        content=urlencode(fields),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def test_delivery_receipts_end_in_a_terminal_status_once(
    desk: SimpleNamespace, gateway: FakeSmsProvider, receipts: SimpleNamespace
) -> None:
    with desk.session() as db:
        delivered = _tell(db, _patient(db), "receipt-ok")
        failed = _tell(db, _patient(db), "receipt-bad")

    first = _receipt(
        receipts.client,
        receipts.path,
        id=delivered.provider_message_id,
        status="Success",
    )
    replay = _receipt(
        receipts.client,
        receipts.path,
        id=delivered.provider_message_id,
        status="Success",
    )
    in_transit = _receipt(
        receipts.client, receipts.path, id=failed.provider_message_id, status="Buffered"
    )
    gone = _receipt(
        receipts.client,
        receipts.path,
        id=failed.provider_message_id,
        status="Failed",
        failureReason="AbsentSubscriber",
    )
    unknown = _receipt(
        receipts.client, receipts.path, id="ATXid_nobody", status="Success"
    )

    assert [
        r.json()["outcome"] for r in (first, replay, in_transit, gone, unknown)
    ] == [
        "processed",
        "duplicate",
        "processed",
        "processed",
        "ignored",
    ]
    with desk.session() as db:
        ok = db.get_one(Notification, delivered.id)
        bad = db.get_one(Notification, failed.id)
    assert (ok.status, ok.delivered_at is not None) == (
        NotificationStatus.DELIVERED.value,
        True,
    )
    assert bad.status == NotificationStatus.DEAD.value
    assert bad.last_error == "delivery failed: AbsentSubscriber"


def test_a_receipt_without_the_callback_secret_changes_nothing(
    desk: SimpleNamespace, gateway: FakeSmsProvider, receipts: SimpleNamespace
) -> None:
    with desk.session() as db:
        row = _tell(db, _patient(db), "receipt-forged")
    forged = _receipt(
        receipts.client,
        "/api/v1/webhooks/sms/africastalking/" + "x" * 44,
        id=row.provider_message_id,
        status="Failed",
    )
    garbage = receipts.client.post(receipts.path, content=b"\xff\xfe")
    assert forged.status_code == status.HTTP_404_NOT_FOUND
    assert garbage.status_code == status.HTTP_400_BAD_REQUEST
    with desk.session() as db:
        assert db.get_one(Notification, row.id).status == NotificationStatus.SENT.value

    desk.app.dependency_overrides[get_settings] = lambda: queue_settings()
    off = _receipt(
        receipts.client, receipts.path, id=row.provider_message_id, status="Success"
    )
    assert off.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


# --- the gateway adapter ------------------------------------------------------------------------------


def _gateway_answering(
    recipient: dict[str, object], seen: list[httpx.Request]
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            201,
            json={
                "SMSMessageData": {"Message": "Sent to 1/1", "Recipients": [recipient]}
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def _at_settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=AppEnvironment.DEVELOPMENT,
        sms_provider=SmsProviderKind.AFRICAS_TALKING,
        africas_talking_api_key="at-test-key",
        **overrides,  # type: ignore[arg-type]
    )


def test_sandbox_is_the_default_so_local_development_spends_nothing() -> None:
    """How the adapter talks to the gateway: sandbox URL and account unless production says otherwise."""
    seen: list[httpx.Request] = []
    provider = AfricasTalkingSmsProvider(
        _at_settings(),
        client=_gateway_answering(
            {
                "statusCode": 101,
                "status": "Success",
                "messageId": "ATXid_1",
                "cost": "ZAR 0.0000",
            },
            seen,
        ),
    )
    receipt = provider.send_message(to="+27821234567", text="hello", sender="ClinicQ")
    (request,) = seen
    form = parse_qs(request.content.decode())
    assert (
        str(request.url) == "https://api.sandbox.africastalking.com/version1/messaging"
    )
    assert form == {
        "username": ["sandbox"],
        "to": ["+27821234567"],
        "message": ["hello"],
    }
    assert request.headers["apiKey"] == "at-test-key"
    assert (receipt.message_id, receipt.cost, receipt.currency) == (
        "ATXid_1",
        Decimal("0.0000"),
        "ZAR",
    )

    live_seen: list[httpx.Request] = []
    live = AfricasTalkingSmsProvider(
        _at_settings(sms_sandbox=False, africas_talking_username="clinicq"),
        client=_gateway_answering(
            {
                "statusCode": 101,
                "status": "Success",
                "messageId": "ATXid_2",
                "cost": "ZAR 0.2500",
            },
            live_seen,
        ),
    )
    assert live.send_message(
        to="+27821234567", text="hi", sender="ClinicQ"
    ).cost == Decimal("0.2500")
    live_form = parse_qs(live_seen[0].content.decode())
    assert str(live_seen[0].url).startswith("https://api.africastalking.com/")
    assert live_form["username"] == ["clinicq"] and live_form["from"] == ["ClinicQ"]


@pytest.mark.parametrize(
    ("code", "error"),
    [
        (403, SmsRecipientRejectedError),
        (406, SmsRecipientRejectedError),
        (405, SmsSendError),
        (500, SmsSendError),
    ],
)
def test_the_gateways_refusals_are_told_apart(
    code: int, error: type[Exception]
) -> None:
    """An invalid or opted-out number is never retried; a balance or gateway problem is."""
    provider = AfricasTalkingSmsProvider(
        _at_settings(),
        client=_gateway_answering(
            {"statusCode": code, "status": "x", "messageId": "None"}, []
        ),
    )
    with pytest.raises(error):
        provider.send_message(to="+27821234567", text="hi", sender="")


def test_the_cost_the_gateway_reports_is_the_cost_recorded(
    desk: SimpleNamespace,
) -> None:
    provider = AfricasTalkingSmsProvider(
        _at_settings(),
        client=_gateway_answering(
            {
                "statusCode": 101,
                "status": "Success",
                "messageId": "ATXid_9",
                "cost": "ZAR 0.3100",
            },
            [],
        ),
    )
    with use_transports(SmsTransport(provider)), desk.session() as db:
        row = _tell(db, _patient(db), "priced")
    assert (row.provider, row.cost, row.cost_currency) == (
        SmsProviderKind.AFRICAS_TALKING.value,
        Decimal("0.3100"),
        "ZAR",
    )


# --- length and encoding ----------------------------------------------------------------------------------


def test_a_message_that_would_split_beyond_the_limit_is_refused_not_sent(
    desk: SimpleNamespace, gateway: FakeSmsProvider
) -> None:
    with desk.session() as db:
        row = service.send_sms(
            db,
            to="+27831112222",
            template=NotificationTemplate.GENERIC,
            context={"text": "A message nobody should pay three times for. " * 8},
            provider=gateway,
        )
        db.commit()
    assert row.status == NotificationStatus.DEAD.value and row.attempts == 1
    assert (row.last_error or "").startswith(
        f"{SmsBlockReason.TOO_LONG.value}: 3 gsm7 segments"
    )
    assert gateway.sent == []


def test_a_dash_is_sent_as_gsm_so_the_message_stays_one_segment(
    desk: SimpleNamespace, gateway: FakeSmsProvider
) -> None:
    with desk.session() as db:
        service.notify(
            db,
            patient_id=_patient(db).id,
            event=PatientEvent.TRANSFERRED,
            context={
                "number": "D001",
                "clinic": "Zola Clinic",
                "queue": "Doctor",
                "room": None,
                "wait": "~15–25 min",
            },
            site_id=SITE_A,
        )
        db.commit()
    (sent,) = gateway.sent
    assert "~15-25 min" in sent.text and "–" not in sent.text
