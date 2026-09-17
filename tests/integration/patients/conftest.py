"""The fixture every patient-identity test builds on (Issues 17, 219).

``make_ctx(**settings)`` gives an app on a fresh in-memory database with the RBAC catalog a
deployment has, SMS going to a :class:`~src.modules.notifications.sms.FakeSmsProvider` the test can
read, and **every module that reads settings directly patched to the test's own** — email included,
because a developer's ``.env`` may name a real SMTP server and no test may send mail through it.
With ``smtp_host=""`` an emailed code goes to the development outbox, which is where
``test_patient_email_sign_in.py`` reads it from.

Settings are built with ``_env_file=None`` so a local ``.env`` cannot change any outcome.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.commons.enums import AppEnvironment
from src.core import email_send, otp_store, refresh_token_policy, security
from src.core.config import Settings, get_settings
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.database.models import Base, Patient
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app
from src.modules.notifications import dev_outbox
from src.modules.notifications import service as notification_service
from src.modules.notifications.sms import FakeSmsProvider
from src.modules.patients import service as patient_service

_SECRET = "patient-otp-test-secret-min-32-characters!"
_OTP = "/api/v1/patients/otp"
_NUMBER = "0821234567"
_E164 = "+27821234567"


def _settings(**overrides: object) -> Settings:
    """Isolated settings: development, a known secret, the shipped OTP defaults."""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "jwt_secret": _SECRET,
        "smtp_host": "",
        "auth_password_login_enabled": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def make_ctx(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Factory: an app on a fresh SQLite database; SMS goes to a fake the test can read."""
    engines = []

    def _make(**overrides: object) -> SimpleNamespace:
        settings = _settings(**overrides)
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        ).execution_options(schema_translate_map=sqlite_schema_translate_map())
        Base.metadata.create_all(engine)
        engines.append(engine)
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        with (
            factory() as db
        ):  # the catalog and grants a deployment has (make seed-rbac)
            sync_rbac_catalog(db)
            db.commit()

        def _db() -> Generator[Session]:
            with factory() as db:
                yield db

        sms = FakeSmsProvider()
        # Every module that reads settings directly, email included: a developer's .env may name
        # a real SMTP server, and no test may send mail through it.
        for module in (
            security,
            otp_store,
            refresh_token_policy,
            dev_outbox,
            email_send,
            # The service reads the flags itself — SMS_ENABLED and, from Issue 219,
            # PATIENT_EMAIL_SIGN_IN_ENABLED — so a test that switches one on must be the thing it
            # reads, not a developer's .env.
            patient_service,
        ):
            monkeypatch.setattr(module, "get_settings", lambda: settings)
        monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
        monkeypatch.setattr(notification_service, "build_sms_provider", lambda: sms)
        otp_store.reset_otp_state()
        dev_outbox.clear()
        app = create_app(settings)
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_settings] = lambda: settings
        return SimpleNamespace(
            client=TestClient(app), settings=settings, session=factory, sms=sms
        )

    yield _make
    for engine in engines:
        Base.metadata.drop_all(engine)
        engine.dispose()


def patient_count(ctx: SimpleNamespace) -> int:
    """How many patient rows exist."""
    with ctx.session() as db:
        return int(db.scalar(select(func.count(Patient.id))) or 0)
