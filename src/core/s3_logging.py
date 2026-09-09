"""S3 structured-logging handler.

Buffers log records and uploads them to S3 as NDJSON under the key pattern::

    {env}/logs/{log_type}/{api|web}/{YYYY}/{MM}/{DD}/clinicq-{ccyymmdd}-{HHMMSS}.json

A daemon thread flushes the buffer on a count (``max_events``) or time
(``flush_interval_seconds``) trigger so request latency is never affected. Uploads
run on background threads and every failure is non-fatal: logs are dropped and the
application keeps serving. Ported and adapted from the ``maps`` project.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime
from datetime import time as datetime_time
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from src.commons.enums import S3LogType
from src.core.config import get_settings

# af-south-1 (Cape Town) region timestamps use the Johannesburg wall clock.
APP_TIMEZONE = ZoneInfo("Africa/Johannesburg")


def _json_fallback_serializer(value: Any) -> Any:
    """Serialize non-JSON-native log values into safe JSON representations."""
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _to_json_line(payload: dict[str, Any]) -> str:
    """Return one JSON line for S3 logs using safe fallback serialization."""
    return json.dumps(payload, default=_json_fallback_serializer)


def _level_to_log_type(level: int) -> S3LogType:
    """Map a logging level to the S3 ``log_type`` segment (info / warning / error)."""
    if level >= logging.ERROR:
        return S3LogType.ERROR
    if level >= logging.WARNING:
        return S3LogType.WARNING
    return S3LogType.INFO


def _s3_key(log_type: str, dt: datetime, path_segment: str | None = None) -> str:
    """Build the S3 object key for a batch of records.

    ``{env}/logs/{log_type}/{path}/{YYYY}/{MM}/{DD}/clinicq-{ccyymmdd}-{HHMMSS}.json``
    """
    cfg = get_settings()
    env = cfg.s3_environment
    segment = path_segment or cfg.aws_s3_log_path.value
    yy = dt.strftime("%Y")
    mm = dt.strftime("%m")
    dd = dt.strftime("%d")
    ccyymmdd = dt.strftime("%Y%m%d")
    hhmmss = dt.strftime("%H%M%S")
    return f"{env}/logs/{log_type}/{segment}/{yy}/{mm}/{dd}/clinicq-{ccyymmdd}-{hhmmss}.json"


def _create_s3_client(
    region: str, verify_ssl: bool, botocore_config: Any | None = None
) -> Any:
    """Create a boto3 S3 client, using explicit credentials from settings when set.

    ``botocore_config`` is an optional ``botocore.config.Config`` — the readiness
    probe passes one with short timeouts and no retries so a slow or unreachable S3
    fails fast instead of stalling the health check.
    """
    import boto3  # type: ignore[import-untyped]

    if not verify_ssl:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    cfg = get_settings()
    kwargs: dict[str, Any] = {"region_name": region, "verify": verify_ssl}
    access_key = (cfg.aws_access_key_id or "").strip()
    secret_key = (cfg.aws_secret_access_key or "").strip()
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    if botocore_config is not None:
        kwargs["config"] = botocore_config
    return boto3.client("s3", **kwargs)


def create_s3_probe_client() -> Any | None:
    """Return a fast-fail S3 client for the readiness probe, or ``None``.

    Short connect/read timeouts and zero retries keep a slow or unreachable S3 from
    stalling ``/health``. Returns ``None`` when no bucket is configured or the client
    cannot be built (the probe reports the storage dependency as degraded).
    """
    cfg = get_settings()
    if not (cfg.aws_s3_bucket or "").strip():
        return None
    try:
        from botocore.config import Config  # type: ignore[import-untyped]

        probe_config = Config(
            connect_timeout=2, read_timeout=2, retries={"max_attempts": 0}
        )
        return _create_s3_client(
            cfg.aws_s3_region or "af-south-1",
            verify_ssl=cfg.aws_ssl_cert_enabled,
            botocore_config=probe_config,
        )
    except Exception:
        return None


def get_s3_logs_client() -> Any | None:
    """Return a boto3 S3 client for the configured logs bucket, or ``None``.

    Returns ``None`` when the bucket is unset or client creation fails. Used by the
    read-side log viewer (Issue #8) for ``ListObjectsV2`` / ``GetObject``.

    Bounded connect/read timeouts and a single retry keep a slow or unreachable S3 from
    hanging the ``/admin/logs`` request on boto's ~60s defaults and adaptive retry budget
    — the viewer degrades to a fast error instead. Slightly more forgiving than the
    ``create_s3_probe_client`` health check (which uses zero retries), because a listing is
    a real user action worth one retry, not a liveness ping that must fail instantly.
    """
    cfg = get_settings()
    if not (cfg.aws_s3_bucket or "").strip():
        return None
    try:
        from botocore.config import Config  # type: ignore[import-untyped]

        viewer_config = Config(
            connect_timeout=3, read_timeout=5, retries={"max_attempts": 1}
        )
        return _create_s3_client(
            cfg.aws_s3_region or "af-south-1",
            verify_ssl=cfg.aws_ssl_cert_enabled,
            botocore_config=viewer_config,
        )
    except Exception:
        return None


def _record_to_payload(record: logging.LogRecord) -> dict[str, Any]:
    """Build the structured JSON payload for one log record (with request context)."""
    cfg = get_settings()
    payload: dict[str, Any] = {
        "timestamp": datetime.fromtimestamp(
            record.created, tz=APP_TIMEZONE
        ).isoformat(),
        "level": record.levelname,
        "log_type": _level_to_log_type(record.levelno).value,
        "path": cfg.aws_s3_log_path.value,
        "message": record.getMessage(),
        "logger": record.name,
    }
    if record.exc_info:
        payload["exception"] = logging.Formatter().formatException(record.exc_info)
    # Request context injected by RequestContextFilter / RequestLoggingMiddleware.
    for attr in (
        "request_id",
        "path",
        "method",
        "client_ip",
        "status",
        "security_http_event",
    ):
        if hasattr(record, attr):
            payload[attr] = getattr(record, attr, None)
    for k, v in getattr(record, "extra_context", {}).items():
        if k not in payload:
            payload[k] = v
    return payload


class S3LogHandler(logging.Handler):
    """Buffer log records and flush them to S3 as NDJSON.

    Flush is triggered by buffer size (``max_events``) or elapsed time
    (``flush_interval_seconds``). Uploads run on background threads; if S3 is
    misconfigured or an upload fails, records are dropped so the app keeps running.
    """

    def __init__(
        self,
        bucket: str,
        region: str,
        flush_interval_seconds: float = 30.0,
        max_events: int = 100,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._bucket = bucket
        self._region = region
        self._flush_interval = flush_interval_seconds
        self._max_events = max_events
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()
        self._client: Any = None
        self._closed = False
        self._start_flush_thread()

    def _get_client(self) -> Any:
        """Lazily create the boto3 S3 client; returns None on failure."""
        if self._client is not None:
            return self._client
        try:
            self._client = _create_s3_client(
                self._region, verify_ssl=get_settings().aws_ssl_cert_enabled
            )
            return self._client
        except Exception:
            return None

    def _start_flush_thread(self) -> None:
        """Start a daemon thread that flushes the buffer on the time trigger."""

        def run() -> None:
            while not self._closed:
                time.sleep(min(5.0, self._flush_interval))
                if self._closed:
                    break
                if time.monotonic() - self._last_flush >= self._flush_interval:
                    self.flush()

        threading.Thread(target=run, daemon=True).start()

    def emit(self, record: logging.LogRecord) -> None:
        """Append the record to the buffer, flushing when it is full."""
        if record.levelno < self.level or self._closed:
            return
        try:
            payload = _record_to_payload(record)
        except Exception:
            return
        with self._lock:
            self._buffer.append(payload)
            if len(self._buffer) >= self._max_events:
                self._flush_holding_lock()

    def _flush_holding_lock(self) -> None:
        """Drain the buffer and spawn one upload per log_type. Requires ``_lock``."""
        if not self._buffer:
            return
        events = self._buffer[:]
        self._buffer = []
        self._last_flush = time.monotonic()
        by_type: dict[str, list[dict[str, Any]]] = {}
        for e in events:
            by_type.setdefault(e.get("log_type", "info"), []).append(e)
        for log_type, group in by_type.items():
            threading.Thread(
                target=self._upload, args=(log_type, group), daemon=True
            ).start()

    def _upload(self, log_type: str, events: list[dict[str, Any]]) -> None:
        """Upload events as one NDJSON object to S3. No-op on failure."""
        if not events:
            return
        client = self._get_client()
        if client is None:
            return
        try:
            ts_str = events[0].get("timestamp", "")
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            dt = datetime.now(APP_TIMEZONE)
        key = _s3_key(log_type, dt)
        body = "\n".join(_to_json_line(e) for e in events)
        try:
            client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body.encode("utf-8"),
                ContentType="application/x-ndjson",
            )
        except Exception as exc:
            # The application must keep running; log once and drop the batch.
            logging.getLogger(__name__).warning(
                "S3 log upload failed (logs dropped): %s", exc
            )

    def flush(self) -> None:
        """Flush buffered records to S3."""
        if self._closed:
            return
        with self._lock:
            self._flush_holding_lock()

    def close(self) -> None:
        """Flush and mark the handler closed."""
        self._closed = True
        self.flush()
        super().close()


def create_s3_handler_if_enabled() -> logging.Handler | None:
    """Return an ``S3LogHandler`` when S3 logging is enabled and a bucket is set.

    Otherwise return ``None`` so the caller keeps only the console handler.
    """
    cfg = get_settings()
    bucket = (cfg.aws_s3_bucket or "").strip()
    if not cfg.aws_s3_logging_enabled or not bucket:
        return None
    return S3LogHandler(
        bucket=bucket,
        region=cfg.aws_s3_region or "af-south-1",
        flush_interval_seconds=30.0,
        max_events=100,
    )
