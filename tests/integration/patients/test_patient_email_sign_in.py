"""A patient is an email address and a 6-digit code too, when the flag is on (Issue 219).

The sibling of ``test_patient_otp.py``, and every acceptance criterion the issue asks for, over
real HTTP against an in-memory database:

* **with the flag off, nothing changed**: the request is refused ``503
  patients.otp.email_disabled``, and the page renders exactly what it rendered before this issue;
* **with it on, an address is an identity**: a code, a session, a patient row with an address and
  **no number**, who can then do what any patient does;
* the two contacts are **peers, not alternatives to one record**: one address is one patient
  however it is capitalised, and two first sign-ins at once make one row, not two;
* the budgets, the cooldown and the wrong/expired/locked outcomes behave for an address exactly as
  they do for a number, because they are the same code;
* a code issued for an address **cannot be spent on a number**, and the reverse;
* naming both contacts or neither is a 422 before anything is issued;
* the address never reaches a log record or the audit trail.

``make_ctx`` is this package's fixture (``conftest.py``). With ``smtp_host=""`` the emailed code
goes to the development outbox, which is where these tests read it.
"""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette import status

from src.core import otp_store
from src.database.models import AuditEvent, Patient
from src.modules.notifications import dev_outbox
from tests.integration.patients.conftest import _NUMBER, _OTP, patient_count

_ADDRESS = "nomsa@example.com"
_SHOUTED = "Nomsa@Example.COM"


def _on(make_ctx, **overrides: object) -> SimpleNamespace:  # type: ignore[no-untyped-def]
    """A context with email sign-in switched on."""
    return make_ctx(patient_email_sign_in_enabled=True, **overrides)


def _code(_ctx: SimpleNamespace) -> str:
    """The code in the latest email the outbox holds. ``messages()`` is newest first."""
    emails = [m for m in dev_outbox.messages() if m["channel"] == "email"]
    assert emails, "no email in the development outbox"
    match = re.search(r"\b(\d{6})\b", emails[0]["text"])
    assert match, "no code in the last email"
    return match.group(1)


def _request(ctx: SimpleNamespace, email: str = _ADDRESS, client=None):  # type: ignore[no-untyped-def]
    """Ask for a code for ``email``."""
    return (client or ctx.client).post(f"{_OTP}/request", json={"email": email})


def _verify(ctx: SimpleNamespace, code: str, email: str = _ADDRESS, client=None):  # type: ignore[no-untyped-def]
    """Verify ``code`` for ``email``."""
    return (client or ctx.client).post(
        f"{_OTP}/verify", json={"email": email, "code": code}
    )


# --- the flag is the whole switch ------------------------------------------------------------


def test_with_the_flag_off_an_address_is_refused_and_nothing_is_issued(
    make_ctx,
) -> None:  # type: ignore[no-untyped-def]
    """Off by default: the way in does not exist, and asking leaves no trace that it might."""
    ctx = make_ctx()
    assert ctx.settings.patient_email_sign_in_enabled is False

    refused = _request(ctx)

    assert refused.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert refused.json()["code"] == "patients.otp.email_disabled"
    assert not [m for m in dev_outbox.messages() if m["channel"] == "email"]
    assert patient_count(ctx) == 0


def test_the_flag_going_off_mid_sign_in_stops_a_code_already_issued(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """Verify checks the flag too, so switching it off ends sign-ins rather than only new ones."""
    ctx = _on(make_ctx)
    assert _request(ctx).status_code == status.HTTP_202_ACCEPTED
    code = _code(ctx)
    ctx.settings.patient_email_sign_in_enabled = False

    stopped = _verify(ctx, code)

    assert stopped.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert patient_count(ctx) == 0


def test_the_phone_sign_in_is_untouched_by_the_flag(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """The number is the identity it always was, flag or no flag."""
    for ctx in (make_ctx(), _on(make_ctx)):
        # The store is process-wide, so the second context starts where the first left off; a
        # cooldown on one number is not what this test is about.
        otp_store.reset_otp_state()
        assert (
            ctx.client.post(f"{_OTP}/request", json={"phone": _NUMBER}).status_code
            == status.HTTP_202_ACCEPTED
        )


# --- an address is an identity ---------------------------------------------------------------


def test_an_address_signs_a_patient_in_and_the_record_has_no_number(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """The whole point: somebody with no mobile number is a patient here, like any other."""
    ctx = _on(make_ctx)
    assert _request(ctx).status_code == status.HTTP_202_ACCEPTED
    assert patient_count(ctx) == 0, "no patient until the code is verified"

    signed_in = _verify(ctx, _code(ctx))

    assert signed_in.status_code == status.HTTP_200_OK, signed_in.text
    assert signed_in.json()["email"] == "n•••a@example.com"
    assert signed_in.json()["phone"] == ""
    assert ctx.client.cookies.get(ctx.settings.patient_session_cookie_name)
    me = ctx.client.get("/api/v1/patients/me")
    assert me.status_code == status.HTTP_200_OK, me.text
    assert me.json()["id"] == signed_in.json()["id"]
    with ctx.session() as db:
        patient = db.execute(select(Patient)).scalar_one()
        assert patient.email == _ADDRESS
        assert patient.phone_e164 is None
        assert patient.email_verified_at is not None


def test_one_address_written_two_ways_is_one_patient(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """``Nomsa@Example.COM`` and ``nomsa@example.com`` are one person, as two spellings of a number are."""
    ctx = _on(make_ctx, otp_resend_cooldown_seconds=0)
    _request(ctx, _ADDRESS)
    first = _verify(ctx, _code(ctx), email=_ADDRESS)
    assert first.status_code == status.HTTP_200_OK, first.text

    returning = TestClient(ctx.client.app)
    assert (
        _request(ctx, _SHOUTED, client=returning).status_code
        == status.HTTP_202_ACCEPTED
    )
    again = _verify(ctx, _code(ctx), email=_SHOUTED, client=returning)

    assert again.status_code == status.HTTP_200_OK, again.text
    assert again.json()["id"] == first.json()["id"]
    assert patient_count(ctx) == 1
    with ctx.session() as db:
        assert db.scalar(select(Patient.email)) == _ADDRESS


def test_two_first_sign_ins_at_one_address_make_one_patient(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """The unique constraint's race, from the service, exactly as the number's is tested."""
    from src.modules.patients.service import get_or_create_patient_by_email

    ctx = _on(make_ctx)
    with ctx.session() as first, ctx.session() as second:
        one, created_one = get_or_create_patient_by_email(first, _ADDRESS)
        first.commit()
        one_id = one.id
        two, created_two = get_or_create_patient_by_email(second, _ADDRESS)
        second.commit()
        two_id = two.id

    assert created_one is True
    assert created_two is False
    assert one_id == two_id
    assert patient_count(ctx) == 1


def test_a_patient_who_signed_in_by_address_can_do_what_a_patient_does(
    make_ctx,
) -> None:  # type: ignore[no-untyped-def]
    """Parity, asserted rather than assumed: the session opens the patient's own routes."""
    ctx = _on(make_ctx)
    _request(ctx)
    assert _verify(ctx, _code(ctx)).status_code == status.HTTP_200_OK
    # Unsafe requests echo the readable CSRF cookie the sign-in set, as a browser's fetch does.
    ctx.client.headers["X-CSRF-Token"] = ctx.client.cookies.get(
        ctx.settings.csrf_cookie_name
    )

    consents = ctx.client.get("/api/v1/patients/me/consents")
    named = ctx.client.patch("/api/v1/patients/me", json={"display_name": "Nomsa"})
    out = ctx.client.post("/api/v1/patients/logout")

    assert consents.status_code == status.HTTP_200_OK, consents.text
    assert named.status_code == status.HTTP_200_OK, named.text
    assert named.json()["display_name"] == "Nomsa"
    assert out.status_code == status.HTTP_204_NO_CONTENT
    assert ctx.client.get("/api/v1/patients/me").status_code == (
        status.HTTP_401_UNAUTHORIZED
    )


# --- the two contacts are peers, and never each other ------------------------------------------


def test_a_code_issued_for_an_address_cannot_be_spent_on_a_number(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """The store is keyed by (kind, identifier), and this is what that buys."""
    ctx = _on(make_ctx)
    _request(ctx)
    emailed = _code(ctx)

    misused = ctx.client.post(
        f"{_OTP}/verify", json={"phone": _NUMBER, "code": emailed}
    )

    assert misused.status_code == status.HTTP_400_BAD_REQUEST
    assert patient_count(ctx) == 0


def test_a_request_naming_both_contacts_or_neither_is_refused(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """A 422 from the schema, before anything is issued, counted or looked up."""
    ctx = _on(make_ctx)

    for body in (
        {"phone": _NUMBER, "email": _ADDRESS},
        {},
    ):
        answer = ctx.client.post(f"{_OTP}/request", json=body)
        assert answer.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, body

    assert not dev_outbox.messages()
    assert not ctx.sms.sent


def test_an_address_that_cannot_be_read_is_refused_with_a_sentence(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """422 with the patient's own sentence, and it never repeats what they typed."""
    ctx = _on(make_ctx)

    answer = _request(ctx, "nomsa-at-example")

    assert answer.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = answer.text
    assert "name@example.com" in body
    assert "nomsa-at-example" not in body


# --- the same budgets, cooldown and outcomes ---------------------------------------------------


def test_a_second_code_inside_the_cooldown_waits_exactly_as_a_number_does(
    make_ctx,
) -> None:  # type: ignore[no-untyped-def]
    """One cooldown, one implementation, and the address is subject to it."""
    ctx = _on(make_ctx, otp_resend_cooldown_seconds=60)
    assert _request(ctx).status_code == status.HTTP_202_ACCEPTED

    again = _request(ctx)

    assert again.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert int(again.headers["Retry-After"]) > 0


def test_a_wrong_code_is_refused_and_locks_after_the_configured_attempts(
    make_ctx,
) -> None:  # type: ignore[no-untyped-def]
    """Wrong, then locked — the outcomes the number's sign-in has, with the same sentences."""
    ctx = _on(make_ctx, otp_max_verify_attempts=2)
    _request(ctx)
    right = _code(ctx)
    wrong = "000000" if right != "000000" else "111111"

    first = _verify(ctx, wrong)
    assert first.status_code == status.HTTP_400_BAD_REQUEST
    assert first.json()["code"] == "patients.otp.invalid"

    _verify(ctx, wrong)
    locked = _verify(ctx, right)

    assert locked.status_code == status.HTTP_400_BAD_REQUEST
    assert locked.json()["code"] == "patients.otp.locked"
    assert patient_count(ctx) == 0


def test_a_code_is_single_use(make_ctx) -> None:  # type: ignore[no-untyped-def]
    """Spent on the first verification, refused on the second."""
    ctx = _on(make_ctx)
    _request(ctx)
    code = _code(ctx)
    assert _verify(ctx, code).status_code == status.HTTP_200_OK

    assert _verify(ctx, code, client=TestClient(ctx.client.app)).status_code == (
        status.HTTP_400_BAD_REQUEST
    )
    assert patient_count(ctx) == 1


# --- the address is personal information -------------------------------------------------------


def test_the_code_and_the_address_never_reach_a_log_record(make_ctx, caplog) -> None:  # type: ignore[no-untyped-def]
    """Logs are shipped, kept and read by people who are not the recipient (Issue 6)."""
    ctx = _on(make_ctx)

    class _Capture(logging.Handler):
        def __init__(self) -> None:
            super().__init__(logging.DEBUG)
            self.snapshots: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.snapshots.append(f"{record.getMessage()} {record.__dict__}")

    handler = _Capture()
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        assert _request(ctx).status_code == status.HTTP_202_ACCEPTED
        code = _code(ctx)
        assert _verify(ctx, code).status_code == status.HTTP_200_OK
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)

    assert handler.snapshots, "nothing was logged: the capture is not attached"
    for snapshot in handler.snapshots:
        assert code not in snapshot, f"the code reached a log record: {snapshot[:200]}"
        assert _ADDRESS not in snapshot, (
            f"the address reached a log record: {snapshot[:200]}"
        )


def test_the_audit_row_for_a_new_patient_names_the_channel_and_no_address(
    make_ctx,
) -> None:  # type: ignore[no-untyped-def]
    """The number's rule, applied to the address: the trail says a patient was created, not who."""
    ctx = _on(make_ctx)
    _request(ctx)
    _verify(ctx, _code(ctx))

    with ctx.session() as db:
        rows = list(db.execute(select(AuditEvent)).scalars())

    assert rows, "creating a patient is audited"
    for row in rows:
        assert _ADDRESS not in repr(
            {c.name: getattr(row, c.name) for c in row.__table__.columns}
        )


@pytest.fixture(autouse=True)
def _clean_store() -> None:
    """Each test starts with an empty OTP store and outbox, as the phone suite's fixture does."""
    otp_store.reset_otp_state()
    dev_outbox.clear()
