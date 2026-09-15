"""ASGI entrypoint for the BK ClinicQ API (monolith; modules map to future services)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Response
from fastapi import status as http_status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.v1.router import api_v1_router
from src.commons.enums import AppEnvironment, DependencyStatus, HealthStatus
from src.core.config import CORS_ANY_ORIGIN, Settings, get_settings
from src.core.csrf_middleware import CsrfProtectMiddleware
from src.core.error_handlers import ERROR_RESPONSES, install_error_handlers
from src.core.health import (
    aggregate_status,
    database_status,
    migrations_status,
    probe_cert,
    redis_status,
    storage_status,
)
from src.core.live_events import start_fanout, stop_fanout
from src.core.logging_config import setup_logging
from src.core.request_logging import RequestLoggingMiddleware
from src.core.scheduler import shutdown_scheduler, start_scheduler
from src.core.security_headers import SecurityHeadersMiddleware
from src.core.telemetry import (
    MetricsMiddleware,
    init_error_tracking,
    metrics_endpoint,
    raise_for_error_tracking,
)
from src.schemas.health import DependencyChecks, LivenessResponse, ReadinessResponse
from src.web.dashboard.lookup import router as dashboard_lookup_router
from src.web.dashboard.routes import router as dashboard_router
from src.web.dashboard.settings import router as dashboard_settings_router
from src.web.dashboard.walkin import router as dashboard_walk_in_router
from src.web.dev import router as dev_router
from src.web.discover import router as discover_router
from src.web.display import router as display_router
from src.web.display_stream import router as display_stream_router
from src.web.join import router as join_router
from src.web.routes import router as web_router
from src.web.ticket import router as ticket_router
from src.web.ticket import worker_router as patient_worker_router

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(settings_obj: Settings | None = None) -> FastAPI:
    cfg = settings_obj if settings_obj is not None else get_settings()
    setup_logging()
    # Before the app exists, so Sentry's FastAPI integration wraps it (Issue 14). Off without a DSN.
    init_error_tracking(cfg)
    is_prod = cfg.environment == AppEnvironment.PRODUCTION

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Start background jobs on boot (Issue #38) and stop them on shutdown."""
        start_scheduler(cfg)
        # Live events reach every instance's screens through Redis when it is configured (Issue 57).
        start_fanout(cfg)
        try:
            yield
        finally:
            stop_fanout()
            shutdown_scheduler()

    app = FastAPI(
        title=cfg.app_name,
        version=cfg.version,
        # Tracebacks in error responses. Development only: the settings refuse DEBUG elsewhere.
        debug=cfg.debug,
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
        lifespan=lifespan,
    )
    # Added last = outermost (Starlette wraps in reverse), so every response carries X-Request-ID
    # and its log lines their context, including a request the CSRF middleware refuses (Issue 6).
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CsrfProtectMiddleware)
    # Cross-origin browser access only for the origins CORS_ORIGINS names (Issue 12); none by
    # default, so the app stays same-origin. Outside CSRF, so a preflight is answered before the
    # CSRF check. Credentials are never offered to the development-only wildcard.
    if cfg.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.cors_origins,
            allow_credentials=CORS_ANY_ORIGIN not in cfg.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(
        RequestLoggingMiddleware, trust_proxy_headers=cfg.trust_proxy_headers
    )
    # Outermost of ours, so it times and counts everything, the requests the others refuse included.
    if cfg.metrics_enabled:
        app.add_middleware(MetricsMiddleware)
        app.add_api_route(
            "/metrics",
            metrics_endpoint(cfg),
            methods=["GET"],
            include_in_schema=False,
        )
    # Staging only (production refuses the setting): a route that fails on purpose, so an
    # exception can be followed from the request into error tracking (Issue 14).
    if cfg.error_tracking_test_route:
        app.add_api_route(
            "/health/error-tracking-test",
            raise_for_error_tracking,
            methods=["GET"],
            include_in_schema=False,
        )
    # One error envelope for every API error (Issue 4): domain errors, HTTPException, validation.
    install_error_handlers(app)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(web_router)
    # The clinic dashboard (M7): one clinic's screens, inside the frame Issue 48 resolves.
    app.include_router(dashboard_router)
    app.include_router(dashboard_settings_router)
    app.include_router(dashboard_walk_in_router)
    app.include_router(dashboard_lookup_router)
    # Clinic discovery (Issue 32): the patient's first screen, on the discovery service.
    app.include_router(discover_router)
    # Joining a queue from the web (Issue 200): sign in by phone, then the queue.
    app.include_router(join_router)
    # The waiting-room board (M8): public, read-only, fed only by the privacy projection (Issue 58).
    app.include_router(display_router)
    app.include_router(display_stream_router)
    app.include_router(ticket_router)
    app.include_router(patient_worker_router)
    # The component catalogue and layout samples (Issue 5) exist only in development: in staging
    # and production the paths are not registered at all, so they answer 404.
    if cfg.environment is AppEnvironment.DEVELOPMENT:
        app.include_router(dev_router)
    app.include_router(api_v1_router, prefix="/api/v1", responses=ERROR_RESPONSES)

    def liveness() -> LivenessResponse:
        """Liveness: the process is up. Touches no dependency, so it never restart-loops
        on a database or S3 outage — that is readiness' job (Issue #65).

        Served at both ``/health`` and ``/health/live``. ``/health`` is the *uniform*
        platform heartbeat every product exposes and the infra gateway polls by default,
        so it must stay dependency-free and cheap; ``/health/live`` is the same probe under
        an explicit name for the container ``HEALTHCHECK``. The richer per-dependency verdict
        lives at ``/health/ready``.

        The optional ``cert`` block is the one exception to "touches no dependency": it is a
        cached read of a *local* PEM (no network, no DB), reported so the infra gateway —
        which can't read the edge cert files — can surface expiry. It is omitted, never
        failing, when unconfigured or unreadable."""
        return LivenessResponse(
            status=HealthStatus.OK,
            version=cfg.version,
            git_sha=cfg.git_sha,
            cert=probe_cert(cfg),
        )

    # Two paths, one handler: /health is the uniform heartbeat (what the infra health poller
    # hits for every service), /health/live is the explicit alias the Docker HEALTHCHECK uses.
    # ``exclude_none`` keeps the body minimal: the ``cert`` block appears only when a cert
    # path is configured, so services without one (and every existing test) see the exact
    # same ``{status, …}`` shape as before.
    app.add_api_route(
        "/health",
        liveness,
        methods=["GET"],
        response_model=LivenessResponse,
        response_model_exclude_none=True,
    )
    app.add_api_route(
        "/health/live",
        liveness,
        methods=["GET"],
        response_model=LivenessResponse,
        response_model_exclude_none=True,
    )

    @app.get(
        "/health/ready",
        response_model=ReadinessResponse,
        response_model_exclude_none=True,
    )
    def readiness(
        response: Response,
        database: DependencyStatus = Depends(database_status),
        migrations: DependencyStatus = Depends(migrations_status),
        redis: DependencyStatus = Depends(redis_status),
        storage: DependencyStatus = Depends(storage_status),
    ) -> ReadinessResponse:
        """Readiness: per-dependency status and an aggregated verdict. Answers 503 when a
        dependency is down so an external monitor alerts and the gateway stops routing.

        Kept off the uniform ``/health`` path so a dependency outage never fails the
        platform heartbeat other services share (Issue #65); the infra poller is pointed at
        this path for clinicq specifically, while services that only expose ``/health``
        keep working unchanged."""
        checks = DependencyChecks(
            database=database, migrations=migrations, redis=redis, storage=storage
        )
        overall = aggregate_status(database, migrations, redis, storage)
        if overall is HealthStatus.DOWN:
            response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status=overall, checks=checks, cert=probe_cert(cfg))

    return app


app = create_app()
