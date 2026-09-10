"""Structured logging configuration.

``setup_logging()`` configures the root logger with a human-readable console handler
(always on) and, when ``AWS_S3_LOGGING_ENABLED=true`` and a bucket is set, an
``S3LogHandler`` that ships warn+ records to S3. A ``RequestContextFilter`` is attached
so every record carries request context. Ported and adapted from the ``maps`` project.
"""

from __future__ import annotations

import logging
import sys

from src.core.config import get_settings
from src.core.request_logging import RequestContextFilter
from src.core.s3_logging import create_s3_handler_if_enabled


def setup_logging() -> None:
    """Configure the root logger: console always, S3 when enabled."""
    cfg = get_settings()
    level = getattr(logging, str(cfg.log_level).upper(), logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(
        logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    # The context filter is attached at the handler level so it also injects request
    # context onto records that propagate up from module loggers (not just root).
    console.addFilter(RequestContextFilter())
    root.addHandler(console)

    s3_handler = create_s3_handler_if_enabled()
    if s3_handler is not None:
        s3_min = getattr(logging, cfg.aws_s3_log_level.value, logging.WARNING)
        s3_handler.setLevel(s3_min)
        s3_handler.addFilter(RequestContextFilter())
        root.addHandler(s3_handler)

    # Keep third-party loggers readable and quiet the S3 client's own retry noise
    # (otherwise botocore/urllib3 warnings could feed back into the S3 handler).
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("gunicorn").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
