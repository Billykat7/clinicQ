"""Every error response ``contracts/notifications.yaml`` documents, driven over real HTTP (Issue 71).

``test_openapi_contracts.py`` (Issue 30's harness) checks the notifications contract against the
application's own OpenAPI document: every route documented, every documented route served, every
status FastAPI knows about present. FastAPI knows the success codes and nothing else a handler or a
dependency raises, so the error half of the contract (a ``401`` from the session, a ``403`` from a
grant or the CSRF middleware, a ``404`` from the site guard, a ``409`` while web push is off, a
``503`` while the SMS webhook is off) would be unproved text. This file is the proof, and it is kept
equal to the contract in both directions:

* :data:`CASES` holds one or more concrete requests per documented ``(method, path, error status)``,
  each run against the real application on the queue fixture's clinics, asserting the status and the
  envelope's ``code`` (which the contract must name for that response);
* a documented error status with no case fails, naming it, so a refusal cannot be written down and
  left unproved;
* a case for a status the contract does not document fails, so a refusal the application gives is
  written down.

No case reaches a provider: the refusals all happen before a message could be sent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from starlette import status

from scripts.generate_vapid_keys import generate as generate_vapid_keys
from src.commons.enums import UserRole
from src.core.config import get_settings
from tests.factories import FACTORY_STAFF_PASSWORD, StaffFactory
from tests.integration.queue.conftest import (  # noqa: F401 (``desk`` registers the fixture)
    SITE_A,
    SITE_B,
    desk,
    queue_settings,
)

_ROOT = Path(__file__).resolve().parents[3]
_CONTRACT = _ROOT / "contracts" / "notifications.yaml"
_METHODS = ("get", "post", "put", "patch", "delete")

_N = "/api/v1/notifications"
_T = f"{_N}/templates"
_SMS_TOKEN = "ct" * 22  # the SMS callback secret, in this test only
_WEBHOOK_SECRET = "contract-test-webhook-secret-32-chars-min"
_RECEIPT = f"/api/v1/webhooks/sms/africastalking/{_SMS_TOKEN}"
_REPLY = f"{_RECEIPT}/inbound"
_FORGED = "/api/v1/webhooks/sms/africastalking/" + "x" * 44
_NO_LINK = "x" * 43
_SUBSCRIPTION = {
    "endpoint": "https://fcm.googleapis.com/fcm/send/contract-test",
    "keys": {"p256dh": "B" + "A" * 86, "auth": "A" * 22},
}


# --------------------------------------------------------------------------------------
# The world a case runs in
# --------------------------------------------------------------------------------------


class World:
    """The queue fixture's two clinics, and the callers a refusal needs."""

    def __init__(self, fixture: SimpleNamespace) -> None:
        self.desk = fixture
        self._operator: TestClient | None = None

    def anonymous(self) -> TestClient:
        """No session at all."""
        return TestClient(self.desk.app)

    def receptionist(self) -> TestClient:
        """``desk.a``: works at clinic A, holds no operator grant and no notification centre."""
        return self.desk.staff("desk.a")  # type: ignore[no-any-return]

    def manager(self) -> TestClient:
        """``manager.a``: manages clinic A only."""
        return self.desk.staff("manager.a")  # type: ignore[no-any-return]

    def operator(self) -> TestClient:
        """A platform administrator: the kernel's ``admin`` role, which holds ``logs`` and the centre."""
        if self._operator is None:
            with self.desk.session() as db:
                StaffFactory.create(
                    db, email="ops@clinicq.example", role=UserRole.ADMIN
                )
                db.commit()
            client = TestClient(self.desk.app)
            signed_in = client.post(
                "/api/v1/auth/password/login",
                json={
                    "email": "ops@clinicq.example",
                    "password": FACTORY_STAFF_PASSWORD,
                },
            )
            assert signed_in.status_code == status.HTTP_200_OK, signed_in.text
            token = client.cookies.get(self.desk.settings.csrf_cookie_name)
            if token:
                client.headers["X-CSRF-Token"] = token
            self._operator = client
        return self._operator

    def staff_without_csrf(self) -> TestClient:
        """A receptionist's browser session that forgets to echo the CSRF token."""
        client = self.receptionist()
        client.headers.pop("X-CSRF-Token", None)
        return client

    def patient(self) -> TestClient:
        """A patient's web session, as a bearer token."""
        client, _ = self.desk.patient()
        return client  # type: ignore[no-any-return]

    def patient_cookie(self) -> TestClient:
        """A patient's browser session cookie, with no CSRF token echoed."""
        client, _ = self.desk.patient()
        token = client.headers.pop("Authorization").removeprefix("Bearer ")
        client.cookies.set(get_settings().patient_session_cookie_name, token)
        return client  # type: ignore[no-any-return]

    def page_token(self) -> str:
        """A patient's ticket in clinic A's Triage, by its page link."""
        joined = self.patient().post(self.desk.join_path(self.desk.triage), json={})
        assert joined.status_code == status.HTTP_201_CREATED, joined.text
        return str(joined.json()["page_url"]).removeprefix("/t/")

    def configure(self, **overrides: object) -> None:
        """Run the application with these settings (the queue fixture's, changed)."""
        settings = queue_settings(**overrides)
        self.desk.app.dependency_overrides[get_settings] = lambda: settings


@pytest.fixture
def world(request: pytest.FixtureRequest) -> World:
    """The queue fixture, wrapped for the cases."""
    return World(request.getfixturevalue("desk"))


def _web_push_on(world: World) -> None:
    keys = generate_vapid_keys("mailto:ops@clinicq.example")
    world.configure(
        web_push_vapid_public_key=keys["WEB_PUSH_VAPID_PUBLIC_KEY"],
        web_push_vapid_private_key=keys["WEB_PUSH_VAPID_PRIVATE_KEY"],
        web_push_vapid_subject=keys["WEB_PUSH_VAPID_SUBJECT"],
    )


def _subscribe_where_no_push_service_is(world: World) -> httpx.Response:
    _web_push_on(world)
    return world.patient().post(
        f"{_N}/web-push/subscriptions",
        json={**_SUBSCRIPTION, "endpoint": "https://push.evil.example/x"},
    )


def _receipt_webhook(world: World, path: str, **settings: object) -> httpx.Response:
    world.configure(**settings)
    return world.anonymous().post(
        path,
        content=b"id=ATXid_1&status=Success",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def _garbage_webhook(world: World, path: str) -> httpx.Response:
    world.configure(sms_webhook_token=_SMS_TOKEN)
    return world.anonymous().post(path, content=b"\xff\xfe")


def _delivery_webhook(
    world: World, secret: str, presented: str | None, body: dict[str, Any]
) -> httpx.Response:
    world.configure(notification_webhook_secret=secret)
    headers = {"X-Webhook-Secret": presented} if presented else {}
    return world.anonymous().post(f"{_N}/webhooks/delivery", json=body, headers=headers)


# --------------------------------------------------------------------------------------
# The cases: one or more per documented error status
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Case:
    """One concrete request that must be refused with ``status``."""

    method: str
    #: The contract's path, relative to its ``/api/v1`` server.
    path: str
    status: int
    #: What makes it refused, for the test id and a failure message.
    why: str
    drive: Callable[[World], httpx.Response]
    #: The envelope's ``code``; ``None`` for a body with no code (the CSRF and SMS webhook refusals).
    code: str | None

    @property
    def id(self) -> str:
        return f"{self.method} {self.path} {self.status} ({self.why})"


_UNAUTH = "http.unauthorized"
_FORBIDDEN = "insufficient_permission"
_NOT_FOUND = "http.not_found"
_INVALID = "request.invalid"
_REFUSED = "http.unprocessable_content"


def _operator_only(method: str, path: str, url: str, **kwargs: Any) -> list[Case]:
    """The 401 and 403 every operator route (``logs`` at ``business`` tier) documents."""
    return [
        Case(
            method,
            path,
            401,
            "no session",
            lambda w: w.anonymous().request(method, url, **kwargs),
            _UNAUTH,
        ),
        Case(
            method,
            path,
            403,
            "a receptionist holds no logs grant",
            lambda w: w.receptionist().request(method, url, **kwargs),
            _FORBIDDEN,
        ),
    ]


def _centre(method: str, path: str, url: str) -> list[Case]:
    """The 401 and 403 every notification centre route documents."""
    return [
        Case(
            method,
            path,
            401,
            "no session",
            lambda w: w.anonymous().request(method, url),
            _UNAUTH,
        ),
        Case(
            method,
            path,
            403,
            "a site-held role holds no centre grant",
            lambda w: w.receptionist().request(method, url),
            _FORBIDDEN,
        ),
    ]


_KEY = "/notifications/templates/{template}/{channel}/{language}"
_PUBLISH = f"{_KEY}/versions"
_BUDGET = "/sites/{site_id}/sms-budget"

CASES: tuple[Case, ...] = (
    # ── The delivery ledger ──────────────────────────────────────────────────────────────
    *_operator_only("GET", "/notifications", _N),
    Case(
        "GET",
        "/notifications",
        422,
        "limit 0",
        lambda w: w.operator().get(_N, params={"limit": 0}),
        _INVALID,
    ),
    *_operator_only("GET", "/notifications/{notification_id}", f"{_N}/nope"),
    Case(
        "GET",
        "/notifications/{notification_id}",
        404,
        "no such message",
        lambda w: w.operator().get(f"{_N}/nope"),
        _NOT_FOUND,
    ),
    *_operator_only("GET", "/notifications/delivery-stats", f"{_N}/delivery-stats"),
    Case(
        "GET",
        "/notifications/delivery-stats",
        422,
        "more than a week",
        lambda w: w.operator().get(f"{_N}/delivery-stats", params={"hours": 169}),
        _INVALID,
    ),
    Case(
        "POST",
        "/notifications/webhooks/delivery",
        401,
        "wrong secret",
        lambda w: _delivery_webhook(
            w, _WEBHOOK_SECRET, "wrong", {"provider_message_id": "m", "delivered": True}
        ),
        _UNAUTH,
    ),
    Case(
        "POST",
        "/notifications/webhooks/delivery",
        404,
        "no secret configured",
        lambda w: _delivery_webhook(
            w, "", _WEBHOOK_SECRET, {"provider_message_id": "m", "delivered": True}
        ),
        _NOT_FOUND,
    ),
    Case(
        "POST",
        "/notifications/webhooks/delivery",
        404,
        "unknown provider message",
        lambda w: _delivery_webhook(
            w,
            _WEBHOOK_SECRET,
            _WEBHOOK_SECRET,
            {"provider_message_id": "m", "delivered": True},
        ),
        _NOT_FOUND,
    ),
    Case(
        "POST",
        "/notifications/webhooks/delivery",
        422,
        "not a receipt",
        lambda w: _delivery_webhook(w, _WEBHOOK_SECRET, _WEBHOOK_SECRET, {}),
        _INVALID,
    ),
    # ── SMS ──────────────────────────────────────────────────────────────────────────────
    *_operator_only("GET", "/notifications/sms/kill-switch", f"{_N}/sms/kill-switch"),
    *_operator_only(
        "PUT",
        "/notifications/sms/kill-switch",
        f"{_N}/sms/kill-switch",
        json={"enabled": True},
    ),
    Case(
        "PUT",
        "/notifications/sms/kill-switch",
        422,
        "no enabled",
        lambda w: w.operator().put(f"{_N}/sms/kill-switch", json={"reason": "why"}),
        _INVALID,
    ),
    Case(
        "GET",
        _BUDGET,
        401,
        "no session",
        lambda w: w.anonymous().get(f"/api/v1/sites/{SITE_A}/sms-budget"),
        _UNAUTH,
    ),
    Case(
        "GET",
        _BUDGET,
        403,
        "a receptionist cannot read settings",
        lambda w: w.receptionist().get(f"/api/v1/sites/{SITE_A}/sms-budget"),
        _FORBIDDEN,
    ),
    Case(
        "GET",
        _BUDGET,
        404,
        "another clinic",
        lambda w: w.receptionist().get(f"/api/v1/sites/{SITE_B}/sms-budget"),
        _NOT_FOUND,
    ),
    Case(
        "GET",
        _BUDGET,
        422,
        "not a day",
        lambda w: w.manager().get(
            f"/api/v1/sites/{SITE_A}/sms-budget", params={"day": "yesterday"}
        ),
        _INVALID,
    ),
    Case(
        "PUT",
        _BUDGET,
        401,
        "no session",
        lambda w: w.anonymous().put(
            f"/api/v1/sites/{SITE_A}/sms-budget", json={"daily_cap": 10}
        ),
        _UNAUTH,
    ),
    Case(
        "PUT",
        _BUDGET,
        403,
        "a receptionist cannot change settings",
        lambda w: w.receptionist().put(
            f"/api/v1/sites/{SITE_A}/sms-budget", json={"daily_cap": 10}
        ),
        _FORBIDDEN,
    ),
    Case(
        "PUT",
        _BUDGET,
        404,
        "another clinic",
        lambda w: w.manager().put(
            f"/api/v1/sites/{SITE_B}/sms-budget", json={"daily_cap": 10}
        ),
        _NOT_FOUND,
    ),
    Case(
        "PUT",
        _BUDGET,
        422,
        "a negative cap",
        lambda w: w.manager().put(
            f"/api/v1/sites/{SITE_A}/sms-budget", json={"daily_cap": -1}
        ),
        _INVALID,
    ),
    Case(
        "POST",
        "/webhooks/sms/africastalking/{token}",
        400,
        "not a receipt",
        lambda w: _garbage_webhook(w, _RECEIPT),
        "http.bad_request",
    ),
    Case(
        "POST",
        "/webhooks/sms/africastalking/{token}",
        404,
        "wrong callback secret",
        lambda w: _receipt_webhook(w, _FORGED, sms_webhook_token=_SMS_TOKEN),
        "http.not_found",
    ),
    Case(
        "POST",
        "/webhooks/sms/africastalking/{token}",
        503,
        "webhook off",
        lambda w: _receipt_webhook(w, _RECEIPT),
        "http.service_unavailable",
    ),
    Case(
        "POST",
        "/webhooks/sms/africastalking/{token}/inbound",
        400,
        "not a message",
        lambda w: _garbage_webhook(w, _REPLY),
        "http.bad_request",
    ),
    Case(
        "POST",
        "/webhooks/sms/africastalking/{token}/inbound",
        404,
        "wrong callback secret",
        lambda w: _receipt_webhook(
            w, f"{_FORGED}/inbound", sms_webhook_token=_SMS_TOKEN
        ),
        "http.not_found",
    ),
    Case(
        "POST",
        "/webhooks/sms/africastalking/{token}/inbound",
        503,
        "webhook off",
        lambda w: _receipt_webhook(w, _REPLY),
        "http.service_unavailable",
    ),
    # ── Templates ────────────────────────────────────────────────────────────────────────
    *_operator_only("GET", "/notifications/templates", _T),
    *_operator_only(
        "POST",
        "/notifications/templates/preview",
        f"{_T}/preview",
        json={
            "template": "ticket_next",
            "channel": "sms",
            "language": "en",
            "body": "{number}",
        },
    ),
    Case(
        "POST",
        "/notifications/templates/preview",
        404,
        "not a patient template",
        lambda w: w.operator().post(
            f"{_T}/preview",
            json={
                "template": "otp_sign_in",
                "channel": "sms",
                "language": "en",
                "body": "{code}",
            },
        ),
        _NOT_FOUND,
    ),
    Case(
        "POST",
        "/notifications/templates/preview",
        404,
        "a language messages are not written in",
        lambda w: w.operator().post(
            f"{_T}/preview",
            json={
                "template": "ticket_next",
                "channel": "sms",
                "language": "tsn",
                "body": "{number}",
            },
        ),
        _NOT_FOUND,
    ),
    Case(
        "POST",
        "/notifications/templates/preview",
        422,
        "no body",
        lambda w: w.operator().post(f"{_T}/preview", json={"template": "ticket_next"}),
        _INVALID,
    ),
    *_operator_only(
        "GET", "/notifications/templates/versions/{version_id}", f"{_T}/versions/nope"
    ),
    Case(
        "GET",
        "/notifications/templates/versions/{version_id}",
        404,
        "no such version",
        lambda w: w.operator().get(f"{_T}/versions/nope"),
        _NOT_FOUND,
    ),
    *_operator_only("GET", _KEY, f"{_T}/ticket_next/sms/en"),
    Case(
        "GET",
        _KEY,
        404,
        "email is not a patient transport",
        lambda w: w.operator().get(f"{_T}/ticket_next/email/en"),
        _NOT_FOUND,
    ),
    Case(
        "GET",
        _KEY,
        422,
        "not a template",
        lambda w: w.operator().get(f"{_T}/ticket_soon/sms/en"),
        _INVALID,
    ),
    *_operator_only(
        "POST", _PUBLISH, f"{_T}/ticket_next/sms/en/versions", json={"body": "{number}"}
    ),
    Case(
        "POST",
        _PUBLISH,
        404,
        "not a patient template",
        lambda w: w.operator().post(
            f"{_T}/otp_sign_in/sms/en/versions", json={"body": "{code}"}
        ),
        _NOT_FOUND,
    ),
    Case(
        "POST",
        _PUBLISH,
        422,
        "a blank the message does not have",
        lambda w: w.operator().post(
            f"{_T}/ticket_recalled/sms/en/versions",
            json={"body": "{app}: ticket {number}, come within {minuts} minutes."},
        ),
        _REFUSED,
    ),
    Case(
        "POST",
        _PUBLISH,
        422,
        "an empty body",
        lambda w: w.operator().post(
            f"{_T}/ticket_next/sms/en/versions", json={"body": ""}
        ),
        _INVALID,
    ),
    # ── A patient's browsers and preferences ─────────────────────────────────────────────
    Case(
        "POST",
        "/notifications/web-push/subscriptions",
        401,
        "no patient session",
        lambda w: w.anonymous().post(
            f"{_N}/web-push/subscriptions", json=_SUBSCRIPTION
        ),
        _UNAUTH,
    ),
    Case(
        "POST",
        "/notifications/web-push/subscriptions",
        401,
        "a staff session is not a patient's",
        lambda w: w.receptionist().post(
            f"{_N}/web-push/subscriptions", json=_SUBSCRIPTION
        ),
        _UNAUTH,
    ),
    Case(
        "POST",
        "/notifications/web-push/subscriptions",
        403,
        "a cookie session without the CSRF token",
        lambda w: w.patient_cookie().post(
            f"{_N}/web-push/subscriptions", json=_SUBSCRIPTION
        ),
        None,
    ),
    Case(
        "POST",
        "/notifications/web-push/subscriptions",
        409,
        "web push off",
        lambda w: w.patient().post(f"{_N}/web-push/subscriptions", json=_SUBSCRIPTION),
        "http.conflict",
    ),
    Case(
        "POST",
        "/notifications/web-push/subscriptions",
        422,
        "not a push service",
        _subscribe_where_no_push_service_is,
        _REFUSED,
    ),
    Case(
        "DELETE",
        "/notifications/web-push/subscriptions",
        401,
        "no patient session",
        lambda w: w.anonymous().request(
            "DELETE",
            f"{_N}/web-push/subscriptions",
            json={"endpoint": _SUBSCRIPTION["endpoint"]},
        ),
        _UNAUTH,
    ),
    Case(
        "DELETE",
        "/notifications/web-push/subscriptions",
        403,
        "a cookie session without the CSRF token",
        lambda w: w.patient_cookie().request(
            "DELETE",
            f"{_N}/web-push/subscriptions",
            json={"endpoint": _SUBSCRIPTION["endpoint"]},
        ),
        None,
    ),
    Case(
        "DELETE",
        "/notifications/web-push/subscriptions",
        422,
        "an endpoint too short",
        lambda w: w.patient().request(
            "DELETE", f"{_N}/web-push/subscriptions", json={"endpoint": "x"}
        ),
        _INVALID,
    ),
    Case(
        "GET",
        "/notifications/patient-preferences/{page_token}",
        404,
        "no ticket has the link",
        lambda w: w.anonymous().get(f"{_N}/patient-preferences/{_NO_LINK}"),
        _NOT_FOUND,
    ),
    Case(
        "PUT",
        "/notifications/patient-preferences/{page_token}",
        404,
        "no ticket has the link",
        lambda w: w.anonymous().put(f"{_N}/patient-preferences/{_NO_LINK}", json={}),
        _NOT_FOUND,
    ),
    Case(
        "PUT",
        "/notifications/patient-preferences/{page_token}",
        422,
        "email is not a patient transport",
        lambda w: w.anonymous().put(
            f"{_N}/patient-preferences/{w.page_token()}",
            json={"preferred_channel": "email"},
        ),
        _REFUSED,
    ),
    Case(
        "PUT",
        "/notifications/patient-preferences/{page_token}",
        422,
        "half a quiet-hours window",
        lambda w: w.anonymous().put(
            f"{_N}/patient-preferences/{w.page_token()}",
            json={"quiet_hours_start": "22:00"},
        ),
        _INVALID,
    ),
    # ── An account holder's preferences and notification centre ──────────────────────────
    Case(
        "GET",
        "/notifications/preferences",
        401,
        "no session",
        lambda w: w.anonymous().get(f"{_N}/preferences"),
        _UNAUTH,
    ),
    Case(
        "PUT",
        "/notifications/preferences",
        401,
        "no session",
        lambda w: w.anonymous().put(f"{_N}/preferences", json={}),
        _UNAUTH,
    ),
    Case(
        "PUT",
        "/notifications/preferences",
        403,
        "a cookie session without the CSRF token",
        lambda w: w.staff_without_csrf().put(f"{_N}/preferences", json={}),
        None,
    ),
    Case(
        "PUT",
        "/notifications/preferences",
        422,
        "an unknown timezone",
        lambda w: w.receptionist().put(
            f"{_N}/preferences", json={"timezone": "Mars/Olympus_Mons"}
        ),
        _REFUSED,
    ),
    *_centre("GET", "/notifications/center", f"{_N}/center"),
    Case(
        "GET",
        "/notifications/center",
        422,
        "limit 0",
        lambda w: w.operator().get(f"{_N}/center", params={"limit": 0}),
        _INVALID,
    ),
    *_centre("GET", "/notifications/center/unread-count", f"{_N}/center/unread-count"),
    *_centre("POST", "/notifications/center/read-all", f"{_N}/center/read-all"),
    *_centre("POST", "/notifications/center/{item_id}/read", f"{_N}/center/nope/read"),
    Case(
        "POST",
        "/notifications/center/{item_id}/read",
        404,
        "not the caller's",
        lambda w: w.operator().post(f"{_N}/center/nope/read"),
        _NOT_FOUND,
    ),
    *_centre(
        "POST", "/notifications/center/{item_id}/unread", f"{_N}/center/nope/unread"
    ),
    Case(
        "POST",
        "/notifications/center/{item_id}/unread",
        404,
        "not the caller's",
        lambda w: w.operator().post(f"{_N}/center/nope/unread"),
        _NOT_FOUND,
    ),
)


# --------------------------------------------------------------------------------------
# Reading the contract
# --------------------------------------------------------------------------------------

Operation = tuple[str, str, str]


@cache
def _contract() -> dict[str, Any]:
    """The contract, parsed once: it does not change during a run, and every case reads it."""
    return yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def documented_errors(document: dict[str, Any]) -> set[Operation]:
    """Every ``(METHOD, path, status)`` the document promises a 4xx or 5xx for (wildcards skipped)."""
    return {
        (method.upper(), path, code)
        for path, operations in document.get("paths", {}).items()
        for method, operation in operations.items()
        if method in _METHODS
        for code in operation.get("responses", {})
        if code[0] in "45" and code.isdigit()
    }


def proven_errors(cases: tuple[Case, ...]) -> set[Operation]:
    """Every ``(METHOD, path, status)`` a case drives."""
    return {(case.method, case.path, str(case.status)) for case in cases}


def _response(document: dict[str, Any], case: Case) -> dict[str, Any]:
    """The documented response a case proves, with a ``$ref`` to a shared response resolved."""
    response = document["paths"][case.path][case.method.lower()]["responses"][
        str(case.status)
    ]
    ref = response.get("$ref", "")
    if ref:
        return document["components"]["responses"][ref.rsplit("/", 1)[1]]  # type: ignore[no-any-return]
    return response  # type: ignore[no-any-return]


def _readable(operations: set[Operation]) -> str:
    return "\n".join(f"  {m} {p} {s}" for m, p, s in sorted(operations))


# --------------------------------------------------------------------------------------
# The proof
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_the_documented_refusal_is_what_the_application_answers(
    case: Case, world: World
) -> None:
    """Drive the refusal over real HTTP; the status and the ``code`` are the ones documented."""
    answer = case.drive(world)
    assert answer.status_code == case.status, answer.text
    body = answer.json()
    assert body.get("code") == case.code, body
    if case.code is not None:
        assert case.code in yaml.safe_dump(_response(_contract(), case)), (
            f"{case.method} {case.path} {case.status} does not name the code {case.code}"
        )


def test_every_documented_error_status_has_a_case() -> None:
    """A refusal written in the contract with nothing driving it is a promise nobody checked."""
    unproved = documented_errors(_contract()) - proven_errors(CASES)
    assert not unproved, (
        "notifications.yaml documents these error statuses, and no case drives them:\n"
        + _readable(unproved)
    )


def test_every_case_is_a_documented_error_status() -> None:
    """A refusal the application gives, and the contract does not mention, is a hole in the contract."""
    undocumented = proven_errors(CASES) - documented_errors(_contract())
    assert not undocumented, (
        "these cases drive error statuses notifications.yaml does not document:\n"
        + _readable(undocumented)
    )


def test_the_contract_documents_errors_on_most_of_its_operations() -> None:
    """A guard with nothing to compare passes forever: the reading found the contract's refusals."""
    document = _contract()
    operations = {
        (method, path)
        for path, ops in document["paths"].items()
        for method in ops
        if method in _METHODS
    }
    errors = documented_errors(document)
    assert len(operations) >= 27
    assert len({(m, p) for m, p, _ in errors}) >= len(operations) - 1
    assert len(errors) >= 70


def test_a_wrong_callback_secret_is_word_for_word_a_path_that_does_not_exist(
    world: World,
) -> None:
    """The SMS callbacks answer in the envelope, and a wrong secret cannot be told from no route at all."""
    world.configure(sms_webhook_token=_SMS_TOKEN)
    client = world.anonymous()
    nowhere = client.post("/api/v1/webhooks/sms/nobody-listens-here", content=b"id=1")
    assert nowhere.status_code == 404

    def shape(response: httpx.Response) -> tuple[int, dict[str, Any]]:
        body = response.json()
        assert body.pop("request_id"), "every refusal carries its request id"
        return response.status_code, body

    for path in (_FORGED, f"{_FORGED}/inbound"):
        forged = client.post(
            path,
            content=b"id=ATXid_1&status=Success",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert (
            shape(forged)
            == shape(nowhere)
            == (
                404,
                {"detail": "Not Found", "code": "http.not_found"},
            )
        )


def test_the_reconciliation_fails_both_ways() -> None:
    """A fixture that is exactly the two mistakes this file exists to catch."""
    document = {
        "paths": {
            "/things/{id}": {
                "get": {
                    "responses": {"200": {}, "404": {}, "409": {}, "4XX": {}},
                }
            }
        }
    }
    cases = (
        Case("GET", "/things/{id}", 404, "none", lambda w: httpx.Response(404), None),
        Case("GET", "/things/{id}", 403, "none", lambda w: httpx.Response(403), None),
    )
    assert documented_errors(document) - proven_errors(cases) == {
        ("GET", "/things/{id}", "409")
    }
    assert proven_errors(cases) - documented_errors(document) == {
        ("GET", "/things/{id}", "403")
    }
