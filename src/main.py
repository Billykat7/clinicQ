"""ASGI entrypoint for the BK ClinicQ API (monolith; modules map to future services)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Response
from fastapi import status as http_status
from fastapi.staticfiles import StaticFiles

from src.api.v1.router import api_v1_router
from src.commons.enums import AppEnvironment, DependencyStatus, HealthStatus
from src.core.config import Settings, get_settings
from src.core.csrf_middleware import CsrfProtectMiddleware
from src.core.health import (
    aggregate_status,
    database_status,
    migrations_status,
    probe_cert,
    storage_status,
)
from src.core.logging_config import setup_logging
from src.core.request_logging import RequestLoggingMiddleware
from src.core.scheduler import shutdown_scheduler, start_scheduler
from src.core.security_headers import SecurityHeadersMiddleware
from src.schemas.health import DependencyChecks, LivenessResponse, ReadinessResponse
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
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(CsrfProtectMiddleware)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(web_router)
    app.include_router(api_v1_router, prefix="/api/v1")

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
            status=HealthStatus.OK, version=cfg.version, cert=probe_cert(cfg)
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
        storage: DependencyStatus = Depends(storage_status),
    ) -> ReadinessResponse:
        """Readiness: per-dependency status and an aggregated verdict. Answers 503 when a
        dependency is down so an external monitor alerts and the gateway stops routing.

        Kept off the uniform ``/health`` path so a dependency outage never fails the
        platform heartbeat other services share (Issue #65); the infra poller is pointed at
        this path for properties specifically, while services that only expose ``/health``
        keep working unchanged."""
        checks = DependencyChecks(
            database=database, migrations=migrations, storage=storage
        )
        overall = aggregate_status(database, migrations, storage)
        if overall is HealthStatus.DOWN:
            response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status=overall, checks=checks, cert=probe_cert(cfg))

    return app


app = create_app()
