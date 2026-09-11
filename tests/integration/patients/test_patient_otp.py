"""A patient is a phone number and a 6-digit code (Issue 17), over real HTTP.

Every acceptance criterion, each through the API against an in-memory database:

* a number written three ways is one patient, signed in with a code and no password (no column
  for one exists, and the sign-in refuses a ``password`` field);
* codes expire, are single-use, and lock after ``OTP_MAX_VERIFY_ATTEMPTS`` wrong guesses until a
  new one is requested; a new one cannot be requested inside the cooldown;
* **a code never reaches a log record**, captured unredacted at DEBUG from every logger, and never
  a database row either (the notification ledger withholds it);
* USSD and WhatsApp resolve the patient from the gateway's number with no code, and never with a
  web session;
* a patient session opens patient routes only, and a staff token opens none of them.

Settings are built with ``_env_file=None`` so a local ``.env`` cannot change the outcome.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Generator, Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment, PatientChannel
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
from tests.factories import FACTORY_STAFF_PASSWORD, StaffFactory

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


def _code(ctx: SimpleNamespace) -> str:
    """The code in the latest SMS the fake provider accepted."""
    match = re.search(r"\b(\d{6})\b", ctx.sms.sent[-1].text)
    assert match, "no code in the last SMS"
    return match.group(1)


def _request(ctx: SimpleNamespace, phone: str = _NUMBER, client=None):  # type: ignore[no-untyped-def]
    """Ask for a code for ``phone`` (on ``client``, the context's by default)."""
    return (client or ctx.client).post(f"{_OTP}/request", json={"phone": phone})


def _verify(ctx: SimpleNamespace, code: str, phone: str = _NUMBER, client=None):  # type: ignore[no-untyped-def]
    """Verify ``code`` for ``phone`` (on ``client``, the context's by default)."""
    return (client or ctx.client).post(
        f"{_OTP}/verify", json={"phone": phone, "code": code}
    )


def _patients(ctx: SimpleNamespace) -> int:
    """How many patient rows exist."""
    with ctx.session() as db:
        return int(db.scalar(select(func.count(Patient.id))) or 0)


# --- one number, one patient, no password ---------------------------------------------------


def test_one_number_written_three_ways_is_one_patient_signed_in_with_a_code(
    make_ctx,  # type: ignore[no-untyped-def]
) -> None:
    """Request as ``0821234567``, verify as ``+27821234567``; then again as ``27821234567``."""
    ctx = make_ctx(otp_resend_cooldown_seconds=0)
    assert _request(ctx, "0821234567").status_code == status.HTTP_202_ACCEPTED
    assert ctx.sms.sent[-1].to == _E164
    first = _verify(ctx, _code(ctx), phone="+27821234567")
    assert first.status_code == status.HTTP_200_OK, first.text
    assert first.json()["phone"] == "+27 ** *** 4567"
    assert ctx.client.cookies.get(ctx.settings.patient_session_cookie_name)
    me = ctx.client.get("/api/v1/patients/me")
    assert me.status_code == status.HTTP_200_OK, me.text
    assert me.json()["id"] == first.json()["id"]

    returning = TestClient(ctx.client.app)
    assert (
        _request(ctx, "27821234567", client=returning).status_code
        == status.HTTP_202_ACCEPTED
    )
    again = _verify(ctx, _code(ctx), phone="27821234567", client=returning)
    assert again.json()["id"] == first.json()["id"]
    assert _patients(ctx) == 1
    with ctx.session() as db:
        assert db.scalar(select(Patient.phone_e164)) == _E164


def test_no_patient_exists_until_the_code_is_verified(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """Asking for a code creates nothing: an unverified number is not somebody's record."""
    ctx = make_ctx()
    assert _request(ctx).status_code == status.HTTP_202_ACCEPTED
    assert _patients(ctx) == 0


def test_a_patient_has_no_password_on_any_channel(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """No column could hold one, and the sign-in refuses a password field outright."""
    ctx = make_ctx()
    columns = {c.name for c in inspect(Patient).columns}
    assert not {c for c in columns if re.search("password|secret|pin|hash", c)}
    _request(ctx)
    refused = ctx.client.post(
        f"{_OTP}/verify",
        json={"phone": _NUMBER, "code": _code(ctx), "password": "hunter22"},
    )
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_a_number_that_cannot_be_read_is_a_422_that_does_not_repeat_it(
    make_ctx,
) -> None:  # type: ignore[no-untyped-def]
    """The error envelope is logged, so it never quotes the number back."""
    ctx = make_ctx()
    response = _request(ctx, "0921234567")
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["code"] == "patients.phone.invalid"
    assert "0921234567" not in response.text
    assert ctx.sms.sent == []


# --- expiry, single use, attempts, cooldown -------------------------------------------------


class _Clock:
    """A settable stand-in for the ``time`` module the OTP store reads."""

    def __init__(self, now: float) -> None:
        """Start at ``now``."""
        self.now = now

    def time(self) -> float:
        """The current (fake) time."""
        return self.now


def test_a_code_expires_within_the_configured_window(
    make_ctx,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    """One second past ``OTP_TTL_MINUTES`` the right code is refused as expired."""
    ctx = make_ctx(otp_ttl_minutes=5)
    clock = _Clock(1_800_000_000.0)
    monkeypatch.setattr(otp_store, "time", clock)
    _request(ctx)
    code = _code(ctx)
    clock.now += 5 * 60 + 1
    late = _verify(ctx, code)
    assert late.status_code == status.HTTP_400_BAD_REQUEST
    assert late.json()["code"] == "patients.otp.expired"
    assert _patients(ctx) == 0


def test_a_code_works_once(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """The second use of a good code is refused, from any client."""
    ctx = make_ctx()
    _request(ctx)
    code = _code(ctx)
    assert _verify(ctx, code).status_code == status.HTTP_200_OK
    replay = _verify(ctx, code, client=TestClient(ctx.client.app))
    assert replay.status_code == status.HTTP_400_BAD_REQUEST
    assert replay.json()["code"] == "patients.otp.expired"


def test_too_many_wrong_codes_lock_it_until_a_new_one_is_requested(
    make_ctx,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    """Five wrong guesses lock the code: the right one is then refused; a new code works."""
    ctx = make_ctx()
    clock = _Clock(1_800_000_000.0)
    monkeypatch.setattr(otp_store, "time", clock)
    _request(ctx)
    code = _code(ctx)
    wrong = "000000" if code != "000000" else "111111"
    answers = [_verify(ctx, wrong).json() for _ in range(5)]
    assert [a["attempts_left"] for a in answers] == [4, 3, 2, 1, 0]
    assert answers[-1]["code"] == "patients.otp.locked"

    locked = _verify(ctx, code)
    assert locked.status_code == status.HTTP_400_BAD_REQUEST
    assert locked.json()["code"] == "patients.otp.locked"

    clock.now += ctx.settings.otp_resend_cooldown_seconds
    assert _request(ctx).status_code == status.HTTP_202_ACCEPTED
    assert _verify(ctx, _code(ctx)).status_code == status.HTTP_200_OK


def test_a_new_code_cannot_be_requested_inside_the_cooldown(
    make_ctx,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    """429 with ``Retry-After``, and no second SMS; after the cooldown it goes through."""
    ctx = make_ctx()
    clock = _Clock(1_800_000_000.0)
    monkeypatch.setattr(otp_store, "time", clock)
    _request(ctx)
    clock.now += 20
    too_soon = _request(ctx)
    assert too_soon.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert too_soon.headers["Retry-After"] == "40"
    assert len(ctx.sms.sent) == 1
    clock.now += 40
    assert _request(ctx).status_code == status.HTTP_202_ACCEPTED
    assert len(ctx.sms.sent) == 2


def test_one_number_can_only_be_sent_so_many_codes(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """``OTP_RATE_LIMIT_PER_PHONE`` codes per window, however the number is written."""
    ctx = make_ctx(otp_resend_cooldown_seconds=0, otp_rate_limit_per_phone=3)
    spellings = ["0821234567", "+27821234567", "27821234567", "082 123 4567"]
    answers = [_request(ctx, spelling).status_code for spelling in spellings]
    assert answers == [202, 202, 202, 429]
    assert len(ctx.sms.sent) == 3


def test_the_staff_email_code_locks_too_because_the_store_is_shared(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """One OTP implementation: the email sign-in gains the lock (on ``main`` it had none)."""
    ctx = make_ctx()
    with ctx.session() as db:
        StaffFactory.create(db, email="nurse@clinicq.example")
        db.commit()
    assert (
        ctx.client.post(
            "/api/v1/auth/otp/request", json={"email": "nurse@clinicq.example"}
        ).status_code
        == 200
    )
    code = re.search(r"\b(\d{6})\b", dev_outbox.messages()[0]["text"]).group(1)  # type: ignore[union-attr]
    for _ in range(5):
        ctx.client.post(
            "/api/v1/auth/otp/verify",
            json={
                "email": "nurse@clinicq.example",
                "code": "000000" if code != "000000" else "111111",
            },
        )
    right = ctx.client.post(
        "/api/v1/auth/otp/verify", json={"email": "nurse@clinicq.example", "code": code}
    )
    assert right.status_code == status.HTTP_400_BAD_REQUEST


# --- the code never reaches a log or a row --------------------------------------------------


class _Everything(logging.Handler):
    """Keep every record from every logger as it was emitted: before any filter could redact it.

    Installed **first** on the root logger (see :func:`_capture_first`). The app's handlers carry
    the ``RedactionFilter``, which rewrites a record in place; a capture that ran after them would
    see the redacted text and could not tell a leak from its mask. So each record's message and
    extras are copied the moment it arrives.
    """

    def __init__(self) -> None:
        """Start empty."""
        super().__init__(level=logging.DEBUG)
        self.snapshots: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Copy the record's rendered message and every attribute, now, before it moves on."""
        self.snapshots.append(f"{record.name}: {record.getMessage()} {vars(record)!r}")

    def text(self) -> str:
        """Every captured record, as one searchable string."""
        return "\n".join(self.snapshots)


def _capture_first(handler: logging.Handler) -> None:
    """Put ``handler`` ahead of the root logger's other handlers, so it sees records unfiltered."""
    root = logging.getLogger()
    root.handlers.insert(0, handler)


def test_an_otp_never_reaches_a_log_record_or_a_database_row(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """With the default logging SMS provider, the full flow, every logger at DEBUG, no redaction.

    The redaction filter on the app's handlers would mask a code that slipped into a message; this
    proves none is ever handed to the logging system in the first place, and that the notification
    ledger stores a placeholder rather than the code.
    """
    ctx = make_ctx()
    # The real default provider this time, not the fake: it is the one that used to log the text.
    from src.modules.notifications.sms import LoggingSmsProvider

    notification_service.build_sms_provider = lambda: LoggingSmsProvider()  # type: ignore[assignment]
    handler = _Everything()
    root = logging.getLogger()
    _capture_first(handler)
    previous = root.level
    root.setLevel(logging.DEBUG)
    try:
        assert _request(ctx).status_code == status.HTTP_202_ACCEPTED
        code = re.search(r"\b(\d{6})\b", dev_outbox.messages()[0]["text"]).group(1)  # type: ignore[union-attr]
        _verify(ctx, "000000" if code != "000000" else "111111")
        assert _verify(ctx, code).status_code == status.HTTP_200_OK
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)

    assert handler.snapshots, "nothing was logged: the capture is not attached"
    for snapshot in handler.snapshots:
        assert code not in snapshot, f"the code reached a log record: {snapshot[:200]}"
        assert _E164 not in snapshot, (
            f"the number reached a log record: {snapshot[:200]}"
        )
    with ctx.session() as db:
        for table in Base.metadata.sorted_tables:
            for row in db.execute(text(f'SELECT * FROM "{table.name}"')):
                assert code not in repr(tuple(row)), (
                    f"the code is stored in {table.name}"
                )


def test_the_dev_outbox_is_where_a_developer_reads_a_code(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """``GET /dev/outbox`` in development; outside it nothing is kept and the route does not exist."""
    ctx = make_ctx()
    from src.modules.notifications.sms import LoggingSmsProvider

    notification_service.build_sms_provider = lambda: LoggingSmsProvider()  # type: ignore[assignment]
    _request(ctx)
    messages = ctx.client.get("/dev/outbox").json()["messages"]
    assert messages[0]["to"] == _E164 and re.search(r"\b\d{6}\b", messages[0]["text"])

    staging = make_ctx(
        environment=AppEnvironment.STAGING, cors_origins="https://clinicq.example"
    )
    assert dev_outbox.record(dev_outbox.NotificationChannel.SMS, _E164, "x") is False
    assert staging.client.get("/dev/outbox").status_code == status.HTTP_404_NOT_FOUND


# --- sessions: patient routes only ---------------------------------------------------------


def test_a_patient_session_opens_patient_routes_and_no_staff_route(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """A patient token is refused by staff routes, and a staff token by patient routes."""
    ctx = make_ctx()
    _request(ctx)
    _verify(ctx, _code(ctx))
    token = ctx.client.cookies.get(ctx.settings.patient_session_cookie_name)
    bearer = {"Authorization": f"Bearer {token}"}
    assert ctx.client.get("/api/v1/patients/me").status_code == 200
    assert (
        TestClient(ctx.client.app).get("/api/v1/auth/me", headers=bearer).status_code
        == 401
    )

    with ctx.session() as db:
        StaffFactory.create(db, email="reception@clinicq.example")
        db.commit()
    staff = TestClient(ctx.client.app)
    staff.post(
        "/api/v1/auth/password/login",
        json={"email": "reception@clinicq.example", "password": FACTORY_STAFF_PASSWORD},
    )
    staff_bearer = {
        "Authorization": f"Bearer {staff.cookies.get(ctx.settings.access_token_cookie_name)}"
    }
    assert staff.get("/api/v1/auth/me").status_code == 200
    assert (
        TestClient(ctx.client.app)
        .get("/api/v1/patients/me", headers=staff_bearer)
        .status_code
        == 401
    )


def test_signing_out_is_enforced_on_the_server(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """A copy of the session cookie kept from before the sign-out is refused afterwards."""
    ctx = make_ctx()
    _request(ctx)
    _verify(ctx, _code(ctx))
    kept = ctx.client.cookies.get(ctx.settings.patient_session_cookie_name)
    csrf = ctx.client.cookies.get(ctx.settings.csrf_cookie_name)
    assert (
        ctx.client.post(
            "/api/v1/patients/logout", headers={"X-CSRF-Token": csrf}
        ).status_code
        == 204
    )
    replay = TestClient(ctx.client.app)
    replay.cookies.set(ctx.settings.patient_session_cookie_name, kept)
    assert replay.get("/api/v1/patients/me").status_code == 401


def test_a_patient_write_needs_its_csrf_token_and_changes_only_the_name(
    make_ctx,
) -> None:  # type: ignore[no-untyped-def]
    """The patient cookie is a session cookie like the staff one: no token, 403; other fields, 422."""
    ctx = make_ctx()
    _request(ctx)
    _verify(ctx, _code(ctx))
    csrf = {"X-CSRF-Token": ctx.client.cookies.get(ctx.settings.csrf_cookie_name)}
    assert (
        ctx.client.patch(
            "/api/v1/patients/me", json={"display_name": "Thandi"}
        ).status_code
        == 403
    )
    renamed = ctx.client.patch(
        "/api/v1/patients/me", json={"display_name": "Thandi"}, headers=csrf
    )
    assert renamed.status_code == 200 and renamed.json()["display_name"] == "Thandi"
    sneaky = ctx.client.patch(
        "/api/v1/patients/me", json={"phone_e164": "+27830000000"}, headers=csrf
    )
    assert sneaky.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# --- USSD and WhatsApp: the gateway vouches ------------------------------------------------


def test_ussd_and_whatsapp_resolve_the_patient_from_the_gateway_number_without_a_code(
    make_ctx,  # type: ignore[no-untyped-def]
) -> None:
    """The MSISDN form and the web form of one number are one patient; no SMS, no web session."""
    ctx = make_ctx()
    _request(ctx)
    web = _verify(ctx, _code(ctx)).json()
    sent_before = len(ctx.sms.sent)

    with ctx.session() as db:
        by_ussd = patient_service.patient_for_gateway(
            db, msisdn="27821234567", channel=PatientChannel.USSD
        )
        by_whatsapp = patient_service.patient_for_gateway(
            db,
            msisdn="+27 82 123 4567",
            channel=PatientChannel.WHATSAPP,
            whatsapp_id="27821234567",
        )
        new_by_ussd = patient_service.patient_for_gateway(
            db, msisdn="27831112222", channel=PatientChannel.USSD
        )
        db.commit()
        assert by_ussd.id == by_whatsapp.id == web["id"]
        assert by_whatsapp.whatsapp_id == "27821234567"
        assert new_by_ussd.phone_e164 == "+27831112222"
        assert (
            new_by_ussd.phone_verified_at is None
        )  # vouched for, not proved by a code
        with pytest.raises(ValueError, match="does not vouch"):
            patient_service.patient_for_gateway(
                db, msisdn="27821234567", channel=PatientChannel.WEB
            )
    assert len(ctx.sms.sent) == sent_before
    assert _patients(ctx) == 2


def test_two_sign_ins_racing_for_a_new_number_make_one_patient(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """The unique constraint picks the winner; the loser reads it back inside its savepoint."""
    ctx = make_ctx()
    with ctx.session() as db, ctx.session() as racer:
        racer.add(Patient(phone_e164=_E164))
        racer.commit()

        class _MissFirstLookup:
            """The session, except that the first lookup misses (the other insert lands after)."""

            def __init__(self, inner: Session) -> None:
                self._inner, self._missed = inner, False

            def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                result = self._inner.execute(*args, **kwargs)
                if not self._missed:
                    self._missed = True
                    return SimpleNamespace(scalar_one_or_none=lambda: None)
                return result

            def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
                return getattr(self._inner, name)

        patient, created = patient_service.get_or_create_patient(
            _MissFirstLookup(db),  # type: ignore[arg-type]
            _E164,
        )
        assert created is False and patient.phone_e164 == _E164
        db.commit()
    assert _patients(ctx) == 1
