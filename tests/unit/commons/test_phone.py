"""One function normalises a phone number (Issue 17): every spelling of a number is one patient.

Essential logic with no I/O, so a unit test (``.cursor/rules/testing-strategy.mdc``).
"""

import pytest

from src.commons.phone import InvalidPhoneNumberError, mask_phone, normalize_phone


@pytest.mark.parametrize(
    "written",
    [
        "+27821234567",
        "0821234567",
        "27821234567",  # the bare MSISDN a USSD gateway sends
        "082 123 4567",
        "082-123-4567",
        "+27 82 123 4567",
        "+27 (0)82 123 4567",
        "0027 82 123 4567",
        " (082) 123.4567 ",
    ],
)
def test_every_way_of_writing_a_south_african_mobile_is_one_number(
    written: str,
) -> None:
    """The three forms the acceptance criterion names, and the ways people actually type them."""
    assert normalize_phone(written) == "+27821234567"


def test_normalising_is_idempotent() -> None:
    """A stored number normalises to itself, so a lookup by it can never miss."""
    once = normalize_phone("0821234567")
    assert normalize_phone(once) == once


@pytest.mark.parametrize(
    ("written", "e164"),
    [
        ("+44 20 7946 0958", "+442079460958"),
        ("0044 20 7946 0958", "+442079460958"),
        ("+263 77 123 4567", "+263771234567"),
        (
            "011 555 1234",
            "+27115551234",
        ),  # a Johannesburg landline: valid, just never texted
    ],
)
def test_international_and_landline_numbers_are_kept(written: str, e164: str) -> None:
    """The fallback: a ``+`` or ``00`` number is E.164 as written."""
    assert normalize_phone(written) == e164


@pytest.mark.parametrize(
    "written",
    [
        "821234567",  # no trunk prefix, no country: which country?
        "08212345678",  # ten national digits
        "082123456",  # eight national digits
        "0921234567",  # no SA number starts with 9
        "+0821234567",  # a country code cannot start with 0
        "+1234567",  # too short for E.164
        "+1234567890123456",  # 16 digits: too long for E.164
        "082 1234 abc",
        "٠٨٢١٢٣٤٥٦٧",  # Arabic-Indic digits: isdigit() is true, but not a phone number here
        "",
    ],
)
def test_numbers_that_cannot_be_read_are_refused(written: str) -> None:
    """Refused with one message that does not repeat the number (error envelopes are logged)."""
    with pytest.raises(InvalidPhoneNumberError) as refused:
        normalize_phone(written)
    assert refused.value.code == "patients.phone.invalid"
    if written.strip():
        assert written.strip() not in str(refused.value)


def test_a_masked_number_keeps_only_what_its_owner_recognises() -> None:
    """The country code and the last four digits."""
    assert mask_phone("+27821234567") == "+27 ** *** 4567"
