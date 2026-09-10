"""Admin-only API routes gated by RBAC (Issue #8).

Read side of the S3 logging pipeline (Issue #7): let admins browse structured log
objects written to S3 and read a single object's contents. Both endpoints live under
``/admin/logs`` and require the ``logs`` READ verb (``LogsReadDep``). Ported and adapted
from the ``maps`` project (``src/api/v1/routes/admin.py``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.api.rbac_deps import require
from src.commons.enums import GrantScope, LogsPageSize, S3LogListingLevel, S3LogPath
from src.core.config import get_settings
from src.core.s3_logging import APP_TIMEZONE
from src.core.s3_logs_query import (
    build_s3_log_list_item,
    get_or_fetch_logs_listing,
    get_s3_log_object_text,
)

router = APIRouter(prefix="/admin", tags=["admin"])

# Issue #152: resolved through the generic ``require`` factory (src.api.rbac_deps) against the
# ``logs`` manifest (src/api/v1/routes/rbac_manifest.py) instead of the named
# ``require_logs_read`` function — mirrors the Applications pilot (Issue #149). Other routers that
# also gate on ``logs`` (audit, reporting, notifications) declare their own identical local alias
# rather than import this one, matching how each module declares its own aliases.
# Issue #166 (M28): ``business`` tier — this backs a whole-business back-office console,
# and its nav destination declares the same tier, a parity
# ``tests/unit/security/test_nav_enforcement_parity.py`` now asserts. No seeded role loses
# access: every role that holds this grant holds it at ``business`` already.
LogsReadDep = Annotated[
    None, Depends(require("logs", "read", scope=GrantScope.BUSINESS))
]

_MAX_OBJECT_BODY = 400_000


class S3LogObjectItem(BaseModel):
    """One S3 object in the log listing."""

    key: str = Field(description="Full S3 object key (Issue #7 layout).")
    file_name: str = Field(default="", description="Final path segment (filename).")
    log_level: str = Field(
        default="",
        description="Parsed log type segment (info, warning, error) when the key matches.",
    )
    path_segment: str | None = Field(
        default=None,
        description="api / web / worker segment from the key when parsed.",
    )
    calendar_date: str | None = Field(
        default=None,
        description="YYYY-MM-DD from the key path when parsed.",
    )
    size: int = Field(description="Object size in bytes.")
    last_modified: str | None = Field(
        default=None,
        description="S3 Last-Modified (ISO 8601) when available.",
    )


class AdminLogsListOut(BaseModel):
    """Paginated S3 log objects matching filters."""

    scope: str = Field(default="logs", description="RBAC scope for this route family.")
    bucket: str = Field(
        default="", description="Configured S3 bucket name (empty if unset)."
    )
    prefix_used: str = Field(
        default="",
        description="Server-side prefix passed to ListObjectsV2 (env/logs/level/).",
    )
    items: list[S3LogObjectItem] = Field(default_factory=list)
    page_size: int = Field(
        default=10,
        description="Number of objects returned for this page (10, 25, 50, or 100).",
    )
    offset: int = Field(
        default=0, description="Row offset of this page into the full matching listing."
    )
    total: int = Field(
        default=0,
        description="Total objects matching the filters (drives numbered pagination).",
    )
    message: str | None = Field(
        default=None,
        description="Human-readable note (e.g. S3 unavailable or bucket not configured).",
    )


class AdminLogObjectOut(BaseModel):
    """UTF-8 body of a single log object (NDJSON / JSON lines)."""

    key: str
    content: str
    truncated: bool = Field(
        default=False,
        description="True when the response capped content length for safety.",
    )


@router.get(
    "/logs",
    response_model=AdminLogsListOut,
    status_code=status.HTTP_200_OK,
    responses={
        403: {"description": "Insufficient permission (requires logs READ or higher)."},
        401: {"description": "Not authenticated."},
    },
    summary="List S3 application logs",
    description=(
        "Lists structured log objects under "
        "``{env}/logs/{level}/{api|web|worker}/{YYYY}/{MM}/{DD}/``. "
        "Uses S3 prefix listing plus an optional in-object keyword match. "
        "Performance caps live in ``src.core.s3_logs_query`` (max keys per list, "
        "max GetObject calls when a keyword is set). "
        "Example prefix for dev, error level: ``dev/logs/error/``. "
        "With date 2026-07-09, keys must include ``.../api/2026/07/09/``."
    ),
)
async def admin_logs_list(
    _rbac: LogsReadDep,
    level: Annotated[
        S3LogListingLevel,
        Query(
            description="Log type: all ({env}/logs/), or the info / warning / error segment.",
        ),
    ] = S3LogListingLevel.ALL,
    path: Annotated[
        S3LogPath | None,
        Query(description="Restrict to api / web / worker path segment; omit for all."),
    ] = None,
    year: Annotated[
        int | None,
        Query(
            ge=2000,
            le=2100,
            description="Calendar year (default: today, Johannesburg).",
        ),
    ] = None,
    month: Annotated[
        int | None,
        Query(ge=1, le=12, description="Month 1–12 (default: today)."),
    ] = None,
    day: Annotated[
        int | None,
        Query(ge=1, le=31, description="Day of month (default: today)."),
    ] = None,
    keyword: Annotated[
        str | None,
        Query(
            description="Optional case-insensitive substring match on object body "
            "(NDJSON); capped scans per request.",
        ),
    ] = None,
    offset: Annotated[
        int,
        Query(
            ge=0, description="Row offset into the full listing (numbered pagination)."
        ),
    ] = 0,
    page_size: Annotated[
        LogsPageSize,
        Query(description="Max objects per page (10, 25, 50, or 100)."),
    ] = LogsPageSize.XS,
) -> AdminLogsListOut:
    """List application logs stored in S3 for the current deployment environment.

    Date defaults follow the business timezone (Africa/Johannesburg).
    """
    now = datetime.now(APP_TIMEZONE)
    all_omitted = year is None and month is None and day is None
    if all_omitted:
        y = now.year
        month_f: int | None = now.month
        day_f: int | None = now.day
    else:
        y = year if year is not None else now.year
        month_f = month
        day_f = day

    # The S3 listing is synchronous boto3 I/O; running it inline would block the event loop for the
    # whole round-trip. Offload to a worker thread. The full matching set is listed once and cached
    # (a background warm on the logs page fills the cache ahead of this call), so each numbered page
    # is just a slice of the cached, already-newest-first listing — no per-page S3 round-trip.
    all_items, err = await asyncio.to_thread(
        get_or_fetch_logs_listing,
        level=level,
        path_filter=path,
        year=y,
        month=month_f,
        day=day_f,
        keyword=keyword,
    )

    cfg = get_settings()
    bucket = (cfg.aws_s3_bucket or "").strip()
    env = cfg.s3_environment
    prefix_used = (
        f"{env}/logs/"
        if level == S3LogListingLevel.ALL
        else f"{env}/logs/{level.value}/"
    )

    limit = int(page_size.value)
    total = len(all_items)
    page = all_items[offset : offset + limit]
    items = [
        S3LogObjectItem(
            **build_s3_log_list_item(x["key"], x["size"], x["last_modified"])
        )
        for x in page
    ]

    return AdminLogsListOut(
        bucket=bucket,
        prefix_used=prefix_used,
        items=items,
        page_size=limit,
        offset=offset,
        total=total,
        message=err,
    )


@router.get(
    "/logs/object",
    response_model=AdminLogObjectOut,
    status_code=status.HTTP_200_OK,
    responses={
        403: {"description": "Insufficient permission (requires logs READ or higher)."},
        401: {"description": "Not authenticated."},
        400: {"description": "Invalid key or read failed."},
    },
    summary="Fetch one S3 log object body",
)
async def admin_logs_object(
    _rbac: LogsReadDep,
    key: Annotated[
        str, Query(min_length=1, description="S3 object key under {env}/logs/.")
    ],
) -> AdminLogObjectOut:
    """Return UTF-8 text for one log object (for the inline / JSON viewer)."""
    # Blocking boto3 GetObject — offload to a thread so the event loop keeps serving
    # other requests while the object downloads (see ``admin_logs_list``).
    text, err = await asyncio.to_thread(get_s3_log_object_text, key)
    if err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
    assert text is not None
    truncated = len(text) > _MAX_OBJECT_BODY
    content = text[:_MAX_OBJECT_BODY] if truncated else text
    return AdminLogObjectOut(key=key, content=content, truncated=truncated)
