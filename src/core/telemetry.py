"""Error tracking and request metrics (Issue 14).

**Error tracking.** With ``SENTRY_DSN`` set, every unhandled exception is sent to Sentry, or to any
service that speaks its protocol (GlitchTip), tagged so it can be traced to a request and a commit:

* ``request_id``: the id the request carried through every log line (Issue 6), and the
  ``X-Request-ID`` header its response returned;
* ``release``: ``clinicq@<version>``, the release tag the image was built from (Issue 10);
* ``git_sha``: the commit, as ``/health`` reports it;
* ``environment``: staging or production.

Only errors are sent (no performance traces unless ``SENTRY_TRACES_SAMPLE_RATE`` says so), and no
personal data: no cookies, headers that authenticate, user IP, request body or stack-frame local
variables (which would carry all of those). Log lines become breadcrumbs, never events of their
own, so the one exception is one event.

**Metrics.** With ``METRICS_ENABLED``, ``/metrics`` serves Prometheus counters for request rate and
errors and a histogram for latency, labelled by the route's *template* (``/api/v1/clinics/{id}``,
never the raw path, whose ids would make every URL a new series). Outside development it answers
only to ``Authorization: Bearer <METRICS_TOKEN>``.
"""

from __future__ import annotations

import hmac
import logging
import time
from typing import TYPE_CHECKING

import sentry_sdk
from fastapi import Request, Response
from fastapi import status as http_status
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sentry_sdk.integrations.logging import LoggingIntegration

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable

    from sentry_sdk.types import Event, Hint
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from src.core.config import Settings

logger = logging.getLogger(__name__)

#: The prefix of a Sentry release name, so ClinicQ's releases are told apart in a shared account.
RELEASE_PREFIX = "clinicq@"

#: The label a request gets when no route matched it (a 404), so scanners do not add series.
UNMATCHED_ROUTE = "unmatched"

REQUESTS = Counter(
    "clinicq_http_requests_total",
    "HTTP requests answered, by method, route template and status class.",
    ["method", "route", "status"],
)
LATENCY = Histogram(
    "clinicq_http_request_duration_seconds",
    "Time to answer an HTTP request, by method and route template.",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


class ErrorTrackingTestError(RuntimeError):
    """Raised on purpose by /health/error-tracking-test, to prove error tracking end to end."""


def release_name(settings: Settings) -> str:
    """The Sentry release for this build: ``clinicq@0.2.0``."""
    return f"{RELEASE_PREFIX}{settings.version}"


def _scrub(event: Event, _hint: Hint) -> Event | None:
    """Drop what could identify a person from an event before it leaves the process.

    ``send_default_pii=False`` already leaves most of it out; this holds the line whatever the
    SDK's defaults become: no cookies, request body or server environment, and no user.
    """
    request = event.get("request")
    if request is not None:
        request.pop("cookies", None)
        request.pop("data", None)
        request.pop("env", None)
    event.pop("user", None)
    return event


def init_error_tracking(settings: Settings) -> bool:
    """Start sending unhandled exceptions to ``SENTRY_DSN``; return whether it is on."""
    if not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment.value,
        release=release_name(settings),
        send_default_pii=False,
        # Stack frames' local variables hold the raw ASGI scope (every header, cookies included),
        # tokens and patient data; the tests caught a session cookie leaving that way. The
        # traceback's lines are enough to find a bug, and request bodies are never sent.
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        before_send=_scrub,
        # Log records are breadcrumbs on the exception's event, never events of their own: the
        # request logger's "Unhandled error" line would otherwise send the same failure twice.
        integrations=[LoggingIntegration(level=logging.INFO, event_level=None)],
    )
    sentry_sdk.set_tag("git_sha", settings.git_sha)
    logger.info(
        "Error tracking on: release %s, environment %s",
        release_name(settings),
        settings.environment.value,
    )
    return True


def tag_request(request_id: str) -> None:
    """Tag everything Sentry captures during this request with its id (a no-op when it is off).

    Set as the request starts, on the per-request scope Sentry's middleware opens, because the
    exception is captured outermost, after the request's logging context has been unwound.
    """
    sentry_sdk.get_isolation_scope().set_tag("request_id", request_id)


def _route_template(scope: Scope) -> str:
    """The template of the route that answered, e.g. ``/api/v1/clinics/{id}``."""
    route = scope.get("route")
    path = getattr(route, "path_format", None) or getattr(route, "path", None)
    if path:
        return str(path)
    root_path = scope.get("root_path") or ""
    return str(root_path) if root_path else UNMATCHED_ROUTE


class MetricsMiddleware:
    """Count every HTTP request and time it, by method, route template and status class."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve the request and record it, whether it succeeded, failed or raised."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status_code = http_status.HTTP_500_INTERNAL_SERVER_ERROR

        async def record_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, record_status)
        finally:
            route = _route_template(scope)
            method = scope.get("method", "")
            REQUESTS.labels(method, route, f"{status_code // 100}xx").inc()
            LATENCY.labels(method, route).observe(time.perf_counter() - started)


def metrics_endpoint(settings: Settings) -> Callable[[Request], Response]:
    """The /metrics handler, which requires the bearer token whenever one is configured."""
    expected = (
        f"Bearer {settings.metrics_token}".encode() if settings.metrics_token else b""
    )

    def metrics(request: Request) -> Response:
        """Prometheus text format: request counts, error counts by status class, latency."""
        # Compared as bytes, in constant time: a header with non-ASCII text is simply wrong.
        given = request.headers.get("authorization", "").encode()
        if expected and not hmac.compare_digest(given, expected):
            return Response(status_code=http_status.HTTP_401_UNAUTHORIZED)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return metrics


def raise_for_error_tracking() -> None:
    """GET /health/error-tracking-test: fail on purpose, so the failure can be found downstream."""
    raise ErrorTrackingTestError(
        "Deliberate error to prove error tracking end to end (Issue 14)"
    )
