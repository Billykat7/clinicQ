"""Read-side queries for structured application logs in S3 (Issue #8).

Uses the Issue #7 key layout::

    {slug}/{env}/logs/{log_type}/{api|web|worker}/{YYYY}/{MM}/{DD}/{slug}-{ccyymmdd}-{HHMMSS}.json

The bucket is shared with sibling projects, so every listing and read stays under this project's
``{slug}/{env}/logs/`` prefix.

Server-side prefix listing plus an optional in-object keyword match; caps are
enforced to avoid unbounded S3 scans (see module constants). Ported and adapted
from the ``maps`` project (``src/core/s3_logs_query.py``).
"""

from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.commons.enums import S3LogListingLevel, S3LogPath
from src.core.config import get_settings
from src.core.s3_logging import get_s3_logs_client

_LOGS_LIST_RESUME_CURSOR_PREFIX = "bkclinicq_logs_v1:"

# Performance limits (documented in the PR / OpenAPI descriptions).
S3_LOG_LIST_MAX_KEYS_PER_REQUEST = 40
"""Max keys per S3 ListObjectsV2 call (keeps pagination predictable)."""

S3_LOG_LIST_MAX_LIST_ITERATIONS = 60
"""Max ListObjectsV2 calls per request when filtering narrows results."""

S3_LOG_KEYWORD_MAX_OBJECTS_FETCH = 100
"""Max GetObject calls per request when a keyword filter is active."""

S3_LOG_KEYWORD_MAX_BYTES = 512_000
"""Max bytes read per object when scanning for a keyword."""

_LOG_TYPE_SEGMENTS = frozenset({"info", "warning", "error"})
_PATH_SEGMENTS = frozenset(p.value for p in S3LogPath)


@dataclass(frozen=True)
class ParsedS3LogKey:
    """Segments parsed from a well-formed Issue #7 log object key."""

    project: str
    env: str
    level: str
    path: str
    year: int
    month: int
    day: int


def parse_s3_log_object_key(key: str) -> ParsedS3LogKey | None:
    """Parse ``{slug}/{env}/logs/{log_type}/{path}/{YYYY}/{MM}/{DD}/filename`` into fields.

    Returns ``None`` if the key does not match the expected layout.
    """
    parts = key.split("/")
    if len(parts) < 9:
        return None
    if parts[2] != "logs":
        return None
    project, env, level, path_seg = parts[0], parts[1], parts[3], parts[4]
    if path_seg not in _PATH_SEGMENTS:
        return None
    try:
        year, month, day = int(parts[5]), int(parts[6]), int(parts[7])
    except ValueError:
        return None
    return ParsedS3LogKey(
        project=project,
        env=env,
        level=level,
        path=path_seg,
        year=year,
        month=month,
        day=day,
    )


def build_s3_log_list_item(
    key: str,
    size: int,
    last_modified: str | None,
) -> dict[str, Any]:
    """Build one API listing row dict with parsed segments from a log object key.

    ``calendar_date`` is ``YYYY-MM-DD`` from the path when the key matches the layout.
    """
    parsed = parse_s3_log_object_key(key)
    file_name = key.rsplit("/", 1)[-1] if key else ""
    if parsed is not None:
        calendar_date = f"{parsed.year:04d}-{parsed.month:02d}-{parsed.day:02d}"
        return {
            "key": key,
            "file_name": file_name,
            "log_level": parsed.level,
            "path_segment": parsed.path,
            "calendar_date": calendar_date,
            "size": size,
            "last_modified": last_modified,
        }
    return {
        "key": key,
        "file_name": file_name,
        "log_level": "",
        "path_segment": None,
        "calendar_date": None,
        "size": size,
        "last_modified": last_modified,
    }


def key_matches_filters(
    key: str,
    *,
    project: str,
    env: str,
    level: S3LogListingLevel | None,
    path_filter: S3LogPath | None,
    year: int,
    month: int | None,
    day: int | None,
) -> bool:
    """Return True if ``key`` matches project, env, optional level, optional path, and date parts.

    When ``level`` is None, any of info / warning / error is accepted.
    """
    parsed = parse_s3_log_object_key(key)
    if parsed is None:
        return False
    if parsed.project != project or parsed.env != env:
        return False
    if parsed.level not in _LOG_TYPE_SEGMENTS:
        return False
    if level is not None and parsed.level != level.value:
        return False
    if path_filter is not None and parsed.path != path_filter.value:
        return False
    if parsed.year != year:
        return False
    if month is not None and parsed.month != month:
        return False
    return day is None or parsed.day == day


def _is_safe_logs_key(key: str, logs_prefix: str) -> bool:
    """Restrict GetObject to keys under this project's env logs prefix (never a sibling's)."""
    return key.startswith(logs_prefix) and ".." not in key


def _encode_s3_logs_resume_cursor(last_examined_key: str) -> str:
    """Encode a cursor so the next list request resumes after ``last_examined_key``.

    Used when a page fills before the current S3 ``Contents`` batch is exhausted;
    returning only ``NextContinuationToken`` would skip unprocessed keys still in
    that batch.
    """
    payload = json.dumps({"k": last_examined_key}, separators=(",", ":"))
    b = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return _LOGS_LIST_RESUME_CURSOR_PREFIX + b


def _decode_s3_logs_continuation_token(
    token: str | None,
) -> tuple[str | None, str | None]:
    """Split a client ``continuation_token`` into (aws_continuation_token, resume_after_key).

    For the first ListObjectsV2 call of a request, use ``StartAfter=resume_after_key``
    if set, otherwise ``ContinuationToken=aws_continuation_token``.
    """
    if not token:
        return (None, None)
    if not token.startswith(_LOGS_LIST_RESUME_CURSOR_PREFIX):
        return (token, None)
    raw = token[len(_LOGS_LIST_RESUME_CURSOR_PREFIX) :]
    pad = "=" * (-len(raw) % 4)
    try:
        data = base64.urlsafe_b64decode(raw + pad)
        obj = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError, ValueError, UnicodeDecodeError:
        return (token, None)
    k = obj.get("k")
    if isinstance(k, str) and k:
        return (None, k)
    return (None, None)


def _object_body_matches_keyword(
    client: Any, bucket: str, key: str, keyword: str
) -> bool:
    """Return True if object body contains ``keyword`` (case-insensitive), within size cap."""
    kw = keyword.strip().lower()
    if not kw:
        return True
    try:
        resp = client.get_object(Bucket=bucket, Key=key)
        body = resp["Body"].read(S3_LOG_KEYWORD_MAX_BYTES)
    except Exception:
        return False
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return False
    return kw in text.lower()


def list_s3_application_logs(
    *,
    level: S3LogListingLevel,
    path_filter: S3LogPath | None,
    year: int,
    month: int | None,
    day: int | None,
    keyword: str | None,
    continuation_token: str | None,
    page_size: int,
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """List S3 log objects matching filters, within the listing order.

    Returns ``(items, next_continuation_token, error_message)``. ``error_message`` is
    set when the bucket is not configured or S3 is unavailable; ``items`` may still be
    empty.

    ``next_continuation_token`` is either S3's opaque token or an app-encoded cursor
    (prefix ``bkclinicq_logs_v1:``) when the page filled mid-batch; pass it back
    unchanged on the next request. ``page_size`` caps how many objects are returned.
    """
    cfg = get_settings()
    bucket = (cfg.aws_s3_bucket or "").strip()
    logs_prefix = cfg.s3_prefix("logs")
    if not bucket:
        return [], None, "S3 bucket is not configured; log listing is unavailable."

    client = get_s3_logs_client()
    if client is None:
        return [], None, "Could not create S3 client; log listing is unavailable."

    if level == S3LogListingLevel.ALL:
        prefix = logs_prefix
        level_for_match: S3LogListingLevel | None = None
    else:
        prefix = f"{logs_prefix}{level.value}/"
        level_for_match = level
    items: list[dict[str, Any]] = []
    incoming_aws_token, resume_after_key = _decode_s3_logs_continuation_token(
        continuation_token
    )
    list_token: str | None = None
    first_list_request = True
    list_calls = 0
    keyword_clean = (keyword or "").strip()
    fetches_done = 0
    page_cap = max(1, min(page_size, 1000))
    list_batch_keys = min(max(S3_LOG_LIST_MAX_KEYS_PER_REQUEST, page_cap), 1000)
    keyword_fetch_cap = min(page_cap, S3_LOG_KEYWORD_MAX_OBJECTS_FETCH)

    while len(items) < page_cap and list_calls < S3_LOG_LIST_MAX_LIST_ITERATIONS:
        list_calls += 1
        kwargs: dict[str, Any] = {
            "Bucket": bucket,
            "Prefix": prefix,
            "MaxKeys": list_batch_keys,
        }
        if first_list_request:
            if resume_after_key:
                kwargs["StartAfter"] = resume_after_key
            elif incoming_aws_token:
                kwargs["ContinuationToken"] = incoming_aws_token
            first_list_request = False
        elif list_token:
            kwargs["ContinuationToken"] = list_token
        try:
            resp = client.list_objects_v2(**kwargs)
        except Exception:
            return (
                [],
                None,
                "S3 list operation failed; check credentials and bucket permissions.",
            )

        contents = resp.get("Contents") or []
        contents_exhausted = True
        last_examined_key: str | None = None
        for obj in contents:
            if len(items) >= page_cap:
                contents_exhausted = False
                break
            key = obj.get("Key") or ""
            if key:
                last_examined_key = key
            if not key or not key.endswith(".json"):
                continue
            if not key_matches_filters(
                key,
                project=cfg.project_slug,
                env=cfg.s3_environment,
                level=level_for_match,
                path_filter=path_filter,
                year=year,
                month=month,
                day=day,
            ):
                continue
            if keyword_clean:
                if fetches_done >= keyword_fetch_cap:
                    continue
                fetches_done += 1
                if not _object_body_matches_keyword(client, bucket, key, keyword_clean):
                    continue

            lm = obj.get("LastModified")
            last_modified = lm.isoformat() if isinstance(lm, datetime) else None
            items.append(
                {
                    "key": key,
                    "size": int(obj.get("Size") or 0),
                    "last_modified": last_modified,
                }
            )

        if len(items) >= page_cap:
            if not contents_exhausted and last_examined_key:
                return (
                    items,
                    _encode_s3_logs_resume_cursor(last_examined_key),
                    None,
                )
            next_tok = resp.get("NextContinuationToken")
            return items, next_tok, None

        if not resp.get("IsTruncated"):
            return items, None, None
        list_token = resp.get("NextContinuationToken")
        if not list_token:
            return items, None, None

    # Still truncated but hit the iteration cap.
    return items, list_token, None


# --------------------------------------------------------------------------------------
# Full listing + cache for numbered pagination (Issue: logs viewer pagination).
#
# S3 ListObjectsV2 is continuation-based (forward-only), which can't back true numbered pages.
# For a bounded set (a day's log objects), we list the *whole* matching set once, cache it briefly,
# and serve any page as an ``offset:limit`` slice of the cached list. A background warm (kicked off
# when a permitted user opens the logs page) fills the cache ahead of the first API call, so the
# list is ready to page the moment they click in.
# --------------------------------------------------------------------------------------

LOGS_LISTING_MAX_OBJECTS = 2000
"""Hard cap on how many log objects one full listing collects (protects memory / S3 cost)."""

_LOGS_LISTING_FETCH_PAGE = 200
"""Objects requested per underlying ListObjectsV2 pass while collecting the full set."""

LOGS_CACHE_TTL_SECONDS = 60
"""How long a full listing stays fresh in the in-process cache before a re-fetch."""

_LOGS_CACHE: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]], str | None]] = {}
_LOGS_CACHE_LOCK = threading.Lock()


def clear_logs_cache() -> None:
    """Drop all cached listings (used by tests that reconfigure the S3 mock between cases)."""
    with _LOGS_CACHE_LOCK:
        _LOGS_CACHE.clear()


def list_all_s3_application_logs(
    *,
    level: S3LogListingLevel,
    path_filter: S3LogPath | None,
    year: int,
    month: int | None,
    day: int | None,
    keyword: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Collect the *whole* matching set of log objects (all continuation pages), newest first.

    Returns ``(items, error_message)``. Reuses the paged :func:`list_s3_application_logs` in a loop,
    following its continuation token until the listing is exhausted or ``LOGS_LISTING_MAX_OBJECTS``
    is reached, then sorts by ``last_modified`` descending. ``items`` are the raw
    ``{key,size,last_modified}`` rows (the API layer builds display rows from the page slice).
    """
    items: list[dict[str, Any]] = []
    token: str | None = None
    err: str | None = None
    # A generous iteration bound; each pass collects up to _LOGS_LISTING_FETCH_PAGE rows.
    for _ in range(LOGS_LISTING_MAX_OBJECTS // _LOGS_LISTING_FETCH_PAGE + 2):
        page, next_token, page_err = list_s3_application_logs(
            level=level,
            path_filter=path_filter,
            year=year,
            month=month,
            day=day,
            keyword=keyword,
            continuation_token=token,
            page_size=_LOGS_LISTING_FETCH_PAGE,
        )
        if page_err:
            err = page_err
            break
        items.extend(page)
        if len(items) >= LOGS_LISTING_MAX_OBJECTS or not next_token:
            break
        token = next_token

    items.sort(key=lambda it: it.get("last_modified") or "", reverse=True)
    return items[:LOGS_LISTING_MAX_OBJECTS], err


def _logs_cache_key(
    level: S3LogListingLevel,
    path_filter: S3LogPath | None,
    year: int,
    month: int | None,
    day: int | None,
    keyword: str | None,
) -> tuple[Any, ...]:
    return (
        level.value,
        path_filter.value if path_filter is not None else None,
        year,
        month,
        day,
        (keyword or "").strip().lower(),
    )


def get_or_fetch_logs_listing(
    *,
    level: S3LogListingLevel,
    path_filter: S3LogPath | None,
    year: int,
    month: int | None,
    day: int | None,
    keyword: str | None,
    force: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    """Return the full listing for these filters from the cache, fetching it if cold/stale.

    A fresh cache entry (< ``LOGS_CACHE_TTL_SECONDS`` old) is served without touching S3, so paging
    through the numbered pages is instant. Only successful listings are cached; an error result is
    returned but not stored, so the next call retries.
    """
    key = _logs_cache_key(level, path_filter, year, month, day, keyword)
    now = time.monotonic()
    if not force:
        with _LOGS_CACHE_LOCK:
            hit = _LOGS_CACHE.get(key)
        if hit is not None and now - hit[0] < LOGS_CACHE_TTL_SECONDS:
            return hit[1], hit[2]

    items, err = list_all_s3_application_logs(
        level=level,
        path_filter=path_filter,
        year=year,
        month=month,
        day=day,
        keyword=keyword,
    )
    if err is None:
        with _LOGS_CACHE_LOCK:
            _LOGS_CACHE[key] = (now, items, err)
    return items, err


def warm_logs_listing(
    *,
    level: S3LogListingLevel,
    path_filter: S3LogPath | None,
    year: int,
    month: int | None,
    day: int | None,
    keyword: str | None,
) -> None:
    """Best-effort background warm of the cache (fire-and-forget; never raises to the caller)."""
    try:
        get_or_fetch_logs_listing(
            level=level,
            path_filter=path_filter,
            year=year,
            month=month,
            day=day,
            keyword=keyword,
        )
    except Exception:
        return


def get_s3_log_object_text(key: str) -> tuple[str | None, str | None]:
    """Fetch UTF-8 text for a log object key. Validates key is under ``{slug}/{env}/logs/``.

    Returns ``(text, error_message)``.
    """
    cfg = get_settings()
    bucket = (cfg.aws_s3_bucket or "").strip()
    if not bucket:
        return None, "S3 bucket is not configured."
    if not _is_safe_logs_key(key, cfg.s3_prefix("logs")):
        return None, "Invalid log object key."
    client = get_s3_logs_client()
    if client is None:
        return None, "Could not create S3 client."
    try:
        resp = client.get_object(Bucket=bucket, Key=key)
        body = resp["Body"].read()
    except Exception as e:
        return None, f"Failed to read object: {e!s}"
    try:
        return body.decode("utf-8", errors="replace"), None
    except Exception:
        return None, "Failed to decode object body."
