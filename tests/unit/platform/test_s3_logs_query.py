"""Unit tests for the S3 log read-side query core (Issue #8).

Cover the essential, non-trivial logic of ``src.core.s3_logs_query`` in isolation
(no network, no real AWS): parsing an Issue #7 object key, filter matching, listing-row
shaping, the key-safety guard on single-object reads, keyword body matching, and the
pagination/resume-cursor behaviour that keeps a filtered listing from skipping keys.

Per the testing-strategy Cursor rule these build isolated settings and point
``s3_logs_query.get_settings`` / ``get_s3_logs_client`` at test doubles so a developer's
local ``.env`` and real AWS credentials cannot change outcomes. boto3 is mocked
throughout.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.commons.enums import S3LogListingLevel, S3LogPath
from src.core import s3_logs_query
from src.core.s3_logs_query import (
    build_s3_log_list_item,
    get_s3_log_object_text,
    key_matches_filters,
    list_s3_application_logs,
    parse_s3_log_object_key,
)

_RESUME_PREFIX = "bkclinicq_logs_v1:"


def _fake_settings(bucket: str, env: str, slug: str = "clinicq") -> SimpleNamespace:
    """The settings the query module reads: bucket, slug, env and the logs prefix they make."""
    return SimpleNamespace(
        aws_s3_bucket=bucket,
        project_slug=slug,
        s3_environment=env,
        s3_prefix=lambda kind: f"{slug}/{env}/{kind}/",
    )


def _point_at_s3(
    monkeypatch: pytest.MonkeyPatch,
    client: object,
    *,
    bucket: str = "test-bucket",
    env: str = "dev",
) -> None:
    """Point the query module at an isolated bucket/env and a mocked S3 client."""
    monkeypatch.setattr(
        s3_logs_query,
        "get_settings",
        lambda: _fake_settings(bucket, env),
    )
    monkeypatch.setattr(s3_logs_query, "get_s3_logs_client", lambda: client)


# --- key parsing ---------------------------------------------------------------


def test_parse_s3_log_object_key_valid() -> None:
    """Parses a well-formed Issue #7 object key into its segments."""
    key = "clinicq/dev/logs/error/api/2026/08/03/clinicq-20260803-120000.json"
    p = parse_s3_log_object_key(key)
    assert p is not None
    assert p.project == "clinicq"
    assert p.env == "dev"
    assert p.level == "error"
    assert p.path == "api"
    assert p.year == 2026
    assert p.month == 8
    assert p.day == 3


def test_parse_s3_log_object_key_invalid_short() -> None:
    """Keys with too few path segments are rejected."""
    assert parse_s3_log_object_key("clinicq/dev/logs/error/api/2026/08") is None


def test_parse_s3_log_object_key_rejects_unknown_path_segment() -> None:
    """A path segment outside api / web / worker does not parse."""
    key = "clinicq/dev/logs/error/database/2026/08/03/clinicq-20260803-120000.json"
    assert parse_s3_log_object_key(key) is None


# --- filter matching -----------------------------------------------------------


def test_key_matches_exact_day() -> None:
    """Level, path, and full date filters match; a wrong path is rejected."""
    key = "clinicq/prod/logs/warning/web/2025/12/01/clinicq-20251201-090000.json"
    assert key_matches_filters(
        key,
        project="clinicq",
        env="prod",
        level=S3LogListingLevel.WARNING,
        path_filter=S3LogPath.WEB,
        year=2025,
        month=12,
        day=1,
    )
    assert not key_matches_filters(
        key,
        project="clinicq",
        env="prod",
        level=S3LogListingLevel.WARNING,
        path_filter=S3LogPath.API,
        year=2025,
        month=12,
        day=1,
    )


def test_key_matches_month_wildcard() -> None:
    """When day is None, any day within the month matches."""
    key = "clinicq/dev/logs/info/api/2026/03/15/clinicq-20260315-090000.json"
    assert key_matches_filters(
        key,
        project="clinicq",
        env="dev",
        level=S3LogListingLevel.INFO,
        path_filter=None,
        year=2026,
        month=3,
        day=None,
    )


def test_key_matches_any_level_when_level_unspecified() -> None:
    """When ``level`` is None (ALL listing), info / warning / error keys all match."""
    for seg in ("error", "warning", "info"):
        key = f"clinicq/dev/logs/{seg}/api/2026/03/15/clinicq-20260315-090000.json"
        assert key_matches_filters(
            key,
            project="clinicq",
            env="dev",
            level=None,
            path_filter=S3LogPath.API,
            year=2026,
            month=3,
            day=15,
        )


def test_key_matches_rejects_a_sibling_projects_key() -> None:
    """The bucket is shared: another project's key with the same env and date never matches."""
    key = "properties/dev/logs/info/api/2026/03/15/properties-20260315-090000.json"
    assert not key_matches_filters(
        key,
        project="clinicq",
        env="dev",
        level=None,
        path_filter=None,
        year=2026,
        month=3,
        day=15,
    )


def test_parse_s3_log_object_key_rejects_the_old_unprefixed_layout() -> None:
    """A key written before the project slug led the layout does not parse."""
    assert (
        parse_s3_log_object_key(
            "dev/logs/error/api/2026/08/03/clinicq-20260803-120000.json"
        )
        is None
    )


def test_key_matches_rejects_wrong_env() -> None:
    """A key from a different environment prefix never matches."""
    key = "clinicq/prod/logs/info/api/2026/03/15/clinicq-20260315-090000.json"
    assert not key_matches_filters(
        key,
        project="clinicq",
        env="dev",
        level=None,
        path_filter=None,
        year=2026,
        month=3,
        day=15,
    )


# --- listing row shape ---------------------------------------------------------


def test_build_s3_log_list_item_parsed() -> None:
    """A listing row carries level, path, filename, and calendar date from the key."""
    key = "clinicq/dev/logs/warning/api/2026/04/06/clinicq-20260406-190946.json"
    row = build_s3_log_list_item(key, 1024, "2026-04-06T19:09:46+02:00")
    assert row["file_name"] == "clinicq-20260406-190946.json"
    assert row["log_level"] == "warning"
    assert row["path_segment"] == "api"
    assert row["calendar_date"] == "2026-04-06"
    assert row["size"] == 1024


def test_build_s3_log_list_item_unparsable_key() -> None:
    """An off-layout key still yields a row, with empty/None parsed fields."""
    row = build_s3_log_list_item("dev/misc/whatever.json", 7, None)
    assert row["file_name"] == "whatever.json"
    assert row["log_level"] == ""
    assert row["path_segment"] is None
    assert row["calendar_date"] is None


# --- single-object read safety -------------------------------------------------


def test_get_s3_log_object_text_rejects_bad_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keys outside ``{slug}/{env}/logs/`` are rejected without ever calling S3."""
    client = MagicMock()
    _point_at_s3(monkeypatch, client)
    text, err = get_s3_log_object_text("other-bucket/evil/path")
    assert text is None
    assert err is not None
    client.get_object.assert_not_called()


def test_get_s3_log_object_text_refuses_a_sibling_projects_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A well-formed log key of another project in the shared bucket is never read."""
    client = MagicMock()
    _point_at_s3(monkeypatch, client)
    text, err = get_s3_log_object_text(
        "umojanet/dev/logs/info/api/2026/08/03/umojanet-20260803-090000.json"
    )
    assert text is None
    assert err == "Invalid log object key."
    client.get_object.assert_not_called()


def test_list_prefixes_with_the_project_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Listing asks S3 only for this project's objects: ``{slug}/{env}/logs/{level}/``."""
    client = MagicMock()
    client.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}
    monkeypatch.setattr(
        s3_logs_query, "get_settings", lambda: _fake_settings("shared", "prod", "maps")
    )
    monkeypatch.setattr(s3_logs_query, "get_s3_logs_client", lambda: client)
    list_s3_application_logs(
        level=S3LogListingLevel.ERROR,
        path_filter=None,
        year=2026,
        month=8,
        day=3,
        keyword=None,
        continuation_token=None,
        page_size=10,
    )
    assert client.list_objects_v2.call_args.kwargs["Prefix"] == "maps/prod/logs/error/"


def test_get_s3_log_object_text_rejects_traversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key containing ``..`` is rejected even under the logs prefix."""
    client = MagicMock()
    _point_at_s3(monkeypatch, client)
    text, err = get_s3_log_object_text("clinicq/dev/logs/../../secrets.json")
    assert text is None
    assert err is not None
    client.get_object.assert_not_called()


def test_get_s3_log_object_text_reads_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid key returns the decoded UTF-8 body from GetObject."""
    body = b'{"message": "hello"}\n{"message": "world"}\n'
    client = MagicMock()
    client.get_object.return_value = {"Body": SimpleNamespace(read=lambda: body)}
    _point_at_s3(monkeypatch, client)

    key = "clinicq/dev/logs/info/api/2026/08/03/clinicq-20260803-090000.json"
    text, err = get_s3_log_object_text(key)

    assert err is None
    assert text is not None
    assert "hello" in text
    client.get_object.assert_called_once_with(Bucket="test-bucket", Key=key)


def test_get_s3_log_object_text_bucket_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no bucket configured, the read fails cleanly without touching S3."""
    client = MagicMock()
    _point_at_s3(monkeypatch, client, bucket="")
    text, err = get_s3_log_object_text("clinicq/dev/logs/info/api/x.json")
    assert text is None
    assert err is not None
    client.get_object.assert_not_called()


# --- keyword body matching -----------------------------------------------------


def test_list_keyword_filters_by_object_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A keyword is matched case-insensitively against each object body via GetObject."""
    lm = datetime(2026, 8, 3, 12, 0, 0, tzinfo=UTC)
    keys = [
        "clinicq/dev/logs/error/api/2026/08/03/hit.json",
        "clinicq/dev/logs/error/api/2026/08/03/miss.json",
    ]
    bodies = {
        keys[0]: b'{"message": "Database TIMEOUT while querying"}',
        keys[1]: b'{"message": "all good"}',
    }

    def get_object(**kwargs: object) -> dict:
        payload = bodies[str(kwargs["Key"])]
        return {"Body": SimpleNamespace(read=lambda _n=None: payload)}

    client = MagicMock()
    client.list_objects_v2.return_value = {
        "Contents": [{"Key": k, "Size": 1, "LastModified": lm} for k in keys],
        "IsTruncated": False,
    }
    client.get_object.side_effect = get_object
    _point_at_s3(monkeypatch, client)

    items, tok, err = list_s3_application_logs(
        level=S3LogListingLevel.ERROR,
        path_filter=None,
        year=2026,
        month=8,
        day=3,
        keyword="timeout",
        continuation_token=None,
        page_size=50,
    )

    assert err is None
    assert tok is None
    assert [it["key"] for it in items] == [keys[0]]


# --- listing / pagination ------------------------------------------------------


def test_list_returns_error_when_bucket_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no bucket configured, listing returns an explanatory message, not a crash."""
    _point_at_s3(monkeypatch, MagicMock(), bucket="")
    items, tok, err = list_s3_application_logs(
        level=S3LogListingLevel.ALL,
        path_filter=None,
        year=2026,
        month=8,
        day=3,
        keyword=None,
        continuation_token=None,
        page_size=50,
    )
    assert items == []
    assert tok is None
    assert err is not None


def test_list_s3_application_logs_resume_mid_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When page_size is reached before S3 ``Contents`` is exhausted, the next token
    resumes within the same logical listing (``StartAfter``) instead of skipping keys
    via ``NextContinuationToken`` alone.
    """
    keys = [
        f"clinicq/dev/logs/info/api/2026/03/15/properties-{i:02d}.json"
        for i in range(15)
    ]
    lm = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)

    def list_impl(**kwargs: object) -> dict:
        start_after = kwargs.get("StartAfter")
        continuation = kwargs.get("ContinuationToken")
        if start_after is not None:
            assert start_after == keys[4]
            assert continuation is None
            tail = [k for k in keys if k > str(start_after)]
            return {
                "Contents": [
                    {"Key": k, "Size": 1, "LastModified": lm} for k in tail[:40]
                ],
                "IsTruncated": False,
            }
        assert continuation is None
        return {
            "Contents": [{"Key": k, "Size": 1, "LastModified": lm} for k in keys],
            "IsTruncated": False,
        }

    client = MagicMock()
    client.list_objects_v2.side_effect = list_impl
    _point_at_s3(monkeypatch, client)

    items1, tok1, err1 = list_s3_application_logs(
        level=S3LogListingLevel.INFO,
        path_filter=None,
        year=2026,
        month=3,
        day=15,
        keyword=None,
        continuation_token=None,
        page_size=5,
    )
    assert err1 is None
    assert [it["key"] for it in items1] == keys[:5]
    assert tok1 is not None
    assert tok1.startswith(_RESUME_PREFIX)

    items2, tok2, err2 = list_s3_application_logs(
        level=S3LogListingLevel.INFO,
        path_filter=None,
        year=2026,
        month=3,
        day=15,
        keyword=None,
        continuation_token=tok1,
        page_size=5,
    )
    assert err2 is None
    assert [it["key"] for it in items2] == keys[5:10]
    assert tok2 is not None
    assert tok2.startswith(_RESUME_PREFIX)


def test_list_s3_application_logs_aws_continuation_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The opaque S3 ``NextContinuationToken`` is followed across truncated pages."""
    keys_page1 = [
        f"clinicq/dev/logs/info/api/2026/03/15/p1-{i:02d}.json" for i in range(3)
    ]
    keys_page2 = [
        f"clinicq/dev/logs/info/api/2026/03/15/p2-{i:02d}.json" for i in range(2)
    ]
    lm = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)

    def list_impl(**kwargs: object) -> dict:
        assert kwargs.get("StartAfter") is None
        continuation = kwargs.get("ContinuationToken")
        if continuation is None:
            return {
                "Contents": [
                    {"Key": k, "Size": 1, "LastModified": lm} for k in keys_page1
                ],
                "IsTruncated": True,
                "NextContinuationToken": "aws-page-2",
            }
        assert continuation == "aws-page-2"
        return {
            "Contents": [{"Key": k, "Size": 1, "LastModified": lm} for k in keys_page2],
            "IsTruncated": False,
        }

    client = MagicMock()
    client.list_objects_v2.side_effect = list_impl
    _point_at_s3(monkeypatch, client)

    items, tok, err = list_s3_application_logs(
        level=S3LogListingLevel.INFO,
        path_filter=None,
        year=2026,
        month=3,
        day=15,
        keyword=None,
        continuation_token=None,
        page_size=10,
    )
    assert err is None
    assert tok is None
    assert {it["key"] for it in items} == set(keys_page1 + keys_page2)
