"""Unit tests for the S3 logging pipeline (Issue 7): key layout, payload shape, flush.

These cover the essential, non-trivial logic of ``src.core.s3_logging`` in isolation
(no network, no real AWS): the S3 key format, the JSON payload built from a log record
(including request context), the level->log_type mapping, JSON-safe serialization, and
the buffering handler's flush-on-count behaviour with boto3 mocked.

Per the testing-strategy Cursor rule these build an isolated ``Settings``
(``_env_file=None``) and point ``s3_logging.get_settings`` at it, so a developer's local
``.env`` cannot change outcomes.
"""

import json
import logging
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.commons.enums import AppEnvironment, S3LogPath, S3LogType
from src.core import s3_logging
from src.core.config import Settings
from src.core.s3_logging import (
    APP_TIMEZONE,
    S3LogHandler,
    _json_fallback_serializer,
    _level_to_log_type,
    _record_to_payload,
    _s3_key,
    create_s3_handler_if_enabled,
)


def _settings(**overrides: object) -> Settings:
    """Build isolated S3-logging ``Settings`` (no ``.env``) with test defaults."""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "aws_s3_logging_enabled": True,
        "aws_s3_bucket": "test-logs-bucket",
        "aws_s3_region": "af-south-1",
        "aws_s3_log_path": S3LogPath.API,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Point ``s3_logging.get_settings`` at an isolated dev/api ``Settings``."""
    cfg = _settings()
    monkeypatch.setattr(s3_logging, "get_settings", lambda: cfg)
    return cfg


# --- S3 key layout -------------------------------------------------------------


def test_s3_key_uses_env_log_type_path_and_date_layout(
    isolated_settings: Settings,
) -> None:
    """Key is ``{env}/logs/{log_type}/{path}/{YYYY}/{MM}/{DD}/clinicq-...json``."""
    dt = datetime(2026, 8, 3, 9, 5, 7, tzinfo=APP_TIMEZONE)
    key = _s3_key(S3LogType.ERROR.value, dt)
    assert key == ("clinicq/dev/logs/error/api/2026/08/03/clinicq-20260803-090507.json")


def test_s3_key_honours_explicit_path_segment(isolated_settings: Settings) -> None:
    """An explicit ``path_segment`` overrides the configured ``aws_s3_log_path``."""
    dt = datetime(2026, 12, 31, 23, 59, 1, tzinfo=APP_TIMEZONE)
    key = _s3_key(S3LogType.WARNING.value, dt, path_segment=S3LogPath.WEB.value)
    assert key == (
        "clinicq/dev/logs/warning/web/2026/12/31/clinicq-20261231-235901.json"
    )


def test_s3_key_starts_with_the_project_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bucket is shared by sibling projects: the key and the file name carry PROJECT_SLUG."""
    cfg = _settings(project_slug="umojanet")
    monkeypatch.setattr(s3_logging, "get_settings", lambda: cfg)
    dt = datetime(2026, 8, 3, 9, 5, 7, tzinfo=APP_TIMEZONE)
    assert _s3_key(S3LogType.ERROR.value, dt) == (
        "umojanet/dev/logs/error/api/2026/08/03/umojanet-20260803-090507.json"
    )


@pytest.mark.parametrize("slug", ["ClinicQ", "clinicq/dev", "", "-clinicq", "clinic q"])
def test_project_slug_must_be_one_lowercase_key_segment(slug: str) -> None:
    """A slug that would split or rename the key prefix is refused at load, naming the setting."""
    with pytest.raises(ValueError, match="PROJECT_SLUG"):
        _settings(project_slug=slug)


def test_s3_prefix_is_slug_env_kind() -> None:
    """``s3_prefix`` is the one place the ``{slug}/{env}/{kind}/`` prefix is built."""
    assert _settings().s3_prefix("logs") == "clinicq/dev/logs/"
    assert _settings(project_slug="maps").s3_prefix("docs") == "maps/dev/docs/"


def test_s3_key_reflects_environment_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production settings put logs under the ``prod`` prefix."""
    cfg = _settings(
        environment=AppEnvironment.PRODUCTION,
        jwt_secret="prod-secret-value-min-32-characters-long!!",
    )
    monkeypatch.setattr(s3_logging, "get_settings", lambda: cfg)
    dt = datetime(2026, 1, 2, 3, 4, 5, tzinfo=APP_TIMEZONE)
    assert _s3_key(S3LogType.INFO.value, dt).startswith("clinicq/prod/logs/info/api/")


# --- level -> log_type mapping -------------------------------------------------


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (logging.DEBUG, S3LogType.INFO),
        (logging.INFO, S3LogType.INFO),
        (logging.WARNING, S3LogType.WARNING),
        (logging.ERROR, S3LogType.ERROR),
        (logging.CRITICAL, S3LogType.ERROR),
    ],
)
def test_level_to_log_type(level: int, expected: S3LogType) -> None:
    """Logging levels map to the info/warning/error severity bucket."""
    assert _level_to_log_type(level) == expected


# --- JSON-safe serialization ---------------------------------------------------


def test_json_fallback_serializes_datetime_enum_and_bytes() -> None:
    """Non-JSON-native values fall back to safe representations."""
    dt = datetime(2026, 8, 3, 10, 0, 0, tzinfo=APP_TIMEZONE)
    assert _json_fallback_serializer(dt) == dt.isoformat()
    assert _json_fallback_serializer(S3LogType.ERROR) == "error"
    assert _json_fallback_serializer(b"h\xffi") == "h�i"


# --- payload shape (request context) ------------------------------------------


def test_record_to_payload_carries_request_context(
    isolated_settings: Settings,
) -> None:
    """The payload includes level/message and injected request context + status."""
    record = logging.LogRecord(
        name="src.api.v1.routes.admin",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="denied %s",
        args=("thing",),
        exc_info=None,
    )
    record.request_id = "req-123"
    record.path = "/api/v1/admin/rbac/roles"
    record.method = "GET"
    record.client_ip = "203.0.113.7"
    record.status = 403
    record.security_http_event = "http_403_forbidden"

    payload = _record_to_payload(record)

    assert payload["level"] == "WARNING"
    assert payload["log_type"] == S3LogType.WARNING.value
    assert payload["message"] == "denied thing"
    assert payload["logger"] == "src.api.v1.routes.admin"
    assert payload["request_id"] == "req-123"
    assert payload["method"] == "GET"
    assert payload["path"] == "/api/v1/admin/rbac/roles"
    assert payload["client_ip"] == "203.0.113.7"
    assert payload["status"] == 403
    assert payload["security_http_event"] == "http_403_forbidden"
    # Payload must be JSON-serializable with the safe fallback.
    json.loads(json.dumps(payload, default=_json_fallback_serializer))


def test_record_to_payload_includes_exception_text(
    isolated_settings: Settings,
) -> None:
    """A record with ``exc_info`` carries a formatted exception string."""
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="worker",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    payload = _record_to_payload(record)
    assert "ValueError: boom" in payload["exception"]


# --- handler enable/disable gate ----------------------------------------------


def test_create_handler_returns_none_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With S3 logging disabled, no handler is created (console-only)."""
    cfg = _settings(aws_s3_logging_enabled=False)
    monkeypatch.setattr(s3_logging, "get_settings", lambda: cfg)
    assert create_s3_handler_if_enabled() is None


def test_create_handler_returns_none_without_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no bucket set, no handler is created even when enabled."""
    cfg = _settings(aws_s3_bucket="   ")
    monkeypatch.setattr(s3_logging, "get_settings", lambda: cfg)
    assert create_s3_handler_if_enabled() is None


def test_create_handler_returns_handler_when_enabled(
    isolated_settings: Settings,
) -> None:
    """With S3 logging enabled and a bucket set, an S3LogHandler is returned."""
    handler = create_s3_handler_if_enabled()
    try:
        assert isinstance(handler, S3LogHandler)
    finally:
        if handler is not None:
            handler.close()


# --- read-side viewer client (Issue #8) ---------------------------------------


def test_logs_client_is_none_without_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No bucket configured -> no client, and no boto3 call is attempted."""
    cfg = _settings(aws_s3_bucket="   ")
    monkeypatch.setattr(s3_logging, "get_settings", lambda: cfg)

    def _should_not_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("must short-circuit before building a client")

    monkeypatch.setattr(s3_logging, "_create_s3_client", _should_not_run)
    assert s3_logging.get_s3_logs_client() is None


def test_logs_client_uses_bounded_timeouts_and_capped_retries(
    isolated_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The viewer client fails fast: bounded connect/read timeouts and a capped retry.

    Without this config the client inherits boto's ~60s defaults and adaptive retry budget,
    so a slow or unreachable S3 hangs the ``/admin/logs`` request instead of erroring quickly.
    """
    captured: dict[str, object] = {}

    def _capture(
        region: str, verify_ssl: bool, botocore_config: object = None
    ) -> object:
        captured["config"] = botocore_config
        return MagicMock()

    monkeypatch.setattr(s3_logging, "_create_s3_client", _capture)

    client = s3_logging.get_s3_logs_client()
    assert client is not None

    config = captured["config"]
    assert config is not None, (
        "viewer client must be built with an explicit botocore Config"
    )
    assert config.connect_timeout == 3
    assert config.read_timeout == 5
    # Capped, but non-zero: one retry tolerates a transient blip on a real user action.
    assert config.retries == {"max_attempts": 1}


# --- buffered flush (boto3 mocked) --------------------------------------------


def test_upload_writes_ndjson_payload_with_content_type(
    isolated_settings: Settings,
) -> None:
    """``_upload`` puts one NDJSON object per batch with the right key and type."""
    handler = S3LogHandler(bucket="test-logs-bucket", region="af-south-1")
    mock_client = MagicMock()
    handler._get_client = lambda: mock_client  # type: ignore[method-assign]
    try:
        dt = datetime(2026, 8, 3, 6, 0, 0, tzinfo=APP_TIMEZONE)
        events = [
            {"timestamp": dt.isoformat(), "log_type": "error", "message": "one"},
            {"timestamp": dt.isoformat(), "log_type": "error", "message": "two"},
        ]
        handler._upload("error", events)
    finally:
        handler.close()

    kwargs = mock_client.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "test-logs-bucket"
    assert kwargs["ContentType"] == "application/x-ndjson"
    assert kwargs["Key"] == (
        "clinicq/dev/logs/error/api/2026/08/03/clinicq-20260803-060000.json"
    )
    lines = kwargs["Body"].decode("utf-8").split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["message"] == "one"
    assert json.loads(lines[1])["message"] == "two"


def test_emit_flushes_on_count_and_uploads(
    isolated_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reaching ``max_events`` drains the buffer and dispatches an upload."""
    handler = S3LogHandler(bucket="test-logs-bucket", region="af-south-1", max_events=3)
    handler.setLevel(logging.WARNING)
    uploads: list[tuple[str, int]] = []
    monkeypatch.setattr(
        handler,
        "_upload",
        lambda log_type, events: uploads.append((log_type, len(events))),
    )
    # Run the flush's upload dispatch synchronously instead of on a thread.
    monkeypatch.setattr(s3_logging.threading, "Thread", _SyncThread)
    try:
        for i in range(3):
            record = logging.LogRecord(
                name="svc",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="err %d",
                args=(i,),
                exc_info=None,
            )
            handler.emit(record)
    finally:
        handler.close()

    assert handler._buffer == []
    assert uploads == [("error", 3)]


def test_emit_below_threshold_does_not_flush(isolated_settings: Settings) -> None:
    """Records below ``max_events`` stay buffered until a flush trigger."""
    handler = S3LogHandler(
        bucket="test-logs-bucket", region="af-south-1", max_events=10
    )
    handler.setLevel(logging.WARNING)
    try:
        record = logging.LogRecord(
            name="svc",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="warn",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        assert len(handler._buffer) == 1
    finally:
        handler.close()


def test_upload_failure_is_non_fatal(
    isolated_settings: Settings,
) -> None:
    """A boto3 ``put_object`` failure is swallowed so the app keeps running."""
    handler = S3LogHandler(bucket="test-logs-bucket", region="af-south-1")
    mock_client = MagicMock()
    mock_client.put_object.side_effect = RuntimeError("S3 down")
    handler._get_client = lambda: mock_client  # type: ignore[method-assign]
    try:
        # Should not raise despite the upload error.
        handler._upload("error", [{"timestamp": "", "message": "x"}])
    finally:
        handler.close()
    mock_client.put_object.assert_called_once()


class _SyncThread:
    """Minimal ``threading.Thread`` stand-in that runs the target synchronously."""

    def __init__(
        self,
        target: object = None,
        args: tuple[object, ...] = (),
        daemon: bool | None = None,
    ) -> None:
        self._target = target
        self._args = args

    def start(self) -> None:
        """Invoke the target immediately in the calling thread."""
        if callable(self._target):
            self._target(*self._args)


# --- create the bucket when it is missing ---------------------------------------


def _client_error(code: str, operation: str) -> Exception:
    from botocore.exceptions import ClientError  # type: ignore[import-untyped]

    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


@pytest.fixture
def fresh_bucket_checks() -> object:
    """Each test starts with no bucket checked in this process."""
    s3_logging._ensured_buckets.clear()
    yield
    s3_logging._ensured_buckets.clear()


def _creating_settings(
    monkeypatch: pytest.MonkeyPatch, *, create: bool = True
) -> Settings:
    cfg = _settings(aws_s3_create_bucket_if_missing=create)
    monkeypatch.setattr(s3_logging, "get_settings", lambda: cfg)
    return cfg


def test_create_bucket_if_missing_defaults_to_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is ever created unless AWS_S3_CREATE_BUCKET_IF_MISSING says so."""
    monkeypatch.delenv("AWS_S3_CREATE_BUCKET_IF_MISSING", raising=False)
    assert Settings(_env_file=None).aws_s3_create_bucket_if_missing is False
    monkeypatch.setenv("AWS_S3_CREATE_BUCKET_IF_MISSING", "true")
    assert Settings(_env_file=None).aws_s3_create_bucket_if_missing is True


@pytest.mark.parametrize(
    ("region", "configuration"),
    [
        ("us-east-1", None),
        ("af-south-1", {"LocationConstraint": "af-south-1"}),
    ],
)
def test_a_missing_bucket_is_created_in_the_region(
    monkeypatch: pytest.MonkeyPatch,
    fresh_bucket_checks: object,
    region: str,
    configuration: dict[str, str] | None,
) -> None:
    """HeadBucket 404 → CreateBucket, with a LocationConstraint everywhere but us-east-1."""
    _creating_settings(monkeypatch)
    client = MagicMock()
    client.head_bucket.side_effect = _client_error("404", "HeadBucket")

    s3_logging.ensure_bucket_exists(client, "btkplatform", region)

    expected: dict[str, object] = {"Bucket": "btkplatform"}
    if configuration is not None:
        expected["CreateBucketConfiguration"] = configuration
    client.create_bucket.assert_called_once_with(**expected)


def test_an_existing_bucket_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, fresh_bucket_checks: object
) -> None:
    """HeadBucket succeeds → no create, and the check is not repeated in this process."""
    _creating_settings(monkeypatch)
    client = MagicMock()

    s3_logging.ensure_bucket_exists(client, "btkplatform", "us-east-1")
    s3_logging.ensure_bucket_exists(client, "btkplatform", "us-east-1")

    client.head_bucket.assert_called_once_with(Bucket="btkplatform")
    client.create_bucket.assert_not_called()


def test_a_forbidden_bucket_is_never_created_over(
    monkeypatch: pytest.MonkeyPatch,
    fresh_bucket_checks: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """HeadBucket 403 means the name exists (someone else's, or unreadable): no create."""
    _creating_settings(monkeypatch)
    client = MagicMock()
    client.head_bucket.side_effect = _client_error("403", "HeadBucket")

    with caplog.at_level(logging.WARNING, logger=s3_logging.__name__):
        s3_logging.ensure_bucket_exists(client, "btkplatform", "us-east-1")

    client.create_bucket.assert_not_called()
    assert "HeadBucket answered 403" in caplog.text


def test_nothing_is_checked_when_the_setting_is_off(
    monkeypatch: pytest.MonkeyPatch, fresh_bucket_checks: object
) -> None:
    """With the flag off the app never calls HeadBucket or CreateBucket."""
    _creating_settings(monkeypatch, create=False)
    client = MagicMock()

    s3_logging.ensure_bucket_exists(client, "btkplatform", "us-east-1")

    client.head_bucket.assert_not_called()
    client.create_bucket.assert_not_called()


def test_a_failed_create_never_raises_and_is_retried(
    monkeypatch: pytest.MonkeyPatch,
    fresh_bucket_checks: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A refused CreateBucket is logged, not raised, and the next write checks again."""
    _creating_settings(monkeypatch)
    client = MagicMock()
    client.head_bucket.side_effect = _client_error("404", "HeadBucket")
    client.create_bucket.side_effect = _client_error("AccessDenied", "CreateBucket")

    with caplog.at_level(logging.WARNING, logger=s3_logging.__name__):
        s3_logging.ensure_bucket_exists(client, "btkplatform", "us-east-1")
        s3_logging.ensure_bucket_exists(client, "btkplatform", "us-east-1")

    assert client.create_bucket.call_count == 2
    assert "could not be created" in caplog.text


def test_a_bucket_created_meanwhile_by_this_account_counts_as_created(
    monkeypatch: pytest.MonkeyPatch, fresh_bucket_checks: object
) -> None:
    """Two processes racing to create: BucketAlreadyOwnedByYou is success, not a retry."""
    _creating_settings(monkeypatch)
    client = MagicMock()
    client.head_bucket.side_effect = _client_error("404", "HeadBucket")
    client.create_bucket.side_effect = _client_error(
        "BucketAlreadyOwnedByYou", "CreateBucket"
    )

    s3_logging.ensure_bucket_exists(client, "btkplatform", "us-east-1")
    s3_logging.ensure_bucket_exists(client, "btkplatform", "us-east-1")

    client.create_bucket.assert_called_once()


def test_the_handler_creates_the_bucket_before_its_first_upload(
    monkeypatch: pytest.MonkeyPatch, fresh_bucket_checks: object
) -> None:
    """The log handler's first client checks the bucket, then puts the batch into it."""
    _creating_settings(monkeypatch)
    client = MagicMock()
    client.head_bucket.side_effect = _client_error("404", "HeadBucket")
    monkeypatch.setattr(s3_logging, "_create_s3_client", lambda *a, **k: client)

    handler = S3LogHandler(bucket="btkplatform", region="us-east-1")
    try:
        dt = datetime(2026, 9, 15, 9, 0, 0, tzinfo=APP_TIMEZONE)
        handler._upload(
            "warning", [{"timestamp": dt.isoformat(), "log_type": "warning"}]
        )
    finally:
        handler.close()

    calls = [name for name, _args, _kwargs in client.method_calls]
    assert calls == ["head_bucket", "create_bucket", "put_object"]
