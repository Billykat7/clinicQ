"""The SMS feature flag: ``SMS_ENABLED`` switches SMS on or off for a deployment, off by default.

With the flag off, and whatever the provider settings say:

* **no SMS reaches a provider**: a patient reachable only by SMS gets a ledger row saying SMS is switched
  off, and a kernel SMS (a staff invitation, a sign-in code) is recorded as not sent, ``disabled``;
* **web push keeps its full retry budget**, because SMS is not in the chain behind it to fall back to;
* **sign-in by SMS code answers 503** with ``patients.otp.sms_disabled``, and issues no code;
* **the operators see it**: the kill switch answers with the flag beside the switch.

With the flag on (the rest of the suite runs so), SMS behaves as before.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette import status

from src.commons.enums import (
    ConsentPurpose,
    NotificationChannel,
    NotificationStatus,
    NotificationTemplate,
    PatientChannel,
    PatientEvent,
    UserRole,
)
from src.commons.time import now_sast
from src.core.config import Settings
from src.database.models import Patient
from src.modules.notifications import budget, service
from src.modules.notifications import router as notifications_router
from src.modules.notifications.sms import FakeSmsProvider
from src.modules.notifications.transports import NoopTransport, use_transports
from src.modules.notifications.transports.sms import SmsTransport
from src.modules.patients import service as patient_service
from src.modules.patients.consent import record_consent
from tests.factories import FACTORY_STAFF_PASSWORD, PatientFactory, StaffFactory
from tests.integration.queue.conftest import SITE_A, queue_settings


def test_sms_is_off_unless_the_environment_turns_it_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SMS_ENABLED", raising=False)
    assert Settings(_env_file=None).sms_enabled is False  # type: ignore[call-arg]
    for raw, expected in (("true", True), ("1", True), ("false", False), ("0", False)):
        monkeypatch.setenv("SMS_ENABLED", raw)
        assert Settings(_env_file=None).sms_enabled is expected  # type: ignore[call-arg]


@pytest.fixture
def sms_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Settings with SMS switched off, in every module that reads the flag."""
    settings = queue_settings(sms_enabled=False)
    for module in (service, budget, patient_service, notifications_router):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    yield settings


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


def _called(db: Session, patient: Patient, key: str):  # type: ignore[no-untyped-def]
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
    db.commit()
    assert row is not None
    db.refresh(row)
    return row


def test_with_sms_off_no_sms_reaches_a_provider(
    desk: SimpleNamespace, sms_off: Settings
) -> None:
    gateway = FakeSmsProvider()
    with use_transports(SmsTransport(gateway)), desk.session() as db:
        patient_row = _called(db, _patient(db), "sms-off-patient")
        kernel_row = service.send_sms(
            db,
            to="+27831112222",
            template=NotificationTemplate.GENERIC,
            context={"text": "hello"},
            provider=gateway,
        )
        db.commit()

    assert (patient_row.status, patient_row.last_error) == (
        NotificationStatus.SUPPRESSED.value,
        "no transport can reach the patient (SMS is switched off: SMS_ENABLED)",
    )
    assert (kernel_row.status, kernel_row.last_error) == (
        NotificationStatus.SUPPRESSED.value,
        "SMS not sent: disabled",
    )
    assert gateway.sent == []


def test_with_sms_off_web_push_keeps_its_retries_and_never_falls_back_to_sms(
    desk: SimpleNamespace, sms_off: Settings
) -> None:
    push = NoopTransport(
        channel=NotificationChannel.WEB_PUSH, free=True, address="sub", fail_times=1
    )
    sms = NoopTransport(channel=NotificationChannel.SMS, address="+27820000001")
    with use_transports(push, sms), desk.session() as db:
        row = _called(db, _patient(db), "sms-off-push")

    assert row.channel == NotificationChannel.WEB_PUSH.value
    assert row.max_attempts == sms_off.notification_max_attempts
    assert (row.status, row.attempts) == (NotificationStatus.FAILED.value, 1)
    assert sms.attempts == 0 and sms.sent == []


def test_with_sms_off_sign_in_by_code_answers_503_and_issues_no_code(
    desk: SimpleNamespace, sms_off: Settings
) -> None:
    gateway = FakeSmsProvider()
    with use_transports(SmsTransport(gateway)):
        answer = TestClient(desk.app).post(
            "/api/v1/patients/otp/request", json={"phone": "0821234567"}
        )
    assert answer.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert answer.json()["code"] == "patients.otp.sms_disabled"
    assert answer.json()["detail"].startswith("Sign-in by SMS code is not available")
    assert gateway.sent == []


def test_the_kill_switch_answer_shows_the_flag(
    desk: SimpleNamespace, sms_off: Settings
) -> None:
    with desk.session() as db:
        StaffFactory.create(db, email="ops@clinicq.example", role=UserRole.ADMIN)
        db.commit()
    operator = TestClient(desk.app)
    operator.post(
        "/api/v1/auth/password/login",
        json={"email": "ops@clinicq.example", "password": FACTORY_STAFF_PASSWORD},
    )
    answer = operator.get("/api/v1/notifications/sms/kill-switch")
    assert answer.status_code == status.HTTP_200_OK, answer.text
    assert answer.json() == {
        "enabled": False,
        "sms_enabled": False,
        "reason": None,
        "changed_by": None,
        "changed_at": None,
    }
