"""Unit tests for the notification service core (Issue 67 / M11).

The notification service is the one place transactional email and SMS are delivered from, with a
durable per-message status. These tests exercise its essential logic directly against an in-memory
database (per ``.cursor/rules/testing-strategy.mdc``, unit tests are reserved for exactly this kind
of non-trivial core behaviour):

* an SMS is recorded and delivered through the pluggable provider, and its OTP body renders;
* a transient failure is recorded with an exponential-backoff ``next_attempt_at`` — never lost;
* the retry sweep re-attempts a due failure and eventually **dead-letters** it once the attempt
  budget is spent;
* a provider delivery-status callback advances a row to ``delivered`` (and 404s on an unknown id);
* the migrated email path records a ledger row, marks it ``sent``, and re-raises
  :class:`EmailDeliveryError` on failure so existing callers behave exactly as before.

The email tests inject the test session into the service's own-transaction helpers and stub the
raw SMTP transport, so no message ever reaches a network.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.commons.enums import (
    NotificationChannel,
    NotificationStatus,
    NotificationTemplate,
)
from src.core.s3_logging import APP_TIMEZONE
from src.database.models import Base
from src.database.models.notification import Notification
from src.database.schema import sqlite_schema_translate_map
from src.modules.notifications import service, templates
from src.modules.notifications.schemas import DeliveryReceipt
from src.modules.notifications.sms import FakeSmsProvider


@pytest.fixture
def db() -> Generator[Session]:
    """An in-memory SQLite session with the ORM tables (schema mapped away for SQLite)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _rows(db: Session) -> list[Notification]:
    """All notification rows, for assertions."""
    return list(db.execute(select(Notification)).scalars().all())


# --- SMS delivery -------------------------------------------------------------


def test_send_sms_records_and_delivers(db: Session) -> None:
    """A sent SMS is recorded ``sent`` with a provider id, and the OTP body carries the code."""
    provider = FakeSmsProvider()
    notification = service.send_sms(
        db,
        to="+27831112222",
        template=NotificationTemplate.OTP_SIGN_IN,
        context={"code": "123456", "ttl_minutes": 10},
        provider=provider,
    )
    db.commit()

    assert notification.status == NotificationStatus.SENT.value
    assert notification.channel == NotificationChannel.SMS.value
    assert notification.provider == provider.kind.value
    assert notification.provider_message_id
    assert notification.attempts == 1
    assert notification.next_attempt_at is None
    assert len(provider.sent) == 1
    assert "123456" in provider.sent[0].text


def test_send_sms_transient_failure_schedules_backoff(db: Session) -> None:
    """A transient provider failure leaves the row ``failed`` with a future retry time."""
    provider = FakeSmsProvider(fail_times=1)
    now = datetime.now(APP_TIMEZONE)
    notification = service.send_sms(
        db,
        to="+27831112222",
        template=NotificationTemplate.GENERIC,
        context={"text": "urgent"},
        provider=provider,
        now=now,
    )
    db.commit()

    assert notification.status == NotificationStatus.FAILED.value
    assert notification.attempts == 1
    assert notification.last_error
    assert notification.next_attempt_at is not None
    # SQLite reads timestamps back naive; compare on wall-clock terms only.
    assert notification.next_attempt_at.replace(tzinfo=None) > now.replace(tzinfo=None)
    assert provider.sent == []


# --- Retry sweep & dead-lettering --------------------------------------------


def test_retry_sweep_delivers_due_failure(db: Session) -> None:
    """The sweep re-attempts a due failed row and marks it sent on success."""
    provider = FakeSmsProvider(fail_times=1)
    past = datetime.now(APP_TIMEZONE) - timedelta(hours=1)
    notification = service.send_sms(
        db,
        to="+27831112222",
        template=NotificationTemplate.GENERIC,
        context={"text": "urgent"},
        provider=provider,
        now=past,
    )
    db.commit()
    assert notification.status == NotificationStatus.FAILED.value

    # The same provider has spent its forced failure, so the retry now succeeds.
    delivered = service.run_retry_sweep(db, provider=provider)
    db.commit()

    assert delivered == 1
    db.refresh(notification)
    assert notification.status == NotificationStatus.SENT.value
    assert notification.attempts == 2


def test_retry_sweep_skips_rows_not_yet_due(db: Session) -> None:
    """A failed row whose backoff has not elapsed is left untouched by the sweep."""
    provider = FakeSmsProvider(fail_times=1)
    now = datetime.now(APP_TIMEZONE)
    notification = service.send_sms(
        db,
        to="+27831112222",
        template=NotificationTemplate.GENERIC,
        context={"text": "later"},
        provider=provider,
        now=now,
    )
    db.commit()

    # Sweep at the same instant: next_attempt_at is in the future, so nothing is due.
    delivered = service.run_retry_sweep(db, provider=provider, now=now)
    db.commit()

    assert delivered == 0
    db.refresh(notification)
    assert notification.status == NotificationStatus.FAILED.value
    assert notification.attempts == 1


def test_failure_dead_letters_after_max_attempts(db: Session) -> None:
    """Once ``attempts`` reaches ``max_attempts`` the row is dead-lettered, not retried forever."""
    provider = FakeSmsProvider(fail_times=10)
    now = datetime.now(APP_TIMEZONE)
    notification = service.enqueue(
        db,
        channel=NotificationChannel.SMS,
        template=NotificationTemplate.GENERIC,
        recipient="+27831112222",
        subject=None,
        payload={"text": "will fail"},
        max_attempts=2,
    )
    db.flush()

    assert service.attempt(db, notification, provider=provider, now=now) is False
    assert notification.status == NotificationStatus.FAILED.value
    assert notification.attempts == 1

    assert service.attempt(db, notification, provider=provider, now=now) is False
    assert notification.status == NotificationStatus.DEAD.value
    assert notification.attempts == 2
    assert notification.next_attempt_at is None

    # A dead row is terminal: the sweep never picks it up again.
    assert service.run_retry_sweep(db, provider=provider, now=now) == 0


# --- Delivery-status webhook --------------------------------------------------


def test_record_delivery_status_marks_delivered(db: Session) -> None:
    """A confirmed delivery advances a sent row to ``delivered``."""
    provider = FakeSmsProvider()
    notification = service.send_sms(
        db,
        to="+27831112222",
        template=NotificationTemplate.GENERIC,
        context={"text": "hi"},
        provider=provider,
    )
    db.commit()

    matched = service.record_delivery_status(
        db,
        DeliveryReceipt(
            provider_message_id=notification.provider_message_id or "",
            delivered=True,
        ),
    )
    db.commit()

    assert matched is True
    db.refresh(notification)
    assert notification.status == NotificationStatus.DELIVERED.value
    assert notification.delivered_at is not None


def test_record_delivery_status_unknown_id_returns_false(db: Session) -> None:
    """An unknown provider message id is reported unmatched (the endpoint answers 404)."""
    assert (
        service.record_delivery_status(
            db, DeliveryReceipt(provider_message_id="does-not-exist", delivered=True)
        )
        is False
    )


# --- Template rendering -------------------------------------------------------


def test_otp_sms_template_renders_code_without_subject() -> None:
    """The OTP SMS renders the code inline and carries no subject/HTML."""
    message = templates.render(
        NotificationChannel.SMS,
        NotificationTemplate.OTP_SIGN_IN,
        {"code": "990011", "ttl_minutes": 5},
    )
    assert "990011" in message.text
    assert message.subject is None
    assert message.html is None


# --- Migrated email path ------------------------------------------------------


@contextmanager
def _fake_db_context(session: Session) -> Generator[Session]:
    """A ``get_db_context`` stand-in yielding the test session and committing on exit."""
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise


def test_deliver_email_records_and_marks_sent(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The email path records a ledger row and marks it ``sent`` with the SMTP Message-ID."""
    monkeypatch.setattr(service, "get_db_context", lambda: _fake_db_context(db))
    from src.core import email_send

    monkeypatch.setattr(
        email_send, "deliver_smtp", lambda **_kwargs: "<msg-1@bk.local>"
    )

    service.deliver_email(
        to="tenant@example.com",
        subject="Activate your account",
        text="link",
        html="<p>link</p>",
        template=NotificationTemplate.ACCOUNT_ACTIVATION,
    )

    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row.channel == NotificationChannel.EMAIL.value
    assert row.template_key == NotificationTemplate.ACCOUNT_ACTIVATION.value
    assert row.status == NotificationStatus.SENT.value
    assert row.provider == "smtp"
    assert row.provider_message_id == "<msg-1@bk.local>"
    assert row.subject == "Activate your account"


def test_deliver_email_failure_records_and_reraises(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SMTP failure marks the row ``failed`` and re-raises, preserving the caller contract."""
    monkeypatch.setattr(service, "get_db_context", lambda: _fake_db_context(db))
    from src.core import email_send

    def _boom(**_kwargs: object) -> str:
        raise email_send.EmailDeliveryError("smtp down")

    monkeypatch.setattr(email_send, "deliver_smtp", _boom)

    with pytest.raises(email_send.EmailDeliveryError):
        service.deliver_email(
            to="tenant@example.com",
            subject="Reset your password",
            text="link",
            template=NotificationTemplate.PASSWORD_RESET,
        )

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0].status == NotificationStatus.FAILED.value
    assert rows[0].attempts == 1
    assert rows[0].last_error
