"""The words the discovery list shows, decided as data (Issue 32).

These are the sentences a patient acts on, so each is pinned here rather than read back out of
rendered HTML (``.cursor/rules/testing-strategy.mdc``): an unknown queue is never "0 waiting", a wait
is a range or nothing, and "closed" always says when it opens.
"""

from datetime import datetime

import pytest

from src.commons.enums import SiteSector
from src.commons.time import APP_TIMEZONE
from src.modules.discovery.service import OpenStatus
from src.modules.queues.live import WaitRange
from src.web.discover import (
    QUEUE_NOT_REPORTED,
    SECTOR_BADGES,
    WAIT_NOT_AVAILABLE,
    open_label,
    queue_label,
    wait_label,
)

_TUESDAY_10AM = datetime(2026, 9, 15, 10, 0, tzinfo=APP_TIMEZONE)


def test_every_sector_has_a_badge_told_apart_without_colour() -> None:
    """Different words and different shapes, so greyscale and screen readers both tell them apart."""
    assert set(SECTOR_BADGES) == set(SiteSector)
    badges = list(SECTOR_BADGES.values())
    assert len({badge.label for badge in badges}) == len(badges)
    assert len({badge.shape for badge in badges}) == len(badges)
    assert all(badge.description for badge in badges)


@pytest.mark.parametrize(
    ("waiting", "label"),
    [
        (None, QUEUE_NOT_REPORTED),
        (0, "No one waiting"),
        (1, "1 person waiting"),
        (12, "12 people waiting"),
    ],
)
def test_an_unmeasured_queue_is_never_shown_as_empty(
    waiting: int | None, label: str
) -> None:
    """``None`` is "not reported yet"; only a counted zero is "No one waiting"."""
    assert queue_label(waiting) == label


def test_a_wait_is_a_range_or_nothing() -> None:
    """No estimate, or an estimate for only some queues, is not a figure to show."""
    assert wait_label([]) == WAIT_NOT_AVAILABLE
    assert wait_label([None, None]) == WAIT_NOT_AVAILABLE
    assert wait_label([WaitRange(5, 15), None]) == WAIT_NOT_AVAILABLE
    assert wait_label([WaitRange(5, 15), WaitRange(20, 40)]) == "Wait about 5–40 min"


@pytest.mark.parametrize(
    ("status", "label"),
    [
        (OpenStatus(True, _TUESDAY_10AM, None), "Open now"),
        (
            OpenStatus(False, _TUESDAY_10AM.replace(hour=14), None),
            "Closed, opens today at 14:00",
        ),
        (
            OpenStatus(False, datetime(2026, 9, 16, 7, 0, tzinfo=APP_TIMEZONE), None),
            "Closed, opens tomorrow at 07:00",
        ),
        (
            OpenStatus(False, datetime(2026, 9, 21, 7, 30, tzinfo=APP_TIMEZONE), None),
            "Closed, opens Mon 21 Sep at 07:30",
        ),
        (
            OpenStatus(
                False,
                datetime(2026, 9, 16, 7, 0, tzinfo=APP_TIMEZONE),
                "The water is off",
            ),
            "Closed: The water is off. Opens tomorrow at 07:00.",
        ),
        (
            OpenStatus(False, None, "Until further notice."),
            "Closed: Until further notice.",
        ),
        (OpenStatus(False, None, None), "Closed, no opening hours listed"),
    ],
)
def test_closed_always_says_when_it_opens(status: OpenStatus, label: str) -> None:
    """In Johannesburg wall-clock time, relative to when the search ran."""
    assert open_label(status, _TUESDAY_10AM) == label
