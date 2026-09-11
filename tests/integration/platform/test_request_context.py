"""One request id ties every log line together, and the probes say which dependency is down (Issue 6).

Real requests through ``create_app()``, with the console handler ``setup_logging`` builds writing
into a buffer, so what is asserted is the JSON a log pipeline would receive. The routes below are
added for the test: one logs from three modules (one of them in a worker thread, as a sync
dependency runs), one binds a site the way the tenancy helper will (Issue 19), one fails.
"""

import io
import json
import logging
import time
from collections.abc import Iterator
from http import HTTPStatus
from typing import Annotated, Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

import src.core.health as health
from src.commons.enums import AppEnvironment, DependencyStatus, LogFormat
from src.commons.exceptions import ConflictError
from src.core.config import Settings, get_settings
from src.core.health import database_status, migrations_status, storage_status
from src.core.logging_config import build_console_handler
from src.core.request_logging import REQUEST_ID_HEADER, bind_request_context
from src.core.security import create_access_token, get_current_user
from src.database.session import get_db
from src.main import create_app

_SECRET = "issue-6-request-context-secret-at-least-32-chars"
_SITE = "01a0f000-0000-7000-8000-000000000042"
_ACTOR = "01a0f000-0000-7000-8000-00000000a11c"


def _settings(**overrides: Any) -> Settings:
    """Isolated settings: no local ``.env`` can change an outcome."""
    return Settings(
        _env_file=None,
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret=_SECRET,
        auth_enabled=True,
        **overrides,
    )


def _scoped_site() -> str:
    """A sync dependency (so it runs in a worker thread) that binds the request's site."""
    logging.getLogger("src.modules.sites.scope").info("Scoped to site")
    bind_request_context(site_id=_SITE)
    return _SITE


def _routes() -> APIRouter:
    router = APIRouter(prefix="/_issue6")

    @router.get("/work")
    async def work(
        user: Annotated[dict[str, Any], Depends(get_current_user)],
        site: Annotated[str, Depends(_scoped_site)],
    ) -> dict[str, str]:
        logging.getLogger("src.modules.queue.service").info("Joined the queue")
        logging.getLogger("src.modules.notifications.sms").info("SMS queued")
        return {"site": site}

    @router.get("/refused")
    def refused(site: Annotated[str, Depends(_scoped_site)]) -> None:
        raise ConflictError("Ticket already called.", code="queue.ticket.illegal")

    @router.get("/crash")
    def crash() -> None:
        raise RuntimeError("boom")

    return router


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    """The real console handler (JSON, context and redaction filters) writing to a buffer.

    ``create_app`` would call ``setup_logging`` and put the stdout handler back, so it is told
    not to; the handler installed here is the one ``setup_logging`` builds.
    """
    monkeypatch.setattr("src.main.setup_logging", lambda: None)
    stream = io.StringIO()
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    root.handlers[:] = [build_console_handler(LogFormat.JSON, logging.INFO, stream)]
    root.setLevel(logging.INFO)
    yield stream
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def _app(**overrides: Any) -> FastAPI:
    settings = _settings(**overrides)
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.include_router(_routes())
    return app


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    """Every console line as JSON; ``json.loads`` failing here means a line was not NDJSON."""
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def _token() -> str:
    return create_access_token(sub="nurse@example.com", uid=_ACTOR)


def test_every_line_of_a_request_carries_its_id_site_and_actor(
    logs: io.StringIO, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three modules, one thread hop: every line has the id the response header returns."""
    monkeypatch.setattr("src.core.security.get_settings", lambda: _settings())
    client = TestClient(_app())

    response = client.get(
        "/_issue6/work", headers={"Authorization": f"Bearer {_token()}"}
    )

    assert response.status_code == HTTPStatus.OK
    request_id = response.headers[REQUEST_ID_HEADER]
    mine = [line for line in _lines(logs) if line["context"]["path"] == "/_issue6/work"]
    assert [line["message"] for line in mine] == [
        "Scoped to site",
        "Joined the queue",
        "SMS queued",
    ]
    assert {line["context"]["request_id"] for line in mine} == {request_id}
    # The site is bound inside the dependency that logs "Scoped to site"; the actor before it.
    assert [line["context"]["site_id"] for line in mine] == [None, _SITE, _SITE]
    assert {line["context"]["actor_id"] for line in mine} == {_ACTOR}
    # The actor is the user's id: the email in ``sub`` appears nowhere.
    assert "nurse@example.com" not in logs.getvalue()


def test_the_closing_line_of_a_refused_request_carries_the_late_bound_site(
    logs: io.StringIO,
) -> None:
    """The middleware's own 4xx line sees a site bound in a worker thread, after it started."""
    response = TestClient(_app()).get("/_issue6/refused")

    assert response.status_code == HTTPStatus.CONFLICT
    closing = next(
        line
        for line in _lines(logs)
        if line["message"] == "HTTP 409 GET /_issue6/refused"
    )
    assert closing["context"]["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert closing["context"]["site_id"] == _SITE
    assert response.json()["request_id"] == response.headers[REQUEST_ID_HEADER]


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/health", HTTPStatus.OK),
        ("/api/v1/reference/enums", HTTPStatus.OK),
        ("/api/v1/does-not-exist", HTTPStatus.NOT_FOUND),
        ("/_issue6/crash", HTTPStatus.INTERNAL_SERVER_ERROR),
    ],
)
def test_every_response_carries_the_request_id(
    logs: io.StringIO, path: str, status: HTTPStatus
) -> None:
    """Success, a 404 and an unhandled 500 all return ``X-Request-ID``, a UUIDv7."""
    with TestClient(_app(), raise_server_exceptions=False) as client:
        response = client.get(path)

    assert response.status_code == status
    assert len(response.headers[REQUEST_ID_HEADER]) == 36
    assert response.headers[REQUEST_ID_HEADER][14] == "7"


def test_an_unhandled_error_is_logged_with_its_request_id_and_answered_with_it(
    logs: io.StringIO,
) -> None:
    """The 500 a user sees quotes the id of the traceback in the logs."""
    with TestClient(_app(), raise_server_exceptions=False) as client:
        response = client.get("/_issue6/crash")

    request_id = response.headers[REQUEST_ID_HEADER]
    assert response.json()["request_id"] == request_id
    logged = next(line for line in _lines(logs) if line["level"] == "ERROR")
    assert logged["context"]["request_id"] == request_id
    assert "RuntimeError: boom" in logged["exception"]


@pytest.mark.parametrize(
    ("trust_proxy", "inbound", "kept"),
    [
        (True, "5f0c3a1e9b2d4c6f8a7e1d3b5c9f2a4e", True),  # nginx's $request_id
        (True, "bad id\nforged log line", False),
        (True, "x" * 200, False),
        (False, "5f0c3a1e9b2d4c6f8a7e1d3b5c9f2a4e", False),  # no trusted proxy in front
    ],
)
def test_an_inbound_request_id_is_kept_only_from_a_trusted_proxy_in_a_safe_format(
    logs: io.StringIO, trust_proxy: bool, inbound: str, kept: bool
) -> None:
    """The id nginx logged stays the id the app logs; a client cannot inject a log line."""
    response = TestClient(_app(trust_proxy_headers=trust_proxy)).get(
        "/health", headers={REQUEST_ID_HEADER: inbound}
    )
    assert (response.headers[REQUEST_ID_HEADER] == inbound) is kept


@pytest.mark.slow
def test_liveness_never_touches_the_database_and_answers_fast() -> None:
    """With every session request failing, ``/health`` still answers, p95 under 50 ms.

    Best of three rounds of 100: a round slowed by other test workers sharing the CPU is noise,
    while a liveness that touched a dependency would be slow in every round (and here, would
    fail outright, because the database dependency raises).
    """
    app = _app()

    def _no_database() -> Iterator[None]:
        raise AssertionError("liveness must not open a database session")
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = _no_database
    client = TestClient(app)
    client.get("/health")  # warm up

    def _p95_ms() -> float:
        timings = []
        for _ in range(100):
            started = time.perf_counter()
            assert client.get("/health").status_code == HTTPStatus.OK
            timings.append((time.perf_counter() - started) * 1000)
        return sorted(timings)[94]

    best = min(_p95_ms() for _ in range(3))
    assert best < 50, f"p95 {best:.1f} ms"


def test_readiness_names_redis_when_it_is_unreachable(logs: io.StringIO) -> None:
    """A configured Redis that does not answer: 503, ``checks.redis: down``; liveness stays 200.

    The real probe runs, against a port nothing listens on; the other dependencies are pinned
    healthy so the verdict is Redis's alone.
    """
    app = _app(redis_url="redis://127.0.0.1:1/0", redis_probe_timeout_seconds=0.5)
    for dependency in (database_status, migrations_status):
        app.dependency_overrides[dependency] = lambda: DependencyStatus.OK
    app.dependency_overrides[storage_status] = lambda: DependencyStatus.SKIPPED
    client = TestClient(app)

    readiness = client.get("/health/ready")

    assert readiness.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert readiness.json()["checks"]["redis"] == DependencyStatus.DOWN
    assert "127.0.0.1" not in readiness.text  # the reason goes to the log, not the body
    assert client.get("/health").status_code == HTTPStatus.OK


def test_the_redis_probe_is_ok_when_redis_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PING answered: ``ok``, and the client is closed either way."""
    closed: list[bool] = []

    class _Redis:
        def ping(self) -> bool:
            return True

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr("redis.Redis.from_url", lambda *a, **k: _Redis())
    status = health.probe_redis(_settings(redis_url="redis://cache:6379/0"))
    assert status is DependencyStatus.OK
    assert closed == [True]


def test_the_redis_probe_is_skipped_without_a_url() -> None:
    """No ``REDIS_URL``: this deployment has no Redis to be unready for."""
    assert health.probe_redis(_settings()) is DependencyStatus.SKIPPED


def test_a_request_the_csrf_middleware_refuses_still_carries_an_id(
    logs: io.StringIO,
) -> None:
    """The context middleware is outermost, so even a CSRF refusal has an id and a log line."""
    client = TestClient(_app())
    client.cookies.set(get_settings().csrf_cookie_name, "cookie-token")

    response = client.post("/api/v1/auth/logout")  # no X-CSRF-Token header

    assert response.status_code == HTTPStatus.FORBIDDEN
    request_id = response.headers[REQUEST_ID_HEADER]
    security = next(
        line for line in _lines(logs) if line["message"].startswith("SECURITY_HTTP")
    )
    assert security["context"]["request_id"] == request_id
