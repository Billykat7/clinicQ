"""The rules of a clinic's self-reported payment profile that are worth pinning alone (Issue 37).

Staleness is a date calculation a patient reads ("not confirmed since"), the scheme list is the
vocabulary the filter and the clinic meet on, and the request validation is what keeps "other"
honest. Everything else is proven over HTTP and against PostGIS.
"""

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from src.commons.enums import MedicalAidScheme
from src.commons.time import APP_TIMEZONE
from src.modules.sites.payment_profile import (
    CLINIC_REPORTED_NOTICE,
    SCHEME_LABELS,
    is_stale,
    months_before,
)
from src.modules.sites.schemas import PaymentProfileIn


def test_every_scheme_has_the_words_a_patient_reads() -> None:
    """A scheme added to the controlled list without a label cannot reach a screen."""
    assert set(SCHEME_LABELS) == set(MedicalAidScheme)
    assert len(set(SCHEME_LABELS.values())) == len(SCHEME_LABELS)
    assert "confirm with the clinic" in CLINIC_REPORTED_NOTICE


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 9, 13), date(2026, 3, 13)),
        (date(2026, 8, 31), date(2026, 2, 28)),  # no 31 February: the end of the month
        (date(2028, 8, 31), date(2028, 2, 29)),  # a leap year
        (date(2026, 3, 15), date(2025, 9, 15)),  # across a year
    ],
)
def test_six_months_back_is_calendar_months(day: date, expected: date) -> None:
    """Clamped to the end of a shorter month, never an invalid date."""
    assert months_before(day, 6) == expected


def test_a_profile_is_stale_from_the_day_after_six_months() -> None:
    """Confirmed on 13 March: still current on 13 September, stale on the 14th."""
    confirmed = datetime(2026, 3, 13, 9, 0, tzinfo=APP_TIMEZONE)
    assert (
        is_stale(confirmed, datetime(2026, 9, 13, 23, 0, tzinfo=APP_TIMEZONE)) is False
    )
    assert (
        is_stale(confirmed, datetime(2026, 9, 14, 0, 30, tzinfo=APP_TIMEZONE)) is True
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"schemes": ["other"]}, "Name the scheme"),
        (
            {"schemes": ["gems"], "other_scheme_name": "Umvuzo"},
            "only needed with 'Other'",
        ),
        ({"schemes": ["gems", "gems"]}, "listed once"),
        ({"schemes": ["not_a_scheme"]}, "Input should be"),
        ({"schemes": [], "surprise": True}, "Extra inputs"),
    ],
)
def test_the_request_keeps_other_honest_and_the_list_closed(
    body: dict[str, object], message: str
) -> None:
    """``other`` needs its name, a name needs ``other``, and nothing outside the list gets in."""
    with pytest.raises(ValidationError, match=message):
        PaymentProfileIn.model_validate(
            {"accepts_cash": True, "accepts_card": False, **body}
        )
