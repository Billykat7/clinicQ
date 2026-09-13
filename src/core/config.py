"""Application settings (mirrors the Dikima ``src/core/config`` pattern)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, ClassVar, NamedTuple

from pydantic import AliasChoices, Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import ArgumentError

from src.commons.enums import (
    AppEnvironment,
    DbSchema,
    DocumentScannerKind,
    EsignProviderKind,
    GeocodingProvider,
    LogFormat,
    LogLevel,
    RateLimitBackendKind,
    S3LogPath,
    SmsProviderKind,
)

# Default JWT secret shipped for local development only. The production guard
# below refuses to boot outside development when this value is still in use.
_DEFAULT_JWT_SECRET = "clinicq-dev-secret-change-me-min-32-chars"

#: The CORS origin that admits every site. Development only (Issue 12).
CORS_ANY_ORIGIN = "*"

#: The prefix Stripe and Paystack give a secret key that moves real money.
_LIVE_SECRET_KEY_PREFIX = "sk_live_"

#: The lowest bcrypt cost allowed outside development.
_MIN_BCRYPT_ROUNDS_OUTSIDE_DEVELOPMENT = 12

#: A DATABASE_URL still holding ``${...}`` placeholders, to be built from the DB_* parts.
_URL_PLACEHOLDER = "${"


class ConfigProblem(NamedTuple):
    """One value the app must not run with: the setting (its env var) and why, never the value."""

    setting: str
    message: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # A refused boot must not print what it refused: the input holds the secrets (Issue 12).
        hide_input_in_errors=True,
    )

    app_name: str = Field(
        default="BK ClinicQ", description="Application name (env: APP_NAME)"
    )
    version: str = Field(
        default="0.1.0",
        validation_alias=AliasChoices("VERSION", "APP_VERSION"),
        description=(
            "Application version, reported at /health (env: VERSION; APP_VERSION accepted as "
            "alias). The release image sets it from the tag (Issue 10); leave it out of env files."
        ),
    )
    git_sha: str = Field(
        default="unknown",
        description=(
            "The commit the running code was built from, reported at /health (env: GIT_SHA). Set "
            "by the image build (Issue 10); never set it in an env file, or it would mislabel "
            "the image it runs in."
        ),
    )
    environment: AppEnvironment = Field(
        default=AppEnvironment.DEVELOPMENT,
        description="Runtime environment (env: ENVIRONMENT)",
    )
    debug: bool = Field(
        default=False,
        description=(
            "FastAPI debug mode: tracebacks in error responses (env: DEBUG). Development only; "
            "the app refuses to start with it in staging or production."
        ),
    )
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description=(
            "Origins allowed to call the app from a page on another origin, comma-separated, e.g. "
            "'https://clinicq.example.org' (env: CORS_ORIGINS). Empty: same-origin only, no CORS "
            "headers at all. '*' admits every site and is refused outside development."
        ),
    )
    log_level: LogLevel = Field(
        default=LogLevel.INFO,
        description="Minimum level the console writes (env: LOG_LEVEL). INFO unless a deployment says otherwise.",
    )
    log_format: LogFormat = Field(
        default=LogFormat.JSON,
        description="Console log format: json (one object per line) or text (env: LOG_FORMAT).",
    )

    # Monitoring (Issue 14). Both are opt-in: error tracking is off without a DSN, /metrics without
    # METRICS_ENABLED, and outside development /metrics also needs a token, or it would tell anyone
    # the app's traffic.
    sentry_dsn: str = Field(
        default="",
        description=(
            "Where unhandled exceptions are sent: a Sentry or GlitchTip project DSN (env: "
            "SENTRY_DSN). Empty: error tracking is off."
        ),
    )
    sentry_traces_sample_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Share of requests sent as performance traces, 0 to 1 (env: "
            "SENTRY_TRACES_SAMPLE_RATE). 0 sends errors only, which fits the free tiers."
        ),
    )
    metrics_enabled: bool = Field(
        default=False,
        description=(
            "Serve Prometheus metrics (request rate, latency, errors) at /metrics "
            "(env: METRICS_ENABLED)."
        ),
    )
    metrics_token: str = Field(
        default="",
        description=(
            "Bearer token /metrics requires (env: METRICS_TOKEN). Required outside development "
            "while metrics are on: traffic figures are not for everyone."
        ),
    )
    error_tracking_test_route: bool = Field(
        default=False,
        description=(
            "Serve GET /health/error-tracking-test, which raises on purpose, to prove error "
            "tracking works end to end (env: ERROR_TRACKING_TEST_ROUTE). Refused in production."
        ),
    )

    # Edge TLS certificate whose expiry is reported in the health body so the infra
    # gateway can surface it (the gateway can't read the edge PEMs — only its nginx
    # mounts them, see infra alembic 0012). In production this is set by the compose file
    # to ``/etc/edge-cert/fullchain.pem`` — a read-only mount of the same public chain the
    # edge nginx renders as ``ssl_certificate``
    # (``<gateway>/infra/nginx/certs/<DOMAIN>/fullchain.pem``). Unset → the health body
    # simply omits the ``cert`` block; nothing else changes.
    tls_cert_path: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("TLS_CERT_PATH", "CERT_PATH"),
        description="PEM cert whose expiry is reported in /health (env: TLS_CERT_PATH; CERT_PATH accepted as alias). Unset → no cert block.",
    )

    # S3 structured logging (audit/troubleshooting). Console is always active; the
    # S3 handler is only attached when AWS_S3_LOGGING_ENABLED=true and a bucket is set.
    aws_s3_logging_enabled: bool = Field(
        default=False,
        description="Upload structured logs to S3 (env: AWS_S3_LOGGING_ENABLED)",
    )

    aws_s3_bucket: str = Field(
        default="",
        description="S3 bucket for structured logs (env: AWS_S3_BUCKET)",
    )

    aws_s3_region: str = Field(
        default="af-south-1",
        validation_alias=AliasChoices("AWS_S3_REGION", "AWS_REGION"),
        description="AWS region for S3 (env: AWS_S3_REGION; AWS_REGION accepted as alias)",
    )

    aws_s3_log_path: S3LogPath = Field(
        default=S3LogPath.API,
        description="Log path segment for the S3 key: api or web (env: AWS_S3_LOG_PATH)",
    )

    aws_s3_log_level: LogLevel = Field(
        default=LogLevel.WARNING,
        description="Minimum level uploaded to S3 (env: AWS_S3_LOG_LEVEL)",
    )

    # Explicit credentials from .env; when set, boto3 uses these instead of its default
    # chain (SSO/profile/instance role).
    aws_access_key_id: str = Field(
        default="",
        description="AWS access key ID (env: AWS_ACCESS_KEY_ID)",
    )

    aws_secret_access_key: str = Field(
        default="",
        description="AWS secret access key (env: AWS_SECRET_ACCESS_KEY)",
    )

    # SSL verification for AWS/S3. Set false in DEV behind a proxy or with self-signed certs.
    aws_ssl_cert_enabled: bool = Field(
        default=True,
        description="Verify SSL certificates for AWS/S3 (env: AWS_SSL_CERT_ENABLED)",
    )
    # Honor X-Forwarded-For when resolving the client IP (rate-limit keys, the session audit
    # trail and request logs all read one resolver — src.core.client_ip). Off by default and
    # deliberately so: with no proxy in front, the whole header is client-supplied, and defaulting
    # this on would turn an attacker-controlled string into an authorization input for every such
    # deployment (Issue #179).
    trust_proxy_headers: bool = Field(
        default=False,
        description="Trust X-Forwarded-For for client IP (env: TRUST_PROXY_HEADERS)",
    )
    # How many trusted proxies sit in front of the app. X-Forwarded-For is append-only and
    # client-supplied at the *left*: our nginx uses $proxy_add_x_forwarded_for, which appends the
    # address it actually saw, so the trustworthy element is the Nth from the *right*. One hop
    # (nginx facing the internet) is the shipped topology; raise it to 2 if a trusted CDN sits in
    # front of nginx. Only read when ``trust_proxy_headers`` is on.
    trusted_proxy_hops: int = Field(
        default=1,
        ge=1,
        le=8,
        description="Trusted reverse-proxy hops in front of the app (env: TRUSTED_PROXY_HOPS)",
    )

    # Database (PostgreSQL/PostGIS). Set DATABASE_URL, or DB_USER/DB_PASSWORD/DB_HOST/DB_NAME.
    database_url: str = Field(
        default="postgresql://localhost/btk",
        description="PostgreSQL URL (env: DATABASE_URL), or built from DB_USER/DB_PASSWORD/DB_HOST/DB_NAME",
    )
    db_user: str = Field(
        default="", description="DB user (env: DB_USER). Used to build DATABASE_URL."
    )
    db_password: str = Field(
        default="",
        description="DB password (env: DB_PASSWORD). Used to build DATABASE_URL.",
    )
    db_host: str = Field(
        default="",
        description="DB host derived from DATABASE_URL (DB_HOST env is ignored when URL is set)",
    )
    db_name: str = Field(
        default="",
        description="DB name derived from DATABASE_URL (DB_NAME env is ignored when URL is set)",
    )
    db_opts: str = Field(
        default="", description="Optional query string for DB URL (env: DB_OPTS)."
    )
    db_schema: DbSchema = Field(
        default=DbSchema.CLINICQ,
        description="PostgreSQL schema for application tables (env: DB_SCHEMA)",
    )

    # Async request-path database (Issue #81). The request path runs on the asyncio event loop,
    # so it uses an async SQLAlchemy engine over asyncpg driven by ``database_url_async`` below —
    # the same host/database as ``database_url`` with the driver swapped. The **synchronous**
    # ``database_url`` engine stays the one Alembic, cron, the CLI and scripts use unchanged. The
    # pool and timeout knobs are the async pool's, set deliberately rather than left to defaults so
    # a slow or contended database fails fast instead of pinning the loop (see docs/CICD/ASYNC-DB.md).
    db_async_driver: str = Field(
        default="asyncpg",
        description=(
            "SQLAlchemy async driver for the request-path engine, used to build "
            "database_url_async from database_url (env: DB_ASYNC_DRIVER)."
        ),
    )
    db_async_pool_size: int = Field(
        default=5,
        ge=1,
        description="Async engine connection-pool size (env: DB_ASYNC_POOL_SIZE).",
    )
    db_async_max_overflow: int = Field(
        default=10,
        ge=0,
        description=(
            "Async engine connections allowed beyond the pool size under burst "
            "(env: DB_ASYNC_MAX_OVERFLOW)."
        ),
    )
    db_pool_timeout_seconds: int = Field(
        default=30,
        ge=1,
        description=(
            "Seconds a request waits for a free async connection before failing rather than "
            "queueing forever (env: DB_POOL_TIMEOUT_SECONDS)."
        ),
    )
    db_statement_timeout_seconds: int = Field(
        default=30,
        ge=0,
        description=(
            "PostgreSQL statement_timeout applied to async connections; a runaway query is "
            "cancelled instead of holding a loop worker. 0 disables "
            "(env: DB_STATEMENT_TIMEOUT_SECONDS)."
        ),
    )
    db_lock_timeout_seconds: int = Field(
        default=10,
        ge=0,
        description=(
            "PostgreSQL lock_timeout applied to async connections; a blocked lock acquisition "
            "fails fast rather than stalling. 0 disables (env: DB_LOCK_TIMEOUT_SECONDS)."
        ),
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def normalise_log_level(cls, value: object) -> object:
        """Accept ``info`` as well as ``INFO``: the level was a free string before it was an enum."""
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_cors_origins(cls, value: object) -> object:
        """Read ``a,b`` (how an env file writes a list) or a JSON array; drop blanks and a trailing /."""
        if isinstance(value, str):
            text = value.strip()
            items = json.loads(text) if text.startswith("[") else text.split(",")
            return [
                str(item).strip().rstrip("/") for item in items if str(item).strip()
            ]
        return value

    @field_validator("database_url")
    @classmethod
    def database_url_parses(cls, value: str) -> str:
        """A malformed DATABASE_URL is a validation error naming the setting, not a crash later.

        A URL still holding ``${...}`` placeholders is built from the DB_* parts below, so it is
        checked after that. The message never repeats the URL: it can carry a password.
        """
        if _URL_PLACEHOLDER not in value:
            try:
                make_url(value)
            except ArgumentError as exc:
                raise ValueError("DATABASE_URL is not a valid database URL") from exc
        return value

    # The DB_* parts that disagreed with an explicit DATABASE_URL and were therefore ignored.
    # Recorded by the validator below, read by configuration_problems().
    _ignored_database_parts: tuple[str, ...] = PrivateAttr(default=())

    @model_validator(mode="after")
    def build_database_url_from_components(self) -> Settings:
        """Build database_url from DB_USER, DB_PASSWORD, DB_HOST, DB_NAME and DB_OPTS.

        Only when DB_HOST and DB_NAME are set and DATABASE_URL was not given as a literal URL: it
        was left unset, or it still holds ``${...}`` placeholders. Before Issue 12 an unset
        DATABASE_URL still won with its default, so DB_HOST/DB_NAME alone were silently ignored
        and the app connected to localhost. When both are given, DATABASE_URL wins, and parts
        that disagree with it are recorded as a configuration problem.
        """
        explicit_literal_url = (
            "database_url" in self.model_fields_set
            and _URL_PLACEHOLDER not in self.database_url
        )
        if explicit_literal_url:
            url = make_url(self.database_url)
            self._ignored_database_parts = tuple(
                name
                for name, given, actual in (
                    ("DB_HOST", self.db_host, url.host or ""),
                    ("DB_NAME", self.db_name, url.database or ""),
                )
                if name.lower() in self.model_fields_set and given != actual
            )
            return self
        if not self.db_host or not self.db_name:
            return self
        from urllib.parse import quote_plus

        user = quote_plus(self.db_user) if self.db_user else ""
        password = quote_plus(self.db_password) if self.db_password else ""
        auth = f"{user}:{password}@" if user or password else ""
        opts = (self.db_opts or "").strip()
        if opts and not opts.startswith("?"):
            opts = "?" + opts
        self.database_url = f"postgresql://{auth}{self.db_host}/{self.db_name}{opts}"
        return self

    @model_validator(mode="after")
    def sync_db_parts_from_database_url(self) -> Settings:
        """Align db_host/db_name with DATABASE_URL so tooling never shows stale DB_* parts."""
        u = make_url(self.database_url)
        self.db_host = u.host or ""
        self.db_name = u.database or ""
        return self

    # Auth / security core
    auth_enabled: bool = Field(
        default=True,
        description="Enable authentication dependencies (env: AUTH_ENABLED)",
    )
    jwt_secret: str = Field(
        default=_DEFAULT_JWT_SECRET,
        min_length=32,
        description="HS256 signing secret (env: JWT_SECRET)",
    )
    jwt_algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm (env: JWT_ALGORITHM)",
    )
    bcrypt_rounds: int = Field(
        default=12,
        ge=4,
        le=15,
        description=(
            "bcrypt cost factor for password hashing (env: BCRYPT_ROUNDS). The default 12 is a "
            "deliberate work factor; it may be lowered (e.g. 4) only in development/tests to keep "
            "bcrypt-heavy suites fast — a production guard forbids < 12 outside development."
        ),
    )
    field_encryption_key: str = Field(
        default="",
        description=(
            "Secret used to derive the Fernet key that encrypts personal fields at rest "
            "(e.g. a government ID number). When empty, the key is derived from JWT_SECRET so "
            "development and tests need no extra config (env: FIELD_ENCRYPTION_KEY)."
        ),
    )
    field_encryption_keys: str = Field(
        default="",
        description=(
            "Comma-separated field-encryption secrets, newest first, for zero-downtime key "
            "rotation (Issue #78). When set it fully specifies the key list: new data is "
            "encrypted with the first (primary) key, and every listed key is tried on decrypt "
            "so ciphertext under a retired key still reads during migration. When empty the "
            "app falls back to the single FIELD_ENCRYPTION_KEY / JWT_SECRET key "
            "(env: FIELD_ENCRYPTION_KEYS). See docs/CICD/KEY-ROTATION.md."
        ),
    )
    jwt_access_expire_minutes: int = Field(
        default=15,
        ge=1,
        description="Access token lifetime in minutes (env: JWT_ACCESS_EXPIRE_MINUTES)",
    )
    refresh_token_expire_days: int = Field(
        default=7,
        ge=1,
        description="Refresh token lifetime in days (env: REFRESH_TOKEN_EXPIRE_DAYS)",
    )
    refresh_reuse_grace_seconds: int = Field(
        default=5,
        ge=0,
        le=30,
        description=(
            "How long after a refresh token is rotated a second presentation of it is treated as "
            "a concurrent refresh (two tabs whose requests crossed) rather than a replay: it gets "
            "a fresh access token and no new refresh token, and revokes nothing. After the window "
            "a replay revokes the whole token family. 0 turns the window off "
            "(env: REFRESH_REUSE_GRACE_SECONDS)."
        ),
    )

    # Typed-token link lifetimes (activation / password reset emails)
    activation_link_expire_hours: int = Field(
        default=48,
        ge=1,
        description="Activation link validity in hours (env: ACTIVATION_LINK_EXPIRE_HOURS)",
    )
    password_reset_link_expire_hours: int = Field(
        default=2,
        ge=1,
        description="Password reset link validity in hours (env: PASSWORD_RESET_LINK_EXPIRE_HOURS)",
    )
    staff_invite_expire_hours: int = Field(
        default=72,
        ge=1,
        description=(
            "Staff invitation validity in hours — long enough to survive a weekend, short enough "
            "that a forwarded link is not a standing way in (Issue 22; env: "
            "STAFF_INVITE_EXPIRE_HOURS)"
        ),
    )
    email_change_link_expire_hours: int = Field(
        default=2,
        ge=1,
        description=(
            "Email-change confirmation link validity in hours "
            "(env: EMAIL_CHANGE_LINK_EXPIRE_HOURS)"
        ),
    )
    verification_resend_cooldown_minutes: int = Field(
        default=5,
        ge=1,
        description=(
            "Minimum minutes between verification-email resends per user "
            "(env: VERIFICATION_RESEND_COOLDOWN_MINUTES)"
        ),
    )

    # Signup / self-registration (feature flag)
    signup_enabled: bool = Field(
        default=False,
        description="Enable self-registration signup + activation (env: SIGNUP_ENABLED)",
    )

    # Sign-in method feature flags
    auth_otp_login_enabled: bool = Field(
        default=True,
        description="Enable OTP email sign-in endpoints (env: AUTH_OTP_LOGIN_ENABLED)",
    )
    auth_password_login_enabled: bool = Field(
        default=True,
        description="Enable password sign-in endpoint (env: AUTH_PASSWORD_LOGIN_ENABLED)",
    )

    # OTP (email sign-in). Codes live in a short-TTL in-memory store, rate-limited.
    otp_ttl_minutes: int = Field(
        default=10,
        ge=1,
        description="OTP validity in minutes (env: OTP_TTL_MINUTES)",
    )
    otp_length: int = Field(
        default=6,
        ge=4,
        le=8,
        description="OTP code length in digits (env: OTP_LENGTH)",
    )
    otp_rate_limit_per_email: int = Field(
        default=5,
        ge=1,
        description="Max OTP requests per email per window (env: OTP_RATE_LIMIT_PER_EMAIL)",
    )
    otp_rate_limit_per_ip: int = Field(
        default=20,
        ge=1,
        description="Max OTP requests per IP per window (env: OTP_RATE_LIMIT_PER_IP)",
    )
    otp_rate_limit_window_minutes: int = Field(
        default=15,
        ge=1,
        description="OTP rate-limit window in minutes (env: OTP_RATE_LIMIT_WINDOW_MINUTES)",
    )
    otp_rate_limit_per_phone: int = Field(
        default=5,
        ge=1,
        description=(
            "Max patient OTP requests per phone number per window, so one number cannot be "
            "flooded with texts (env: OTP_RATE_LIMIT_PER_PHONE)"
        ),
    )
    otp_max_verify_attempts: int = Field(
        default=5,
        ge=1,
        le=10,
        description=(
            "Wrong guesses a one-time code takes before it locks and a new one must be requested; "
            "applies to email and phone codes alike (env: OTP_MAX_VERIFY_ATTEMPTS)"
        ),
    )
    otp_resend_cooldown_seconds: int = Field(
        default=60,
        ge=0,
        le=600,
        description=(
            "Seconds after a phone code is sent before another may be requested "
            "(env: OTP_RESEND_COOLDOWN_SECONDS)"
        ),
    )
    patient_session_hours: int = Field(
        default=12,
        ge=1,
        le=168,
        description=(
            "How long a patient's web session lasts after a phone OTP, in hours: one clinic day "
            "by default (env: PATIENT_SESSION_HOURS)"
        ),
    )
    patient_session_cookie_name: str = Field(
        default="bk_clinicq_patient_session",
        description="httpOnly cookie carrying a patient's session (env: PATIENT_SESSION_COOKIE_NAME)",
    )

    # Password sign-in (M17 — Issue #101). The password-login fallback authenticates on a
    # single request (no OTP round-trip), so it needs its own brute-force / credential-stuffing
    # brake: a per-email budget stops guessing one account's password and a wider per-IP budget
    # stops spraying one password across many accounts. Both share one window (default 15 min).
    password_login_rate_limit_per_email: int = Field(
        default=10,
        ge=1,
        description="Max password sign-in attempts per email per window (env: PASSWORD_LOGIN_RATE_LIMIT_PER_EMAIL)",
    )
    password_login_rate_limit_per_ip: int = Field(
        default=30,
        ge=1,
        description="Max password sign-in attempts per IP per window (env: PASSWORD_LOGIN_RATE_LIMIT_PER_IP)",
    )
    password_login_rate_limit_window_seconds: int = Field(
        default=900,
        ge=1,
        description="Password sign-in rate-limit window in seconds (env: PASSWORD_LOGIN_RATE_LIMIT_WINDOW_SECONDS)",
    )

    # Rate-limiter state (M30 — Issue #179, pen-test F-02). ``memory`` keeps the sliding windows
    # process-local, which is exactly today's behaviour and the default, so a single-worker dev run
    # needs nothing new. ``redis`` shares one window across every worker and instance. The limiter
    # degrades back to the in-process window when the shared store is unreachable and logs it once
    # per outage — a limiter that takes the site down when Redis blinks is a worse outcome than the
    # abuse it prevents.
    # The Redis the application depends on (Issue 6): queued work and live updates arrive in later
    # milestones, and readiness checks it from now on. Unset means "this deployment has no Redis":
    # readiness reports it ``skipped``. Set and unreachable, readiness answers 503 naming it.
    redis_url: str | None = Field(
        default=None,
        description="Redis the app depends on; checked by /health/ready (env: REDIS_URL). Unset: skipped.",
    )
    redis_probe_timeout_seconds: float = Field(
        default=1.0,
        gt=0,
        le=5,
        description=(
            "Connect and read timeout of the readiness Redis ping, in seconds "
            "(env: REDIS_PROBE_TIMEOUT_SECONDS). Short, so a hung Redis fails readiness quickly."
        ),
    )

    rate_limit_backend: RateLimitBackendKind = Field(
        default=RateLimitBackendKind.MEMORY,
        description="Where rate-limit windows live: memory or redis (env: RATE_LIMIT_BACKEND)",
    )

    rate_limit_redis_url: str = Field(
        default="",
        description="Redis URL for the shared rate-limit store (env: RATE_LIMIT_REDIS_URL)",
    )
    # Signed download-link minting (M30 — Issue #179). Loose on purpose: a person opening
    # documents one at a time never approaches it, a script enumerating a subject's files does.
    signed_link_mint_rate_limit_per_ip: int = Field(
        default=60,
        ge=1,
        description="Max signed download links minted per IP per window (env: SIGNED_LINK_MINT_RATE_LIMIT_PER_IP)",
    )
    signed_link_mint_rate_limit_window_seconds: int = Field(
        default=60,
        ge=1,
        description="Signed-link mint rate-limit window in seconds (env: SIGNED_LINK_MINT_RATE_LIMIT_WINDOW_SECONDS)",
    )

    rate_limit_redis_timeout_seconds: float = Field(
        default=0.25,
        gt=0,
        le=5,
        description=(
            "Socket timeout for the shared rate-limit store, in seconds "
            "(env: RATE_LIMIT_REDIS_TIMEOUT_SECONDS). Deliberately short: the limiter is on the "
            "hot path of every sign-in, so a slow store must degrade fast, not queue requests."
        ),
    )
    # Session lifetime policy (optional server-enforced caps on refresh sessions)
    session_absolute_max_days: int | None = Field(
        default=None,
        ge=1,
        description=(
            "If set, refresh sessions older than this many days from session_started_at "
            "require sign-in again (env: SESSION_ABSOLUTE_MAX_DAYS). None disables."
        ),
    )
    session_server_idle_timeout_minutes: int | None = Field(
        default=None,
        ge=1,
        le=10080,
        description=(
            "If set, revoke a refresh session when last_seen_at is older than this many "
            "minutes (server-enforced idle). None disables "
            "(env: SESSION_SERVER_IDLE_TIMEOUT_MINUTES)."
        ),
    )

    # Email transport (stdlib smtplib + STARTTLS). When SMTP_HOST is unset the app logs
    # the activation link instead of sending (development fallback).
    smtp_host: str = Field(
        default="",
        description="SMTP server host for transactional email (env: SMTP_HOST)",
    )
    smtp_port: int = Field(
        default=587,
        ge=1,
        le=65535,
        description="SMTP server port (env: SMTP_PORT)",
    )
    smtp_use_tls: bool = Field(
        default=True,
        validation_alias=AliasChoices("SMTP_USE_TLS", "SMTP_TLS"),
        description="Use STARTTLS for SMTP (env: SMTP_USE_TLS; SMTP_TLS accepted as alias)",
    )
    smtp_user: str = Field(
        default="",
        description="SMTP username (env: SMTP_USER)",
    )
    smtp_password: str = Field(
        default="",
        description="SMTP password (env: SMTP_PASSWORD)",
    )
    smtp_from_email: str = Field(
        default="noreply@clinicq.bkatalayi.com",
        description="Default From address for transactional email (env: SMTP_FROM_EMAIL)",
    )

    @model_validator(mode="after")
    def strip_smtp_credentials(self) -> Settings:
        """Trim accidental whitespace/newlines from pasted SMTP secrets so auth matches."""
        self.smtp_host = (self.smtp_host or "").strip()
        self.smtp_user = (self.smtp_user or "").strip()
        self.smtp_password = (self.smtp_password or "").strip()
        self.smtp_from_email = (self.smtp_from_email or "").strip()
        return self

    # Notification service (Issue #67) — one service for transactional email + SMS with
    # delivery status. Email reuses the SMTP transport above; SMS sits behind a pluggable
    # provider interface (see ``src.modules.notifications.sms``).
    sms_provider: SmsProviderKind = Field(
        default=SmsProviderKind.LOGGING,
        description=(
            "SMS provider backing the SMS channel (env: SMS_PROVIDER). 'logging' logs and "
            "returns a synthetic id so the app runs without an SMS account; 'fake' is the "
            "in-memory test double."
        ),
    )

    sms_from: str = Field(
        default="",
        description="Sender id / from-number for outbound SMS (env: SMS_FROM).",
    )

    notification_max_attempts: int = Field(
        default=5,
        ge=1,
        le=20,
        description=(
            "How many delivery attempts a notification gets before it is dead-lettered "
            "(env: NOTIFICATION_MAX_ATTEMPTS)."
        ),
    )

    notification_retry_base_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description=(
            "Base seconds for exponential backoff between delivery retries — the wait before "
            "attempt n is base * 2**(n-1) (env: NOTIFICATION_RETRY_BASE_SECONDS)."
        ),
    )

    notification_retry_interval_minutes: int = Field(
        default=5,
        ge=1,
        le=1440,
        description=(
            "How often the notification retry sweep runs to re-attempt due failures "
            "(env: NOTIFICATION_RETRY_INTERVAL_MINUTES)."
        ),
    )

    notification_webhook_secret: str = Field(
        default="",
        description=(
            "Shared secret a delivery-status webhook must present in the "
            "'X-Webhook-Secret' header before a provider callback is accepted. When unset, "
            "the webhook endpoint is disabled (env: NOTIFICATION_WEBHOOK_SECRET)."
        ),
    )

    # Notification preferences (Issue #72). ``public_base_url`` is the externally reachable origin
    # (e.g. ``https://clinicq.bkatalayi.com``) used to build absolute links in mail that is sent
    # outside a request — chiefly the login-free unsubscribe link and its ``List-Unsubscribe``
    # header. When unset (development), the header is omitted rather than pointing at a wrong host.
    public_base_url: str = Field(
        default="",
        description=(
            "Externally reachable base URL for links in outbound mail, e.g. "
            "'https://clinicq.bkatalayi.com' (env: PUBLIC_BASE_URL). When unset, the "
            "List-Unsubscribe header is omitted."
        ),
    )
    unsubscribe_link_expire_days: int = Field(
        default=365,
        ge=1,
        le=3650,
        description=(
            "How long a login-free unsubscribe link stays valid, in days — long-lived because "
            "email lingers in inboxes (env: UNSUBSCRIBE_LINK_EXPIRE_DAYS)."
        ),
    )

    @model_validator(mode="after")
    def strip_public_base_url(self) -> Settings:
        """Trim whitespace and a trailing slash so link building can concatenate paths safely."""
        self.public_base_url = (self.public_base_url or "").strip().rstrip("/")
        return self

    # Browser session cookies (httpOnly access + refresh; non-httpOnly CSRF for double-submit)
    access_token_cookie_name: str = Field(
        default="bk_clinicq_access_token",
        description="httpOnly cookie carrying the JWT access token (env: ACCESS_TOKEN_COOKIE_NAME)",
    )
    refresh_token_cookie_name: str = Field(
        default="bk_clinicq_refresh_token",
        description="httpOnly cookie carrying the opaque refresh token (env: REFRESH_TOKEN_COOKIE_NAME)",
    )
    csrf_cookie_name: str = Field(
        default="bk_clinicq_csrf",
        description="Non-httpOnly CSRF cookie for double-submit (env: CSRF_COOKIE_NAME)",
    )

    # Profile pictures (M23 — Issue #130). An avatar is small by definition, so the cap is far
    # below the document/attachment ones: an upload larger than this is a mistake or an attack,
    # not a photo. Uploads are re-encoded to a square WEBP of avatar_max_dimension_px on a side,
    # which strips EXIF (including GPS) and normalises every stored file to one format. The bytes
    # live in private storage under avatar_storage_dir and are served only from the opaque,
    # unguessable key recorded on the user row — never a path built from a user id or email.
    avatar_max_bytes: int = Field(
        default=5 * 1024 * 1024,
        ge=1,
        description="Max accepted profile-picture upload size in bytes (env: AVATAR_MAX_BYTES)",
    )
    avatar_storage_dir: str = Field(
        default="var/avatars",
        description="Filesystem base dir for stored profile pictures (env: AVATAR_STORAGE_DIR)",
    )
    avatar_max_dimension_px: int = Field(
        default=512,
        ge=32,
        le=2048,
        description=(
            "Side length in pixels of the stored square avatar; larger uploads are "
            "centre-cropped and downscaled to it (env: AVATAR_MAX_DIMENSION_PX)"
        ),
    )
    avatar_cache_max_age_seconds: int = Field(
        default=86400,
        ge=0,
        description=(
            "Cache-Control max-age for a served avatar. Safe to cache because a replacement "
            "gets a new key (and so a new URL) (env: AVATAR_CACHE_MAX_AGE_SECONDS)"
        ),
    )

    # Unified document storage service (M11 — Issue #70). One bounded context behind which tenant,
    # application, lease and maintenance attachments are consolidated: a single ``document`` record
    # (polymorphic owner, type, storage key, SHA-256 checksum, retention class, virus-scan status).
    # Bytes live in private storage under document_storage_dir (never a public URL) and are only ever
    # handed out via a short-lived signed link (document_link_expire_seconds). The size cap is
    # enforced from the declared Content-Length before the body is read into memory (and again while
    # reading), so an oversized upload is rejected before it is buffered. Ingest scans the raw bytes
    # (document_scanner) and refuses an infected upload; the checksum is verified again on download.
    document_max_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=1,
        description="Max accepted document upload size in bytes (env: DOCUMENT_MAX_BYTES)",
    )

    document_storage_dir: str = Field(
        default="var/documents",
        description="Filesystem base dir for stored documents (env: DOCUMENT_STORAGE_DIR)",
    )

    document_link_expire_seconds: int = Field(
        default=300,
        ge=1,
        description="Lifetime of a signed document download link in seconds (env: DOCUMENT_LINK_EXPIRE_SECONDS)",
    )

    document_virus_scan_enabled: bool = Field(
        default=True,
        description=(
            "Scan document bytes for malware on ingest (env: DOCUMENT_VIRUS_SCAN_ENABLED). When "
            "false, ingest records the scan as 'skipped' and stores the bytes unscanned."
        ),
    )

    document_scanner: DocumentScannerKind = Field(
        default=DocumentScannerKind.EICAR,
        description=(
            "Virus scanner backing document ingest (env: DOCUMENT_SCANNER). 'eicar' flags the "
            "industry-standard EICAR test file and passes everything else so the app runs without "
            "an antivirus daemon; 'fake' is the in-memory test double."
        ),
    )

    document_retention_sweep_hour: int = Field(
        default=3,
        ge=0,
        le=23,
        description=(
            "Hour of day (Africa/Johannesburg, 0-23) the daily document retention sweep runs, "
            "purging expired documents and recording each deletion (env: "
            "DOCUMENT_RETENTION_SWEEP_HOUR)."
        ),
    )

    # E-signature integration (M11 — Issue #71). A generated lease/addendum is sent for signature
    # through a pluggable provider (esign_provider) behind an interface, its envelope status tracked,
    # and the executed document plus its certificate of completion retained on the unified document
    # store. The provider webhook is verified with an HMAC-SHA256 signature over the raw request body
    # keyed on esign_webhook_secret (a credential — never logged), and is idempotent on replay. When
    # esign_enabled is false the flow is off and a lease is signed on paper; the webhook endpoint
    # 404s unless a secret is configured, so the shared secret is never revealed.
    esign_enabled: bool = Field(
        default=True,
        description=(
            "Enable the e-signature signing flow (env: ESIGN_ENABLED). When false, leases are "
            "signed on paper and the send endpoints are unavailable."
        ),
    )

    esign_provider: EsignProviderKind = Field(
        default=EsignProviderKind.LOCAL,
        description=(
            "E-signature provider backing the signing flow (env: ESIGN_PROVIDER). 'local' is a "
            "self-contained provider that runs the whole flow without an external account; 'fake' "
            "is the in-memory test double."
        ),
    )

    esign_webhook_secret: str = Field(
        default="",
        description=(
            "Shared secret the provider signs its webhooks with, verified as an HMAC-SHA256 of the "
            "raw request body (env: ESIGN_WEBHOOK_SECRET). A credential — never logged. When empty "
            "the webhook endpoint 404s, so the endpoint's existence is not revealed."
        ),
    )

    esign_envelope_expire_days: int = Field(
        default=14,
        ge=1,
        le=365,
        description=(
            "Days a signing request stays open before it is considered expired (env: "
            "ESIGN_ENVELOPE_EXPIRE_DAYS); stamped on the envelope's expires_at when it is sent."
        ),
    )

    # Background jobs (APScheduler). One process-wide scheduler runs the sweeps registered in
    # ``src.core.scheduler`` on the application timezone. Each sweep elects a single runner across
    # instances with a PostgreSQL advisory lock and is independently idempotent, so a missed run
    # catches up without doing anything twice. Turn the scheduler off when several local processes
    # share one database, or the sweeps contend for no reason.
    scheduler_enabled: bool = Field(
        default=True,
        description=(
            "Start the background job scheduler on app startup "
            "(env: SCHEDULER_ENABLED). Set false to disable all scheduled jobs."
        ),
    )

    # Grant usage telemetry (M29 — Issue #176). ``permission_audit_log`` records every grant
    # *change*; nothing recorded a grant's *use*, so nobody could distinguish a load-bearing grant
    # from one copied into a role during a migration years ago — and the only safe move with any
    # grant was to leave it alone. Collection happens at the three ``ensure_*`` chokepoints, on an
    # allow only, into an in-process buffer that an APScheduler job flushes on an interval. It is
    # **advisory data for pruning decisions, never an audit trail**: a buffer lost on shutdown is
    # acceptable, and the UI must never present it as a record of what happened.
    permission_usage_enabled: bool = Field(
        default=True,
        description=(
            "Record which grants are actually exercised, for the unused-grant pruning worklist. "
            "On by default; a deployment that does not want it should not have to patch code "
            "(env: PERMISSION_USAGE_ENABLED)."
        ),
    )

    permission_usage_flush_minutes: int = Field(
        default=5,
        ge=1,
        le=1440,
        description=(
            "How often the buffered grant-usage hits are flushed to permission_usage. Longer "
            "windows coalesce more and lose more on shutdown; both are acceptable for advisory "
            "data (env: PERMISSION_USAGE_FLUSH_MINUTES)."
        ),
    )

    #: How long a grant may go unused before the console's pruning worklist offers it up. 90 days is
    #: the IAM Access Advisor convention and long enough to survive a quarterly business cycle.
    permission_usage_unused_days: int = Field(
        default=90,
        ge=1,
        le=3650,
        description=(
            "The 'granted, never used in N days' worklist threshold shown on the permissions "
            "matrix (env: PERMISSION_USAGE_UNUSED_DAYS)."
        ),
    )

    # Stripe payment gateway (M12 — Issue #76). Stripe collects card money; the lease ledger stays
    # the source of truth and Stripe is reconciled *into* it. Off by default so a deployment without
    # keys behaves exactly as before (manual capture only). The three keys are secrets read from the
    # environment and never logged; the non-production guard below refuses a live secret key outside
    # production so a test environment can never move real money.
    stripe_enabled: bool = Field(
        default=False,
        description="Enable the Stripe payment gateway and its webhook (env: STRIPE_ENABLED).",
    )

    stripe_secret_key: str = Field(
        default="",
        description=(
            "Stripe secret API key (sk_test_… / sk_live_…); server-side only, never logged "
            "(env: STRIPE_SECRET_KEY)."
        ),
    )

    stripe_publishable_key: str = Field(
        default="",
        description=(
            "Stripe publishable key (pk_test_… / pk_live_…) handed to the client to confirm a "
            "PaymentIntent (env: STRIPE_PUBLISHABLE_KEY)."
        ),
    )

    stripe_webhook_secret: str = Field(
        default="",
        description=(
            "Signing secret (whsec_…) used to verify webhook signatures; never logged "
            "(env: STRIPE_WEBHOOK_SECRET)."
        ),
    )

    # Paystack payment gateway (M12 — Issue #79). Paystack gives tenants in South Africa and the
    # wider African market (ZAR/NGN) a local card and bank-transfer rail alongside Stripe; as with
    # Stripe the lease ledger stays the source of truth and Paystack is reconciled *into* it. Off by
    # default so a deployment without keys behaves exactly as before (manual capture only). Paystack
    # uses the **secret key** both to authenticate API calls and to sign webhooks (HMAC SHA512), so
    # there is no separate webhook secret; the non-production guard below refuses a live secret key
    # outside production so a test environment can never move real money. Keys are never logged.
    paystack_enabled: bool = Field(
        default=False,
        description="Enable the Paystack payment gateway and its webhook (env: PAYSTACK_ENABLED).",
    )

    paystack_secret_key: str = Field(
        default="",
        description=(
            "Paystack secret API key (sk_test_… / sk_live_…); server-side only, never logged. Also "
            "the HMAC-SHA512 webhook signing key (env: PAYSTACK_SECRET_KEY)."
        ),
    )

    paystack_public_key: str = Field(
        default="",
        description=(
            "Paystack public key (pk_test_… / pk_live_…) handed to the client to open the checkout "
            "(env: PAYSTACK_PUBLIC_KEY)."
        ),
    )

    # Geocoding (M4 — Issue 23). Turning a clinic's typed address into a coordinate happens on the
    # server: the Content-Security-Policy allows connect-src 'self' only, so a browser cannot reach
    # a geocoder, and routing it through the application keeps the address and any provider
    # credential in one place. Off by default — an unconfigured deployment asks the operator for
    # the coordinate rather than silently calling somebody else's service.
    geocoding_provider: GeocodingProvider = Field(
        default=GeocodingProvider.NONE,
        description=(
            "Which service turns a typed address into a coordinate, server-side (env: "
            "GEOCODING_PROVIDER). 'none' disables the lookup and site creation takes an explicit "
            "coordinate; 'nominatim' uses the OpenStreetMap search API or a self-hosted instance."
        ),
    )

    geocoding_base_url: str = Field(
        default="https://nominatim.openstreetmap.org",
        description=(
            "Base URL of the geocoding service (env: GEOCODING_BASE_URL). Point it at a "
            "self-hosted Nominatim to keep clinic addresses inside the deployment."
        ),
    )

    geocoding_user_agent: str = Field(
        default="",
        description=(
            "Descriptive User-Agent identifying this deployment to the geocoding service (env: "
            "GEOCODING_USER_AGENT), e.g. 'ClinicQ/0.4 (ops@example.org)'. Nominatim's usage policy "
            "requires one, so the lookup is refused while this is empty."
        ),
    )

    geocoding_country_code: str = Field(
        default="za",
        min_length=2,
        max_length=2,
        description=(
            "ISO 3166-1 alpha-2 code the geocoder is restricted to (env: GEOCODING_COUNTRY_CODE). "
            "Matches the operating-area bounding box in src.commons.geo."
        ),
    )

    geocoding_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=30,
        description=(
            "How long to wait for the geocoding service before giving up and asking the operator "
            "for the coordinate (env: GEOCODING_TIMEOUT_SECONDS)."
        ),
    )

    # Discovery (M5). The clinic detail page (Issue 35) shows a "Join the queue" action that is
    # enabled only when joining is possible. Joining itself is Issue 40's (M6), so until that route
    # exists the flag stays off and the button says why it is disabled, instead of leading nowhere.
    patient_join_enabled: bool = Field(
        default=False,
        description=(
            "Offer 'Join the queue' on the clinic detail page when a clinic is open and has a queue "
            "that takes remote joins (env: PATIENT_JOIN_ENABLED). Off until the join flow (Issue "
            "40) ships; while off, the button is shown disabled with the reason."
        ),
    )

    @property
    def database_url_async(self) -> str:
        """The request-path async DSN: ``database_url`` with an async driver (Issue #81).

        Swaps the DBAPI driver on the existing URL so the async engine talks to the *same*
        database as the synchronous one — PostgreSQL over ``db_async_driver`` (asyncpg) in every
        real deployment, and ``aiosqlite`` for the in-memory SQLite used by tests — without
        duplicating host/credential settings. The synchronous ``database_url`` is left untouched
        for Alembic, cron, the CLI and scripts.
        """
        url = make_url(self.database_url)
        backend = url.get_backend_name()
        if backend == "sqlite":
            return url.set(drivername="sqlite+aiosqlite").render_as_string(
                hide_password=False
            )
        return url.set(drivername=f"{backend}+{self.db_async_driver}").render_as_string(
            hide_password=False
        )

    @property
    def is_development(self) -> bool:
        return self.environment == AppEnvironment.DEVELOPMENT

    @property
    def is_production(self) -> bool:
        return self.environment == AppEnvironment.PRODUCTION

    @property
    def s3_environment(self) -> str:
        """Short environment name used in the S3 key prefix: dev / uat / prod."""
        mapping = {
            AppEnvironment.DEVELOPMENT: "dev",
            AppEnvironment.STAGING: "uat",
            AppEnvironment.PRODUCTION: "prod",
        }
        return mapping.get(self.environment, "dev")

    # ── The one validation path (Issues 1 and 12) ────────────────────────────────────────────
    #
    # Every rule a configuration must pass lives in configuration_problems(), and nowhere else. The
    # boot guard below raises with the whole list at once, so a bad deploy learns everything wrong
    # with it from one failed start; scripts/check_config.py reports the same list for an env file
    # without starting the app. A new rule goes into that method, never into a validator of its own.

    #: scripts/check_config.py turns this off to collect the problems instead of stopping at them.
    raise_on_configuration_problems: ClassVar[bool] = True

    def configuration_problems(self) -> list[ConfigProblem]:
        """Return every value this environment must not run with, naming the setting, never its value.

        Anywhere: an enabled payment gateway needs its keys, and a live key belongs to production
        only, so no test environment can move real money. Outside development: no default JWT
        secret, no bcrypt cost under 12, no debug mode, no CORS open to every origin, and no DB_*
        part silently contradicting DATABASE_URL.
        """
        problems: list[ConfigProblem] = []
        is_production = self.environment is AppEnvironment.PRODUCTION
        if self.stripe_enabled and not (
            self.stripe_secret_key and self.stripe_webhook_secret
        ):
            problems.append(
                ConfigProblem(
                    "STRIPE_SECRET_KEY",
                    "STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET must be set when "
                    "STRIPE_ENABLED is true.",
                )
            )
        if (
            self.stripe_secret_key.startswith(_LIVE_SECRET_KEY_PREFIX)
            and not is_production
        ):
            problems.append(
                ConfigProblem(
                    "STRIPE_SECRET_KEY",
                    "A live Stripe secret key (sk_live_…) may only be used in production; use a "
                    "test key (sk_test_…) outside production.",
                )
            )
        if self.paystack_enabled and not self.paystack_secret_key:
            problems.append(
                ConfigProblem(
                    "PAYSTACK_SECRET_KEY",
                    "PAYSTACK_SECRET_KEY must be set when PAYSTACK_ENABLED is true.",
                )
            )
        if (
            self.paystack_secret_key.startswith(_LIVE_SECRET_KEY_PREFIX)
            and not is_production
        ):
            problems.append(
                ConfigProblem(
                    "PAYSTACK_SECRET_KEY",
                    "A live Paystack secret key (sk_live_…) may only be used in production; use "
                    "a test key (sk_test_…) outside production.",
                )
            )
        if self.environment is AppEnvironment.DEVELOPMENT:
            return problems

        if self.jwt_secret == _DEFAULT_JWT_SECRET:
            problems.append(
                ConfigProblem(
                    "JWT_SECRET",
                    "JWT_SECRET must be set to a secure value outside development. "
                    "Do not use the default secret.",
                )
            )
        if self.bcrypt_rounds < _MIN_BCRYPT_ROUNDS_OUTSIDE_DEVELOPMENT:
            problems.append(
                ConfigProblem(
                    "BCRYPT_ROUNDS",
                    "BCRYPT_ROUNDS must be >= 12 outside development. "
                    "A lower cost is only for development/test speed.",
                )
            )
        if self.debug:
            problems.append(
                ConfigProblem(
                    "DEBUG",
                    "DEBUG must be false outside development: debug mode sends tracebacks "
                    "to whoever triggered the error.",
                )
            )
        if self.metrics_enabled and not self.metrics_token:
            problems.append(
                ConfigProblem(
                    "METRICS_TOKEN",
                    "METRICS_TOKEN must be set outside development while METRICS_ENABLED is "
                    "true, or anyone could read the app's traffic at /metrics.",
                )
            )
        if (
            self.error_tracking_test_route
            and self.environment is AppEnvironment.PRODUCTION
        ):
            problems.append(
                ConfigProblem(
                    "ERROR_TRACKING_TEST_ROUTE",
                    "ERROR_TRACKING_TEST_ROUTE must be false in production: it raises an error "
                    "on request, which is for proving error tracking on staging.",
                )
            )
        if CORS_ANY_ORIGIN in self.cors_origins:
            problems.append(
                ConfigProblem(
                    "CORS_ORIGINS",
                    "CORS_ORIGINS must list exact origins outside development, never '*', "
                    "which lets every website call the app.",
                )
            )
        problems.extend(
            ConfigProblem(
                part,
                f"{part} disagrees with DATABASE_URL, which wins, so {part} is ignored. Set "
                "DATABASE_URL alone, or the DB_* parts without it.",
            )
            for part in self._ignored_database_parts
        )
        return problems

    @model_validator(mode="after")
    def refuse_unsafe_configuration(self) -> Settings:
        """Refuse to start with any configuration problem, listing all of them in one error."""
        problems = self.configuration_problems()
        if problems and self.raise_on_configuration_problems:
            raise ValueError(
                "refusing to start with this configuration: "
                + " | ".join(problem.message for problem in problems)
            )
        return self


def setting_env_names() -> dict[str, tuple[str, ...]]:
    """Map each setting to the environment variables that set it, the canonical one first.

    That is the field's validation aliases when it has them (``VERSION``, then ``APP_VERSION``),
    otherwise its name in capitals. ``.env.example``, its drift test and
    ``scripts/check_config.py`` all read the names from here, so none of them can disagree with
    the class about what a setting is called.
    """
    names: dict[str, tuple[str, ...]] = {}
    for name, field in Settings.model_fields.items():
        alias = field.validation_alias
        if isinstance(alias, AliasChoices):
            names[name] = tuple(str(choice).upper() for choice in alias.choices)
        elif isinstance(alias, str):
            names[name] = (alias.upper(),)
        else:
            names[name] = (name.upper(),)
    return names


@lru_cache
def get_settings() -> Settings:
    return Settings()
