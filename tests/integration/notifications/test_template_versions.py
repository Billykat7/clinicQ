"""Template versions on the ledger, the editor's API, and the patient's language (Issue 66).

Against the queue fixture's clinics, the real notification service and the real template routes:

* **the version used for a send is recorded on the ledger**, and looking that version up gives the exact
  words: send, edit the template, then look the send up (How to verify, step 3);
* **a retry says what the first attempt said**, even when the template was edited in between, while the
  next new message uses the new version;
* **words that break the variable contract cannot be published**, and the preview says why, and counts an
  SMS's characters and segments;
* **a patient's language is used when its words exist**, and English when they do not.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette import status

from src.commons.enums import (
    BoardLanguage,
    ConsentPurpose,
    NotificationChannel,
    NotificationStatus,
    PatientChannel,
    PatientEvent,
    UserRole,
)
from src.commons.time import now_sast, stored_sast
from src.database.models import (
    Notification,
    NotificationTemplateVersion,
    Patient,
    PatientNotificationPreference,
)
from src.modules.notifications import service
from src.modules.notifications import template_registry as registry
from src.modules.notifications.transports import NoopTransport, use_transports
from src.modules.patients.consent import record_consent
from tests.factories import FACTORY_STAFF_PASSWORD, PatientFactory, StaffFactory
from tests.integration.queue.conftest import SITE_A

_EDITED = "{app}: ticket {number}, it is your turn. Go to {where} at {clinic} now."


def _operator(desk: SimpleNamespace) -> TestClient:
    """A platform administrator (the kernel's ``admin`` role, which holds ``logs``), signed in."""
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


def _patient(db: Session, language: BoardLanguage | None = None) -> Patient:
    patient = PatientFactory.create(db)
    record_consent(
        db,
        patient,
        ConsentPurpose.NOTIFICATIONS,
        granted=True,
        channel=PatientChannel.WEB,
    )
    if language is not None:
        db.add(
            PatientNotificationPreference(
                patient_id=patient.id, language=language.value
            )
        )
        db.flush()
    return patient


def _called(db: Session, patient: Patient, key: str) -> Notification:
    row = service.notify(
        db,
        patient_id=patient.id,
        event=PatientEvent.CALLED,
        context={
            "number": "T007",
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


_BASE = "/api/v1/notifications/templates"


def test_the_ledger_names_the_version_sent_and_a_retry_repeats_it_after_an_edit(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 3: send a message, edit the template, then look the send up."""
    operator = _operator(desk)
    sms = NoopTransport(channel=NotificationChannel.SMS, fail_times=1)
    with use_transports(sms), desk.session() as db:
        # Attempt 1 fails, so a retry is due when the template is edited.
        first = _called(db, _patient(db), "before-edit")
        version_one = db.get_one(NotificationTemplateVersion, first.template_version_id)

        edited = operator.post(
            f"{_BASE}/ticket_called/sms/en/versions", json={"body": _EDITED}
        )
        assert edited.status_code == status.HTTP_201_CREATED, edited.text
        assert edited.json()["version"] == version_one.version + 1

        due = stored_sast(db.get_one(Notification, first.id).next_attempt_at)
        service.run_retry_sweep(db, now=due + timedelta(seconds=1))
        db.commit()
        second = _called(db, _patient(db), "after-edit")

    retried_text, new_text = sms.sent[0].text, sms.sent[1].text
    assert "please come in now to Room 2" in retried_text, (
        "the retry used the edited words"
    )
    assert (
        "it is your turn" in new_text
        and second.template_version_id == edited.json()["id"]
    )

    looked_up = operator.get(f"/api/v1/notifications/{first.id}").json()
    assert looked_up["status"] == NotificationStatus.SENT.value
    assert (looked_up["template_version_id"], looked_up["language"]) == (
        version_one.id,
        "en",
    )
    words = operator.get(f"{_BASE}/versions/{looked_up['template_version_id']}").json()
    assert words["body"] == version_one.body and words["version"] == version_one.version
    rendered = registry.render_version(version_one, first.payload).text
    assert rendered == retried_text, (
        "the stored version and context reproduce the message exactly"
    )


def test_words_that_break_the_contract_are_refused_and_the_preview_says_why(
    desk: SimpleNamespace,
) -> None:
    operator = _operator(desk)
    refused = operator.post(
        f"{_BASE}/ticket_recalled/sms/en/versions",
        json={"body": "{app}: ticket {number}, come within {minuts} minutes."},
    )
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "{minuts} cannot be used" in refused.json()["detail"]

    push = operator.post(
        f"{_BASE}/ticket_next/web_push/en/versions",
        json={"subject": "You are next", "body": "Ticket {number}: go to {room}."},
    )
    assert push.status_code == 422 and "web push" in push.json()["detail"]

    bad = operator.post(
        f"{_BASE}/preview",
        json={
            "template": "ticket_next",
            "channel": "sms",
            "language": "en",
            "body": "Hello {clinic}.",
        },
    ).json()
    assert bad == {
        **bad,
        "valid": False,
        "error": "The message must say the ticket number: add {number}.",
    }
    good = operator.post(
        f"{_BASE}/preview",
        json={
            "template": "ticket_transferred",
            "channel": "sms",
            "language": "en",
            "body": "{app}: ticket {number} in {queue}. Wait {wait}.",
        },
    ).json()
    assert (
        good["valid"] is True and good["segments"] == 1 and good["encoding"] == "gsm7"
    )
    # The en dash is sent as a hyphen, and counted so.
    assert "Wait ~15-25 min." in good["text"]
    assert good["worst_segments"] == 1 and good["worst_characters"] > good["characters"]

    assert desk.staff("desk.a").get(_BASE).status_code == status.HTTP_403_FORBIDDEN
    listing = operator.get(_BASE).json()
    assert (
        listing["languages"] == ["en"]
        and len(listing["items"]) == len(PatientEvent) * 3
    )


@pytest.fixture
def isizulu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Locale files for English and a test isiZulu: the shipped English, and made-up words for the test."""
    english = (registry.LOCALES_DIR / "en" / "notifications.toml").read_text(
        encoding="utf-8"
    )
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "notifications.toml").write_text(english, encoding="utf-8")
    (tmp_path / "zu").mkdir()
    (tmp_path / "zu" / "notifications.toml").write_text(
        '[ticket_called.sms]\nversion = 1\nbody = "{app}: ithikithi {number}, ngena manje ku-{where}."\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(registry, "LOCALES_DIR", tmp_path)
    registry.builtin.cache_clear()
    yield tmp_path
    registry.builtin.cache_clear()


def test_a_patients_language_is_used_when_its_words_exist_and_english_otherwise(
    desk: SimpleNamespace, isizulu: Path
) -> None:
    sms = NoopTransport(channel=NotificationChannel.SMS)
    with use_transports(sms), desk.session() as db:
        zulu = _called(db, _patient(db, BoardLanguage.ISIZULU), "zu")
        xhosa = _called(db, _patient(db, BoardLanguage.ISIXHOSA), "xh")
        rows = {
            row.id: row.language
            for row in db.scalars(
                select(Notification).where(Notification.id.in_([zulu.id, xhosa.id]))
            )
        }
    assert rows == {zulu.id: "zu", xhosa.id: "en"}
    assert "ithikithi T007" in sms.sent[0].text
    assert "please come in now" in sms.sent[1].text
