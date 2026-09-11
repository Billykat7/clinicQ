"""Identifiers (Issue 4): URL-safe, sortable by creation time, unique."""

import uuid
from datetime import timedelta
from urllib.parse import quote

import pytest

from src.commons.ids import ID_UUID_VERSION, id_created_at, new_id
from src.commons.time import APP_TIMEZONE, now_sast


def test_an_id_is_a_canonical_uuidv7_string() -> None:
    """36 characters, parses as a UUID of version 7, and fits the kernel's ``String(36)`` keys."""
    identifier = new_id()
    assert len(identifier) == 36
    assert uuid.UUID(identifier).version == ID_UUID_VERSION == 7
    assert identifier == identifier.lower()


def test_an_id_needs_no_escaping_in_a_url() -> None:
    """Percent-encoding with nothing marked safe changes nothing: every character is unreserved."""
    identifier = new_id()
    assert quote(identifier, safe="") == identifier


def test_ids_sort_in_the_order_they_were_made() -> None:
    """Made in a tight loop (many per millisecond), the ids are already in sorted order."""
    ids = [new_id() for _ in range(10_000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_an_id_carries_its_creation_time_in_sast() -> None:
    """The embedded timestamp is now, to the millisecond, in the app zone."""
    before = now_sast()
    created = id_created_at(new_id())
    after = now_sast()
    assert created.tzinfo is APP_TIMEZONE
    assert before - timedelta(milliseconds=1) <= created <= after


def test_a_kernel_uuid4_has_no_creation_time() -> None:
    """A random UUIDv4 (the kernel's older ids) carries no timestamp, and says so."""
    with pytest.raises(ValueError, match="version 4"):
        id_created_at(str(uuid.uuid4()))
