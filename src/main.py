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
from src.core.logging_config import setup_logging
from src.core.request_logging import RequestLoggingMiddleware
from src.core.scheduler import shutdown_scheduler, start_scheduler
from src.core.security_headers import SecurityHeadersMiddleware
from src.schemas.health import DependencyChecks, LivenessResponse, ReadinessResponse
from src.web.dev import router as dev_router
from src.web.routes import router as web_router

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(settings_obj: Settings | None = None) -> FastAPI:
    cfg = settings_obj if settings_obj is not None else get_settings()
    setup_logging()
    is_prod = cfg.environment == AppEnvironment.PRODUCTION

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Start background jobs on boot (Issue #38) and stop them on shutdown."""
        start_scheduler(cfg)
        try:
            yield
        finally:
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
    # One error envelope for every API error (Issue 4): domain errors, HTTPException, validation.
    install_error_handlers(app)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(web_router)
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
