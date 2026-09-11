"""Health check schemas: liveness and per-dependency readiness (Issue #65)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from src.commons.enums import DependencyStatus, HealthStatus


class CertInfo(BaseModel):
    """Edge TLS certificate expiry, reported in the health body for the infra gateway.

    The gateway can't read the edge PEMs itself (only its nginx mounts them), so a service
    that is given its ``fullchain.pem`` reports the expiry here and the poller stores it
    (infra alembic 0012). ``expires_at`` is the field the poller reads (it also accepts
    ``not_after``); the rest is extra context for the dashboard. All times are UTC.
    """

    expires_at: datetime = Field(
        description="Certificate notAfter (ISO-8601, UTC). The value the infra poller reads."
    )
    issued_at: datetime | None = Field(
        default=None, description="Certificate notBefore (ISO-8601, UTC)."
    )
    days_remaining: int = Field(
        description="Whole days until expiry (negative once expired)."
    )
    subject: str | None = Field(
        default=None, description="Subject common name (CN), when present."
    )
    issuer: str | None = Field(
        default=None, description="Issuer common name (CN), when present."
    )


class LivenessResponse(BaseModel):
    """``/health`` and ``/health/live`` — the process answered. No dependencies are touched."""

    status: HealthStatus = Field(description="Liveness status for the API process.")
    version: str = Field(description="Application version string.")
    git_sha: str = Field(
        description="The commit this build came from (`unknown` outside a release image)."
    )
    cert: CertInfo | None = Field(
        default=None,
        description="Edge TLS certificate expiry, when a cert path is configured; else omitted.",
    )


class DependencyChecks(BaseModel):
    """Per-dependency readiness results.

    Each value is a bare status (``ok`` / ``degraded`` / ``down`` / ``skipped``) — never
    a connection string, hostname, driver message or dependency version.
    """

    database: DependencyStatus = Field(description="PostgreSQL round-trip status.")
    migrations: DependencyStatus = Field(
        description="Whether the database is stamped at the latest Alembic revision."
    )
    redis: DependencyStatus = Field(
        description="Redis PING status (skipped when REDIS_URL is not configured)."
    )
    storage: DependencyStatus = Field(
        description="S3 log-storage write status (skipped when S3 logging is off)."
    )


class ReadinessResponse(BaseModel):
    """``/health/ready`` — the aggregated readiness verdict plus each dependency's status."""

    status: HealthStatus = Field(description="Aggregated readiness status.")
    checks: DependencyChecks = Field(description="Per-dependency readiness results.")
    cert: CertInfo | None = Field(
        default=None,
        description="Edge TLS certificate expiry, when a cert path is configured; else omitted.",
    )
