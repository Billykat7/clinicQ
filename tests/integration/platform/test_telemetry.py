"""Error tracking and metrics, over HTTP (Issue 14).

An exception must reach error tracking once, carrying what traces it back: the request id the
response returned, the release and the commit. The metrics must count by route template and stay
closed to anyone without the token. Error tracking is exercised through the real Sentry SDK with
its transport replaced by a list, so nothing leaves the process.
"""

from collections.abc import Generator
from functools import partial
from typing import Any

import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from src.commons.enums import AppEnvironment
from src.core import telemetry
from src.core.config import Settings
from src.main import create_app

SHA = "0123456789abcdef0123456789abcdef01234567"
TEST_ROUTE = "/health/error-tracking-test"
METRICS_TOKEN = "metrics-token-for-the-telemetry-test"


class CapturingTransport(Transport):
    """A transport that keeps each event in a list instead of sending it."""

    def __init__(self) -> None:
        """Start with no events."""
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        """Keep the envelope's event, if it carries one."""
        event = envelope.get_event()
        if event is not None:
            self.events.append(dict(event))


def _settings(**values: object) -> Settings:
    """Staging-like settings, isolated from the local .env."""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "VERSION": "0.2.0",
        "git_sha": SHA,
    }
    return Settings(**(base | values))  # type: ignore[arg-type]


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> Generator[CapturingTransport]:
    """Error tracking on, its events captured here; the SDK is switched off afterwards."""
    transport = CapturingTransport()
    monkeypatch.setattr(
        telemetry.sentry_sdk, "init", partial(sentry_sdk.init, transport=transport)
    )
    yield transport
    sentry_sdk.get_client().close()
    sentry_sdk.init()


def test_an_unhandled_exception_reaches_error_tracking_once_with_its_ids(
    captured: CapturingTransport,
) -> None:
    """The acceptance criterion: request id, release tag and commit, on exactly one event."""
    settings = _settings(
        sentry_dsn="https://public@errors.example.org/1",
        error_tracking_test_route=True,
        environment=AppEnvironment.STAGING,
        jwt_secret="a-staging-secret-that-is-long-enough-32",
    )
    client = TestClient(create_app(settings), raise_server_exceptions=False)
    client.cookies.set("bk_clinicq_access_token", "secret-cookie")

    response = client.get(TEST_ROUTE)
    sentry_sdk.flush()

    assert response.status_code == 500
    (event,) = captured.events
    tags = event["tags"]
    assert tags["request_id"] == response.headers["X-Request-ID"]
    assert tags["git_sha"] == SHA
    assert event["release"] == "clinicq@0.2.0"
    assert event["environment"] == AppEnvironment.STAGING.value
    # One event, carrying the exception's whole chain (Starlette wraps it in an ExceptionGroup).
    chain = [value["type"] for value in event["exception"]["values"]]
    assert telemetry.ErrorTrackingTestError.__name__ in chain
    assert "cookies" not in event.get("request", {})
    assert "secret-cookie" not in str(event)


def test_error_tracking_is_off_without_a_dsn() -> None:
    """No DSN, no client: nothing is ever sent."""
    assert telemetry.init_error_tracking(_settings()) is False


def test_the_test_route_exists_only_when_asked_for() -> None:
    """Off by default (404); production refuses the setting outright (test_config_guards)."""
    assert TestClient(create_app(_settings())).get(TEST_ROUTE).status_code == 404


def _requests(route: str, status: str) -> float:
    """The request counter's value for one route template and status class."""
    value = REGISTRY.get_sample_value(
        "clinicq_http_requests_total",
        {"method": "GET", "route": route, "status": status},
    )
    return value or 0.0


def test_metrics_count_requests_by_route_template_and_status_class() -> None:
    """Rate, errors and latency, keyed by template so a URL with an id is not a new series."""
    client = TestClient(create_app(_settings(metrics_enabled=True)))
    health, unmatched = _requests("/health", "2xx"), _requests("unmatched", "4xx")

    client.get("/health")
    client.get("/no-such-page-4711")
    body = client.get("/metrics").text

    assert _requests("/health", "2xx") == health + 1
    assert _requests("unmatched", "4xx") == unmatched + 1
    assert "clinicq_http_request_duration_seconds_bucket" in body
    assert "no-such-page-4711" not in body


def test_metrics_answer_only_with_the_token_and_not_at_all_when_off() -> None:
    """A configured token is required; without METRICS_ENABLED there is no endpoint."""
    client = TestClient(
        create_app(_settings(metrics_enabled=True, metrics_token=METRICS_TOKEN))
    )
    assert client.get("/metrics").status_code == 401
    assert (
        client.get(
            "/metrics", headers={"Authorization": "Bearer the-wrong-token"}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"}
        ).status_code
        == 200
    )
    assert TestClient(create_app(_settings())).get("/metrics").status_code == 404
