"""Liveness and readiness probes (Issue #65).

Liveness is served at both `/health` and `/health/live`: `/health` is the uniform
platform heartbeat every product exposes and the infra gateway polls by default, so it
stays dependency-free; `/health/live` is the same probe under the name the container
`HEALTHCHECK` uses. It is the release gate's minimum too — CI `test` must collect at least
one test, otherwise pytest exits 5, `Release (GHCR)` never runs, and a pushed tag produces
no deployable image (Issue 13). It touches no dependency, so it is green without a database.

`/health/ready` is readiness — per-dependency status with a `503` when a dependency is
down. The dependency probes are exercised here through FastAPI dependency overrides so the
suite needs no live PostgreSQL or S3 (the probes themselves are unit-tested against fakes
in `tests/unit/test_health_probes.py`).
"""

from __future__ import annotations

import datetime
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.core.health as health
from src.commons.enums import DependencyStatus, HealthStatus
from src.core.config import Settings
from src.core.health import (
    database_status,
    migrations_status,
    redis_status,
    storage_status,
)
from src.main import create_app

#: The deployment hostname the readiness probe reports off the edge certificate.
_CERT_CN = "clinicq.bkatalayi.com"


@pytest.fixture
def app_client() -> Iterator[tuple[TestClient, object]]:
    """A client plus its app, so tests can install dependency overrides on that app."""
    app = create_app()
    yield TestClient(app), app
    app.dependency_overrides.clear()


def _override(app: object, **statuses: DependencyStatus) -> None:
    """Pin each readiness dependency to a fixed status for the test.

    Redis is pinned to ``skipped`` unless a test names it, so an outcome never depends on
    whether the developer's ``.env`` points ``REDIS_URL`` at a running Redis.
    """
    statuses.setdefault("redis", DependencyStatus.SKIPPED)
    mapping = {
        "database": database_status,
        "migrations": migrations_status,
        "redis": redis_status,
        "storage": storage_status,
    }
    for name, status in statuses.items():
        app.dependency_overrides[mapping[name]] = lambda status=status: status  # type: ignore[attr-defined]


@pytest.mark.parametrize("path", ["/health", "/health/live"])
def test_liveness_returns_ok(client: TestClient, path: str) -> None:
    """`GET /health` and `/health/live` answer 200 with the liveness status and version.

    Both must answer without touching a dependency: `/health` is the uniform heartbeat the
    infra poller hits for every service, `/health/live` the container HEALTHCHECK alias.
    """
    response = client.get(path)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == HealthStatus.OK.value
    # Version is whatever the running settings report; assert it is populated, not a
    # literal, so a version bump does not break the release gate.
    assert body["version"]
    assert body["git_sha"]


def test_liveness_reports_the_version_and_commit_the_image_was_built_with() -> None:
    """A release image sets VERSION and GIT_SHA (Issue 10); /health is where they are read back."""
    sha = "0123456789abcdef0123456789abcdef01234567"
    # VERSION is the alias the image sets; the field name is not accepted as a keyword.
    settings = Settings(_env_file=None, VERSION="0.2.0", git_sha=sha)  # type: ignore[call-arg]
    body = TestClient(create_app(settings)).get("/health").json()
    assert (body["version"], body["git_sha"]) == ("0.2.0", sha)


def test_readiness_all_healthy_returns_ok(
    app_client: tuple[TestClient, object],
) -> None:
    """All dependencies healthy → 200, aggregated `ok`, per-dependency statuses present."""
    client, app = app_client
    _override(
        app,
        database=DependencyStatus.OK,
        migrations=DependencyStatus.OK,
        storage=DependencyStatus.SKIPPED,
    )

    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == HealthStatus.OK.value
    assert body["checks"] == {
        "database": DependencyStatus.OK.value,
        "migrations": DependencyStatus.OK.value,
        "redis": DependencyStatus.SKIPPED.value,
        "storage": DependencyStatus.SKIPPED.value,
    }


def test_stopped_database_fails_readiness_but_not_liveness(
    app_client: tuple[TestClient, object],
) -> None:
    """A down database → readiness 503 (`database: down`) while liveness stays 200."""
    client, app = app_client
    _override(
        app,
        database=DependencyStatus.DOWN,
        migrations=DependencyStatus.OK,
        storage=DependencyStatus.SKIPPED,
    )

    readiness = client.get("/health/ready")
    assert readiness.status_code == 503
    body = readiness.json()
    assert body["status"] == HealthStatus.DOWN.value
    assert body["checks"]["database"] == DependencyStatus.DOWN.value

    # Liveness is unaffected: the process is up.
    assert client.get("/health/live").status_code == 200


def test_migrations_behind_head_fails_readiness(
    app_client: tuple[TestClient, object],
) -> None:
    """A database behind head → readiness 503 (`migrations: down`)."""
    client, app = app_client
    _override(
        app,
        database=DependencyStatus.OK,
        migrations=DependencyStatus.DOWN,
        storage=DependencyStatus.SKIPPED,
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == HealthStatus.DOWN.value


def test_unwritable_storage_is_degraded_not_down(
    app_client: tuple[TestClient, object],
) -> None:
    """S3 not writable → `degraded` with HTTP 200: the product still serves."""
    client, app = app_client
    _override(
        app,
        database=DependencyStatus.OK,
        migrations=DependencyStatus.OK,
        storage=DependencyStatus.DEGRADED,
    )

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == HealthStatus.DEGRADED.value


def test_readiness_does_not_leak_internals(
    app_client: tuple[TestClient, object],
) -> None:
    """The readiness body exposes only status + bare per-dependency statuses.

    No version, hostname, connection string or driver detail — the acceptance criterion
    that `/health` never leaks internals.
    """
    client, app = app_client
    _override(
        app,
        database=DependencyStatus.OK,
        migrations=DependencyStatus.OK,
        storage=DependencyStatus.OK,
    )

    body = client.get("/health/ready").json()

    assert set(body) == {"status", "checks"}
    assert set(body["checks"]) == {"database", "migrations", "redis", "storage"}
    allowed = {s.value for s in DependencyStatus}
    assert all(value in allowed for value in body["checks"].values())


def _self_signed_pem(path: Path, *, days_valid: int = 30) -> None:
    """Write a throwaway self-signed cert so the health body has a cert to report."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _CERT_CN)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=days_valid))
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


@pytest.mark.parametrize("path", ["/health", "/health/live", "/health/ready"])
def test_cert_block_reported_when_cert_path_configured(
    tmp_path: Path, path: str
) -> None:
    """With `TLS_CERT_PATH` set, every health body carries the `cert` block the infra
    gateway reads (`cert.expires_at`, ISO-8601). Configured on all three so liveness — the
    body the gateway can reach without a working readiness — reports it too."""
    health._cert_cache = None
    pem = tmp_path / "fullchain.pem"
    _self_signed_pem(pem)
    app = create_app(Settings(_env_file=None, TLS_CERT_PATH=str(pem)))

    body = TestClient(app).get(path).json()

    assert "cert" in body, f"{path} should include the cert block"
    assert body["cert"]["expires_at"].endswith("Z")
    assert body["cert"]["subject"] == _CERT_CN
    health._cert_cache = None
