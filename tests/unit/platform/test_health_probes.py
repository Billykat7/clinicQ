"""Unit tests for the readiness dependency probes (Issue #65).

These cover the pure logic and failure mapping without a live PostgreSQL or S3 — the
happy-path wiring is exercised end-to-end in `tests/integration/test_health.py`.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import src.core.health as health
from src.commons.enums import DependencyStatus, HealthStatus
from src.core.config import Settings
from src.core.health import (
    aggregate_status,
    probe_cert,
    probe_database,
    probe_migrations,
    probe_storage,
)

#: The deployment hostname the readiness probe reports off the edge certificate.
_CERT_CN = "clinicq.bkatalayi.com"
#: The common name a self-signed fixture certificate carries when a test does not pick one.
_DEFAULT_CN = "clinicq.test"


def _write_self_signed(path: Path, *, days_valid: int, cn: str = _DEFAULT_CN) -> None:
    """Write a throwaway self-signed PEM at ``path`` expiring ``days_valid`` from now."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
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


class _RaisingSession:
    """Stand-in Session whose every DB touch raises, to drive the DOWN paths."""

    def execute(self, *_args: object, **_kwargs: object) -> object:
        raise RuntimeError("database unreachable")

    def connection(self) -> object:
        raise RuntimeError("database unreachable")


def test_probe_database_ok_on_successful_roundtrip() -> None:
    """A session that answers `SELECT 1` reports the database as OK."""

    class _OkSession:
        def execute(self, *_args: object, **_kwargs: object) -> None:
            return None

    assert probe_database(_OkSession()) is DependencyStatus.OK  # type: ignore[arg-type]


def test_probe_database_down_when_query_raises() -> None:
    """A failed round-trip maps to DOWN, not an exception."""
    assert probe_database(_RaisingSession()) is DependencyStatus.DOWN  # type: ignore[arg-type]


def test_probe_migrations_down_when_connection_fails() -> None:
    """An unreadable `alembic_version` (here: no connection) maps to DOWN."""
    assert probe_migrations(_RaisingSession()) is DependencyStatus.DOWN  # type: ignore[arg-type]


def test_probe_storage_skipped_when_logging_disabled() -> None:
    """S3 log shipping off → SKIPPED, and no S3 client is ever built."""
    cfg = Settings(_env_file=None, aws_s3_logging_enabled=False, aws_s3_bucket="")

    assert probe_storage(cfg) is DependencyStatus.SKIPPED


def test_probe_storage_skipped_when_bucket_missing() -> None:
    """Enabled but no bucket configured is still SKIPPED (nothing to write to)."""
    cfg = Settings(_env_file=None, aws_s3_logging_enabled=True, aws_s3_bucket="")

    assert probe_storage(cfg) is DependencyStatus.SKIPPED


def test_probe_storage_degraded_when_client_unavailable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Enabled with a bucket but no usable client → DEGRADED (product still serves)."""
    health._storage_cache = None  # reset the module-level TTL cache
    monkeypatch.setattr(
        "src.core.s3_logging.create_s3_probe_client", lambda: None, raising=True
    )
    cfg = Settings(
        _env_file=None, aws_s3_logging_enabled=True, aws_s3_bucket="some-bucket"
    )

    assert probe_storage(cfg) is DependencyStatus.DEGRADED
    health._storage_cache = None


def test_probe_cert_none_when_path_unset() -> None:
    """No `TLS_CERT_PATH` configured → no cert block (None), never an error."""
    cfg = Settings(_env_file=None)

    assert probe_cert(cfg) is None


def test_probe_cert_none_when_file_missing(tmp_path: Path) -> None:
    """A configured-but-missing cert file maps to None (liveness must not fail)."""
    health._cert_cache = None
    cfg = Settings(_env_file=None, TLS_CERT_PATH=str(tmp_path / "does-not-exist.pem"))

    assert probe_cert(cfg) is None
    health._cert_cache = None


def test_probe_cert_none_when_file_unparseable(tmp_path: Path) -> None:
    """Garbage in the PEM path is swallowed to None, not raised."""
    health._cert_cache = None
    bad = tmp_path / "fullchain.pem"
    bad.write_text("not a certificate")
    cfg = Settings(_env_file=None, TLS_CERT_PATH=str(bad))

    assert probe_cert(cfg) is None
    health._cert_cache = None


def test_probe_cert_reports_expiry_and_subject(tmp_path: Path) -> None:
    """A valid leaf cert yields expiry, positive days_remaining, and its CN."""
    health._cert_cache = None
    pem = tmp_path / "fullchain.pem"
    _write_self_signed(pem, days_valid=30, cn=_CERT_CN)
    cfg = Settings(_env_file=None, TLS_CERT_PATH=str(pem))

    info = probe_cert(cfg)

    assert info is not None
    assert info.subject == _CERT_CN
    assert info.expires_at.tzinfo is not None  # tz-aware UTC for a clean ISO-8601 "…Z"
    assert 28 <= info.days_remaining <= 30
    # Serializes exactly as the infra poller parses it: cert.expires_at, ISO-8601.
    assert info.model_dump(mode="json")["expires_at"].endswith("Z")
    health._cert_cache = None


def test_aggregate_status_truth_table() -> None:
    """Any DOWN → DOWN; else any DEGRADED → DEGRADED; SKIPPED ignored; else OK."""
    ok, down, degraded, skipped = (
        DependencyStatus.OK,
        DependencyStatus.DOWN,
        DependencyStatus.DEGRADED,
        DependencyStatus.SKIPPED,
    )

    assert aggregate_status(ok, ok, skipped) is HealthStatus.OK
    assert aggregate_status(ok, ok, ok) is HealthStatus.OK
    assert aggregate_status(down, ok, ok) is HealthStatus.DOWN
    assert aggregate_status(ok, down, degraded) is HealthStatus.DOWN
    assert aggregate_status(ok, ok, degraded) is HealthStatus.DEGRADED
    assert aggregate_status(skipped, skipped, skipped) is HealthStatus.OK
