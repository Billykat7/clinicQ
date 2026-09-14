"""Consent: no until the patient says yes, and withdrawal takes effect at once (Issue 21).

Every acceptance criterion, through the API and the two surfaces consent governs:

* every purpose defaults to **not granted**, on every channel, with no row written;
* withdrawing display consent takes the name off the next board render;
* withdrawing notification consent stops the next send, including one already queued, and the
  ledger row says why;
* an answer records the channel it came through, and the history keeps every answer;
* a sign-in code is never blocked by consent: the patient asked for it by typing their number.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import (
    AppEnvironment,
    ConsentPurpose,
    DisplayMode,
    NotificationStatus,
    NotificationTemplate,
    PatientChannel,
)
from src.core import email_send, otp_store, refresh_token_policy, security
from src.core.config import Settings, get_settings
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.database.models import Base, Notification, Patient, PatientConsentEvent
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app
from src.modules.notifications import service as notification_service
from src.modules.notifications.sms import FakeSmsProvider
from src.modules.patients import service as patient_service
from src.modules.patients.consent import (
    board_projection,
    consent_state,
    has_consent,
    record_consent,
)
from src.modules.patients.consent_text import CONSENT_WORDING, CONSENT_WORDING_VERSION

_CONSENTS = "/api/v1/patients/me/consents"
_NUMBER = "0821234567"


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """A signed-in patient, with SMS going to a fake the test can read."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret="consent-test-secret-min-32-characters!!!!",
        smtp_host="",
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with factory() as db:
        sync_rbac_catalog(db)
        db.commit()

    def _db() -> Generator[Session]:
        with factory() as db:
            yield db

    sms = FakeSmsProvider()
    for module in (security, otp_store, refresh_token_policy, email_send):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
    monkeypatch.setattr(notification_service, "build_sms_provider", lambda: sms)
    otp_store.reset_otp_state()
    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: settings

    client = TestClient(app)
    assert (
        client.post("/api/v1/patients/otp/request", json={"phone": _NUMBER}).status_code
        == 202
    )
    import re

    code = re.search(r"\b(\d{6})\b", sms.sent[-1].text).group(1)  # type: ignore[union-attr]
    signed_in = client.post(
        "/api/v1/patients/otp/verify", json={"phone": _NUMBER, "code": code}
    )
    assert signed_in.status_code == status.HTTP_200_OK, signed_in.text
    with factory() as db:
        patient = db.execute(select(Patient)).scalar_one()
        patient.display_name = "Thabo Mokoena"
        db.commit()
        patient_id = patient.id

    yield SimpleNamespace(
        client=client,
        session=factory,
        settings=settings,
        sms=sms,
        patient_id=patient_id,
    )
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _csrf(ctx: SimpleNamespace) -> dict[str, str]:
    """The double-submit header for the patient's session."""
    return {"X-CSRF-Token": ctx.client.cookies.get(ctx.settings.csrf_cookie_name)}


def _set(ctx: SimpleNamespace, purpose: ConsentPurpose, granted: bool):  # type: ignore[no-untyped-def]
    """Answer one question as the patient."""
    return ctx.client.put(
        f"{_CONSENTS}/{purpose.value}", json={"granted": granted}, headers=_csrf(ctx)
    )


def _patient(ctx: SimpleNamespace, db: Session) -> Patient:
    """The patient row in ``db``."""
    return db.execute(select(Patient).where(Patient.id == ctx.patient_id)).scalar_one()


# --- the default is no ---------------------------------------------------------------------


def test_every_purpose_starts_not_granted_with_nothing_written(
    ctx: SimpleNamespace,
) -> None:
    """A patient who has just signed in has agreed to nothing, and no row claims otherwise."""
    answers = ctx.client.get(_CONSENTS)
    assert answers.status_code == status.HTTP_200_OK, answers.text
    body = answers.json()
    assert {a["purpose"] for a in body["answers"]} == {p.value for p in ConsentPurpose}
    assert all(a["granted"] is False for a in body["answers"])
    assert body["wording_version"] == CONSENT_WORDING_VERSION
    assert (
        body["answers"][0]["question"]
        == CONSENT_WORDING[ConsentPurpose(body["answers"][0]["purpose"])]
    )

    with ctx.session() as db:
        assert consent_state(db, ctx.patient_id) == dict.fromkeys(ConsentPurpose, False)
        assert db.execute(select(PatientConsentEvent)).scalars().all() == []


def test_the_default_is_no_on_every_channel(ctx: SimpleNamespace) -> None:
    """A patient who arrives by USSD or WhatsApp has agreed to nothing either."""
    with ctx.session() as db:
        for number, channel in (
            ("+27830001111", PatientChannel.USSD),
            ("+27830002222", PatientChannel.WHATSAPP),
        ):
            patient = patient_service.patient_for_gateway(
                db, msisdn=number, channel=channel
            )
            db.commit()
            assert consent_state(db, patient.id) == dict.fromkeys(ConsentPurpose, False)


# --- the board ------------------------------------------------------------------------------


def test_withdrawing_display_consent_takes_the_name_off_the_next_render(
    ctx: SimpleNamespace,
) -> None:
    """The board reads the current answer, so a withdrawal shows on the very next render."""
    assert (
        _set(ctx, ConsentPurpose.DISPLAY_NAME, True).status_code == status.HTTP_200_OK
    )
    with ctx.session() as db:
        shown = board_projection(
            db, _patient(ctx, db), ticket_number="A014", display_mode=DisplayMode.FULL
        )
    assert shown.name == "Thabo Mokoena"

    withdrawn = _set(ctx, ConsentPurpose.DISPLAY_NAME, False)
    assert withdrawn.status_code == status.HTTP_200_OK
    assert "straight away" in withdrawn.json()["notice"]

    with ctx.session() as db:
        after = board_projection(
            db, _patient(ctx, db), ticket_number="A014", display_mode=DisplayMode.FULL
        )
    assert after.name is None and after.comment is None
    assert after.ticket_number == "A014"


def test_the_comment_needs_its_own_consent_and_the_display_mode(
    ctx: SimpleNamespace,
) -> None:
    """Health information beside a name is a separate question, and number-only never shows it."""
    _set(ctx, ConsentPurpose.DISPLAY_NAME, True)
    with ctx.session() as db:
        patient = _patient(ctx, db)
        with_name = board_projection(
            db,
            patient,
            ticket_number="A015",
            display_mode=DisplayMode.FULL,
            comment="headache",
        )
        assert with_name.name == "Thabo Mokoena" and with_name.comment is None

    _set(ctx, ConsentPurpose.DISPLAY_COMMENT, True)
    with ctx.session() as db:
        patient = _patient(ctx, db)
        # Standing consent alone is not enough: the reason needs this visit's agreement too (Issue 58).
        standing_only = board_projection(
            db,
            patient,
            ticket_number="A015",
            display_mode=DisplayMode.FULL,
            comment="headache",
        )
        assert standing_only.name == "Thabo Mokoena" and standing_only.comment is None
        both = board_projection(
            db,
            patient,
            ticket_number="A015",
            display_mode=DisplayMode.FULL,
            comment="headache",
            visit_comment_consent=True,
        )
        assert both.name == "Thabo Mokoena" and both.comment == "headache"

        # The site's own setting still decides first (non-negotiable 4).
        number_only = board_projection(
            db,
            patient,
            ticket_number="A015",
            display_mode=DisplayMode.NUMBER_ONLY,
            comment="headache",
            visit_comment_consent=True,
        )
        assert number_only.name is None and number_only.comment is None
        lite = board_projection(
            db,
            patient,
            ticket_number="A015",
            display_mode=DisplayMode.NAME_LITE,
            comment="headache",
            visit_comment_consent=True,
        )
        assert lite.name == "Thabo M." and lite.comment is None


# --- messages -------------------------------------------------------------------------------


def test_without_consent_a_message_is_not_sent_and_the_ledger_says_why(
    ctx: SimpleNamespace,
) -> None:
    """A patient who never agreed is not messaged: suppressed, with the reason on the row."""
    with ctx.session() as db:
        sent_before = len(ctx.sms.sent)
        notification = notification_service.send_sms(
            db,
            to="+27821234567",
            template=NotificationTemplate.GENERIC,
            context={"text": "You are next in the queue."},
            provider=ctx.sms,
        )
        db.commit()
        outcome, reason = notification.status, notification.last_error
    assert outcome == NotificationStatus.SUPPRESSED.value
    assert reason == "suppressed by preference (no-patient-consent)"
    assert len(ctx.sms.sent) == sent_before


def test_with_consent_the_same_message_goes_out(ctx: SimpleNamespace) -> None:
    """And the check is discriminating: saying yes lets it through."""
    _set(ctx, ConsentPurpose.NOTIFICATIONS, True)
    with ctx.session() as db:
        notification = notification_service.send_sms(
            db,
            to="+27821234567",
            template=NotificationTemplate.GENERIC,
            context={"text": "You are next in the queue."},
            provider=ctx.sms,
        )
        db.commit()
        outcome = notification.status
    assert outcome == NotificationStatus.SENT.value
    assert ctx.sms.sent[-1].text == "You are next in the queue."


def test_withdrawing_stops_a_message_that_was_already_queued(
    ctx: SimpleNamespace,
) -> None:
    """Immediately means immediately: a queued message is re-checked when it is delivered."""
    _set(ctx, ConsentPurpose.NOTIFICATIONS, True)
    with ctx.session() as db:
        queued = notification_service.enqueue(
            db,
            channel=notification_service.NotificationChannel.SMS,
            template=NotificationTemplate.GENERIC,
            recipient="+27821234567",
            subject=None,
            payload={"text": "It is nearly your turn."},
        )
        db.commit()
        queued_id = queued.id

    _set(ctx, ConsentPurpose.NOTIFICATIONS, False)

    with ctx.session() as db:
        delivered = notification_service.run_retry_sweep(db, provider=ctx.sms)
        db.commit()
        row = db.execute(
            select(Notification).where(Notification.id == queued_id)
        ).scalar_one()
    assert delivered == 0
    assert row.status == NotificationStatus.SUPPRESSED.value
    assert "no-patient-consent" in (row.last_error or "")
    assert all("nearly your turn" not in message.text for message in ctx.sms.sent)


def test_a_sign_in_code_is_never_blocked_by_consent(ctx: SimpleNamespace) -> None:
    """The code is the service the patient asked for by typing their number, not marketing."""
    with ctx.session() as db:
        assert not has_consent(db, ctx.patient_id, ConsentPurpose.NOTIFICATIONS)
    sent_before = len(ctx.sms.sent)

    fresh = TestClient(ctx.client.app)
    assert (
        fresh.post(
            "/api/v1/patients/otp/request", json={"phone": "0721111111"}
        ).status_code
        == 202
    )
    assert len(ctx.sms.sent) == sent_before + 1


# --- what the record keeps --------------------------------------------------------------


def test_an_answer_records_the_channel_it_came_through_and_keeps_the_history(
    ctx: SimpleNamespace,
) -> None:
    """Consent history is the proof; the current row is the answer."""
    _set(ctx, ConsentPurpose.NOTIFICATIONS, True)
    _set(ctx, ConsentPurpose.NOTIFICATIONS, False)
    with ctx.session() as db:
        patient = _patient(ctx, db)
        # The same question answered at the desk, by a staff member, on the patient's behalf.
        record_consent(
            db,
            patient,
            ConsentPurpose.NOTIFICATIONS,
            granted=True,
            channel=PatientChannel.WALK_IN,
            recorded_by="0199b0c0-0000-7000-8000-00000000000f",
            site_id="hillbrow-chc",
        )
        db.commit()
        events = (
            db.execute(
                select(PatientConsentEvent)
                .where(
                    PatientConsentEvent.purpose == ConsentPurpose.NOTIFICATIONS.value
                )
                .order_by(PatientConsentEvent.created_at)
            )
            .scalars()
            .all()
        )
        state = consent_state(db, ctx.patient_id)

    assert [event.granted for event in events] == [True, False, True]
    assert [event.source_channel for event in events] == [
        PatientChannel.WEB.value,
        PatientChannel.WEB.value,
        PatientChannel.WALK_IN.value,
    ]
    assert events[-1].recorded_by is not None and events[-1].site_id == "hillbrow-chc"
    assert all(event.wording_version == CONSENT_WORDING_VERSION for event in events)
    assert state[ConsentPurpose.NOTIFICATIONS] is True


def test_a_patient_cannot_answer_for_anyone_else(ctx: SimpleNamespace) -> None:
    """The route takes no patient id: the session is the identity (Issue 17)."""
    signed_out = TestClient(ctx.client.app)
    assert signed_out.get(_CONSENTS).status_code == status.HTTP_401_UNAUTHORIZED
    assert (
        signed_out.put(
            f"{_CONSENTS}/{ConsentPurpose.NOTIFICATIONS.value}", json={"granted": True}
        ).status_code
        == status.HTTP_401_UNAUTHORIZED
    )


def test_an_unknown_purpose_is_refused(ctx: SimpleNamespace) -> None:
    """The vocabulary is closed: a purpose nobody defined cannot be granted."""
    response = ctx.client.put(
        f"{_CONSENTS}/whatever_we_like", json={"granted": True}, headers=_csrf(ctx)
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
