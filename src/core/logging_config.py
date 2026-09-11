"""Structured logging configuration (Issue 6).

``setup_logging()`` configures the root logger with a console handler (always on) and, when
``AWS_S3_LOGGING_ENABLED=true`` and a bucket is set, an ``S3LogHandler`` that ships warn+ records
to S3. Two filters sit on **every** handler, so they apply to records from any module:

1. :class:`~src.core.request_logging.RequestContextFilter` adds the request's id, method, path,
   client IP, site and actor;
2. :class:`~src.core.log_redaction.RedactionFilter` then masks phone numbers, OTPs and tokens,
   in the message, the extra fields and the traceback alike.

The console writes one JSON object per line (``LOG_FORMAT=json``, the default): newline-delimited
JSON a log pipeline and ``jq`` parse without a grok pattern. ``LOG_FORMAT=text`` keeps the
kernel's readable line for a developer who prefers it; the same filters run first either way.
Ported from the ``maps`` project and adapted.
"""

import json
import logging
import sys
from datetime import datetime
from typing import Any, Final

from src.commons.enums import LogFormat
from src.commons.time import APP_TIMEZONE
from src.core.config import get_settings
from src.core.log_redaction import RedactionFilter
from src.core.request_logging import RequestContextFilter
from src.core.s3_logging import create_s3_handler_if_enabled

#: The request context fields, grouped under ``context`` in the JSON line. Always present (null
#: until known), so a query such as ``jq 'select(.context.site_id == "...")'`` needs no guard.
CONTEXT_FIELDS: Final = (
    "request_id",
    "site_id",
    "actor_id",
    "method",
    "path",
    "client_ip",
)

#: ``LogRecord`` attributes the logging module owns; the rest of a record is the caller's extras.
_RECORD_ATTRS: Final = frozenset(vars(logging.makeLogRecord({}))) | {
    "message",
    "asctime",
}


def _json_default(value: object) -> str:
    """Serialise what ``json`` cannot (datetimes, enums, ids) as text."""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class JsonFormatter(logging.Formatter):
    """One record as one JSON object on one line.

    ``timestamp`` (Africa/Johannesburg, ISO 8601 with offset), ``level``, ``logger``, ``message``,
    ``context`` (the request fields) and ``extra`` (anything else passed with ``extra=``), plus
    ``exception`` when there is a traceback. Never raises: a value JSON cannot hold is written as
    its ``str``.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as a single JSON line."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=APP_TIMEZONE
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "context": {name: getattr(record, name, None) for name in CONTEXT_FIELDS},
        }
        extra = {
            name: value
            for name, value in vars(record).items()
            if name not in _RECORD_ATTRS
            and name not in CONTEXT_FIELDS
            and not name.startswith("_")
        }
        if extra:
            payload["extra"] = extra
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            payload["exception"] = record.exc_text
        if record.stack_info:
            payload["stack"] = record.stack_info
        return json.dumps(payload, default=_json_default, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """The kernel's readable line, with the request id appended when there is one."""

    def __init__(self) -> None:
        super().__init__(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as text; append ``[request_id]`` inside a request."""
        line = super().format(record)
        request_id = getattr(record, "request_id", None)
        return f"{line} [{request_id}]" if request_id else line


def build_console_handler(
    log_format: LogFormat, level: int, stream: Any = None
) -> logging.Handler:
    """Return the console handler: context and redaction filters, then the chosen formatter.

    Filters run in the order added, so the context is attached before redaction reads it.
    ``stream`` defaults to stdout; tests pass their own to read what was written.
    """
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(
        JsonFormatter() if log_format is LogFormat.JSON else TextFormatter()
    )
    handler.addFilter(RequestContextFilter())
    handler.addFilter(RedactionFilter())
    return handler


def setup_logging() -> None:
    """Configure the root logger: the console always, S3 when enabled."""
    cfg = get_settings()
    level = logging.getLevelNamesMapping().get(cfg.log_level.value, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(build_console_handler(cfg.log_format, level))

    s3_handler = create_s3_handler_if_enabled()
    if s3_handler is not None:
        s3_min = logging.getLevelNamesMapping().get(
            cfg.aws_s3_log_level.value, logging.WARNING
        )
        s3_handler.setLevel(s3_min)
        s3_handler.addFilter(RequestContextFilter())
        s3_handler.addFilter(RedactionFilter())
        root.addHandler(s3_handler)

    # Keep third-party loggers readable and quiet the S3 client's own retry noise
    # (otherwise botocore/urllib3 warnings could feed back into the S3 handler).
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("gunicorn").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    # The readiness probe opens an Alembic migration context on every poll; its two INFO lines
    # ("Context impl …", "Will assume transactional DDL") would otherwise repeat every few seconds.
    logging.getLogger("alembic").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
