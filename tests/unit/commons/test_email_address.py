"""One function normalises a patient's email address (Issue 219): every spelling is one patient.

The sibling of ``test_phone.py``, for the same reason it exists: the unique constraint on
``patient.email`` can only hold if one address has one spelling. Essential logic with no I/O, so a
unit test (``docs/IDE/RULES/testing-strategy.mdc``).
"""

import pytest

from src.commons.email_address import (
    MAX_EMAIL_LENGTH,
    InvalidEmailAddressError,
    mask_email,
    normalize_email,
)


@pytest.mark.parametrize(
    "written",
    [
        "nomsa@example.com",
        "Nomsa@Example.com",
        "NOMSA@EXAMPLE.COM",
        "  nomsa@example.com  ",
        "\tNomsa@Example.COM\n",
    ],
)
def test_every_way_of_writing_one_address_is_one_address(written: str) -> None:
    """Case and surrounding space are not what tells two people apart."""
    assert normalize_email(written) == "nomsa@example.com"


def test_normalising_is_idempotent() -> None:
    """A stored address normalises to itself, so a lookup by it can never miss."""
    once = normalize_email("Nomsa@Example.com")
    assert normalize_email(once) == once


@pytest.mark.parametrize(
    "written",
    [
        "",
        "   ",
        "nomsa",
        "nomsa@",
        "@example.com",
        "nomsa@example",  # no dot in the domain
        "nomsa example@x.com",  # a space inside
        "nomsa@@example.com",
        "nomsa@exam ple.com",
        "a" * (MAX_EMAIL_LENGTH + 1) + "@example.com",
    ],
)
def test_an_address_a_person_can_see_is_wrong_is_refused(written: str) -> None:
    """Not an RFC 5321 parser: what it refuses is what a person can see is wrong."""
    with pytest.raises(InvalidEmailAddressError):
        normalize_email(written)


def test_the_refusal_never_repeats_what_was_typed() -> None:
    """The address is personal information, and error envelopes are logged (Issue 6)."""
    with pytest.raises(InvalidEmailAddressError) as refused:
        normalize_email("nomsa-at-example")

    assert "nomsa-at-example" not in str(refused.value)
    assert refused.value.code == "patients.email.invalid"


@pytest.mark.parametrize(
    ("address", "masked"),
    [
        ("nomsa@gmail.com", "n•••a@gmail.com"),
        (
            "thabo.mokoena@health.gov.za",
            "t###########a@health.gov.za".replace("#", "•"),
        ),
        ("ab@example.com", "••@example.com"),
        ("a@example.com", "•@example.com"),
    ],
)
def test_masking_shows_enough_to_recognise_your_own_and_not_enough_to_read_it(
    address: str, masked: str
) -> None:
    """The domain is not what identifies a person, so it is left alone; the local part is not."""
    assert mask_email(address) == masked


def test_a_short_local_part_is_masked_whole_rather_than_given_away_by_the_rule() -> (
    None
):
    """Keeping the first and last of two characters would be keeping both."""
    assert "a" not in mask_email("ab@example.com").split("@")[0]
