"""Dependency probes behind the readiness health check (Issue #65).

``/health/ready`` (readiness) reports one status per dependency — database reachable,
migrations at head, S3 log storage writable — while ``/health`` and ``/health/live``
(liveness) only answer "the process is up". Splitting the two means a dependency outage
(e.g. a stopped database) fails readiness so an external monitor alerts and the
orchestrator stops routing, without failing liveness and triggering a pointless restart
loop. ``/health`` stays the uniform, dependency-free heartbeat every product exposes and
the infra gateway polls by default; readiness lives off that path so the shared heartbeat
never fails on a dependency blip.

Every probe is defensive: any failure is caught and mapped to a status, never raised,
and the returned status carries **no** connection string, hostname, driver message or
version — only ``ok`` / ``degraded`` / ``down`` / ``skipped``. Details go to the log,
not the response body.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.commons.enums import DependencyStatus, HealthStatus
from src.core.config import Settings, get_settings
from src.database.session import get_db
from src.schemas.health import CertInfo

logger = logging.getLogger(__name__)

# Project root (…/clinicq), two levels up from this file, used to locate the
# Alembic script directory regardless of the process working directory.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_DIR = _PROJECT_ROOT / "alembic"

# The storage probe writes a tiny sentinel to S3, so its result is cached briefly to
# bound write amplification: ``/health`` is public, and without this every poll (or a
# flood of them) would issue an S3 PutObject.
_STORAGE_CACHE_TTL_SECONDS = 60.0
_storage_cache_lock = threading.Lock()
_storage_cache: tuple[float, DependencyStatus] | None = None


def probe_database(db: Session) -> DependencyStatus:
    """Return ``OK`` when a trivial round-trip to the database succeeds, else ``DOWN``."""
    try:
        db.execute(text("SELECT 1"))
        return DependencyStatus.OK
    except Exception:
        logger.warning("Readiness: database probe failed", exc_info=True)
        return DependencyStatus.DOWN


@lru_cache(maxsize=1)
def _alembic_heads() -> frozenset[str]:
    """Return the head revision id(s) from the Alembic script directory.

    Cached for the process: the migration files are baked into the image and never
    change at runtime.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    script = ScriptDirectory.from_config(cfg)
    return frozenset(script.get_heads())


def probe_migrations(db: Session) -> DependencyStatus:
    """Return ``OK`` when the database is stamped at the latest migration, else ``DOWN``.

    A database whose ``alembic_version`` is behind head is running against a schema the
    code does not expect, so readiness must fail until ``alembic upgrade head`` is run.
    """
    try:
        from alembic.runtime.migration import MigrationContext

        heads = _alembic_heads()
        if not heads:
            logger.warning("Readiness: no Alembic head revision found")
            return DependencyStatus.DOWN
        context = MigrationContext.configure(
            db.connection(),
            opts={"version_table_schema": get_settings().db_schema.value},
        )
        current = context.get_current_revision()
        if current in heads:
            return DependencyStatus.OK
        # A plain behind/ahead mismatch is the common "migrations: down" cause, and it
        # was previously silent. Log which revision the app's schema is stamped at vs the
        # head(s) baked into the image so the gap is diagnosable without leaking it in the
        # response body. ``current`` is None when the ``alembic_version`` table is absent
        # from this schema (upgrade never ran here, or ran against a different schema/DB).
        logger.warning(
            "Readiness: migrations behind — schema %r stamped at %r, image head(s) %r",
            get_settings().db_schema.value,
            current,
            sorted(heads),
        )
        return DependencyStatus.DOWN
    except Exception:
        logger.warning("Readiness: migration probe failed", exc_info=True)
        return DependencyStatus.DOWN


def _probe_storage_uncached(cfg: Settings) -> DependencyStatus:
    """Verify S3 log storage is writable by putting a tiny sentinel object.

    ``head_bucket`` would only prove the bucket exists; the log pipeline needs *write*
    access, so the probe overwrites one fixed sentinel key (no accumulation) under the
    log prefix, where any log-retention lifecycle rule will sweep it.
    """
    from src.core.s3_logging import create_s3_probe_client

    client = create_s3_probe_client()
    if client is None:
        logger.warning("Readiness: S3 probe client unavailable")
        return DependencyStatus.DEGRADED
    try:
        key = f"{cfg.s3_environment}/logs/_readiness/probe.json"
        client.put_object(
            Bucket=(cfg.aws_s3_bucket or "").strip(),
            Key=key,
            Body=b'{"probe":"ok"}',
            ContentType="application/json",
        )
        return DependencyStatus.OK
    except Exception:
        logger.warning("Readiness: S3 storage probe failed", exc_info=True)
        return DependencyStatus.DEGRADED


def probe_storage(cfg: Settings) -> DependencyStatus:
    """Return the S3 log-storage status: ``SKIPPED`` when off, else a cached probe.

    S3 log shipping is optional, so a deployment with it disabled reports ``SKIPPED``
    (excluded from the readiness verdict). When enabled, a failed write is ``DEGRADED``
    rather than ``DOWN``: losing log shipping does not take the product offline.
    """
    if not cfg.aws_s3_logging_enabled or not (cfg.aws_s3_bucket or "").strip():
        return DependencyStatus.SKIPPED

    global _storage_cache
    now = time.monotonic()
    with _storage_cache_lock:
        if (
            _storage_cache is not None
            and now - _storage_cache[0] < _STORAGE_CACHE_TTL_SECONDS
        ):
            return _storage_cache[1]
    status = _probe_storage_uncached(cfg)
    with _storage_cache_lock:
        _storage_cache = (now, status)
    return status


# The cert probe reads and parses a PEM off disk. ``/health`` is public and polled
# often, so the parsed result is cached and only re-read when the file's mtime changes
# (a rotation) — bounding file IO without going stale across a cert renewal.
_cert_cache_lock = threading.Lock()
_cert_cache: tuple[str, float, CertInfo | None] | None = None


def _common_name(name) -> str | None:
    """Return the first Common Name (CN) attribute of an x509 name, or ``None``."""
    from cryptography.x509.oid import NameOID

    attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return attrs[0].value if attrs else None


def _load_cert_info(path: Path) -> CertInfo | None:
    """Parse the leaf certificate of a PEM file into ``CertInfo`` (``None`` on any error).

    A ``fullchain.pem`` holds the leaf first, so the first ``CERTIFICATE`` block is the one
    whose expiry matters. Fully defensive: a missing/unreadable/malformed file never raises
    (liveness must not fail because a cert path is misconfigured).
    """
    from cryptography.x509 import load_pem_x509_certificates

    try:
        certs = load_pem_x509_certificates(path.read_bytes())
    except Exception:
        logger.warning("Health: could not read TLS cert at %s", path, exc_info=True)
        return None
    if not certs:
        logger.warning("Health: no certificate found in %s", path)
        return None
    leaf = certs[0]
    not_after = leaf.not_valid_after_utc
    not_before = leaf.not_valid_before_utc
    days_remaining = (not_after - datetime.now(UTC)).days
    return CertInfo(
        expires_at=not_after,
        issued_at=not_before,
        days_remaining=days_remaining,
        subject=_common_name(leaf.subject),
        issuer=_common_name(leaf.issuer),
    )


def probe_cert(cfg: Settings) -> CertInfo | None:
    """Return the edge TLS certificate expiry to report in the health body, or ``None``.

    ``None`` (the ``cert`` block is omitted) when no ``TLS_CERT_PATH`` is configured or the
    file can't be parsed. Cached per (path, mtime) so the public heartbeat doesn't re-read
    the PEM on every poll while still picking up a rotation.
    """
    path = cfg.tls_cert_path
    if path is None:
        return None
    path = Path(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        logger.warning("Health: TLS cert path %s is not accessible", path)
        return None

    global _cert_cache
    key = str(path)
    with _cert_cache_lock:
        if (
            _cert_cache is not None
            and _cert_cache[0] == key
            and _cert_cache[1] == mtime
        ):
            return _cert_cache[2]
    info = _load_cert_info(path)
    with _cert_cache_lock:
        _cert_cache = (key, mtime, info)
    return info


def aggregate_status(
    database: DependencyStatus,
    migrations: DependencyStatus,
    storage: DependencyStatus,
) -> HealthStatus:
    """Fold per-dependency statuses into one readiness verdict.

    Any ``DOWN`` makes the whole readiness ``DOWN``; otherwise any ``DEGRADED`` makes it
    ``DEGRADED``; ``SKIPPED`` dependencies are ignored. Everything healthy is ``OK``.
    """
    values = (database, migrations, storage)
    if DependencyStatus.DOWN in values:
        return HealthStatus.DOWN
    if DependencyStatus.DEGRADED in values:
        return HealthStatus.DEGRADED
    return HealthStatus.OK


# --- FastAPI dependency wrappers (overridable in tests) -----------------------------


def database_status(db: Session = Depends(get_db)) -> DependencyStatus:
    """Readiness dependency: the database probe result."""
    return probe_database(db)


def migrations_status(db: Session = Depends(get_db)) -> DependencyStatus:
    """Readiness dependency: the migrations-at-head probe result."""
    return probe_migrations(db)


def storage_status(cfg: Settings = Depends(get_settings)) -> DependencyStatus:
    """Readiness dependency: the S3 log-storage probe result."""
    return probe_storage(cfg)


def cert_info(cfg: Settings = Depends(get_settings)) -> CertInfo | None:
    """Health dependency: the edge TLS cert expiry, or ``None`` when unconfigured."""
    return probe_cert(cfg)
