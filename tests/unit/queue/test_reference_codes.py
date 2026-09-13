"""Reference codes and ticket numbers: short, unambiguous and safe to read aloud (Issue 39).

Pure functions, so plain unit tests: no database.
"""

import pytest

from src.modules.queue.sequence import (
    REFERENCE_ALPHABET,
    format_reference_code,
    format_ticket_number,
    new_reference_code,
    parse_reference_code,
)

#: The pairs a person reading a stub aloud, or typing one back in, confuses.
CONFUSABLE = "0O1IL"


def test_the_alphabet_has_no_character_that_can_be_mistaken_for_another() -> None:
    """Criterion 6: no ``0``/``O``, no ``1``/``I``/``L``; digits and capitals only, each once."""
    assert not set(CONFUSABLE) & set(REFERENCE_ALPHABET)
    assert len(set(REFERENCE_ALPHABET)) == len(REFERENCE_ALPHABET) == 31
    assert (
        REFERENCE_ALPHABET.isalnum()
        and REFERENCE_ALPHABET.upper() == REFERENCE_ALPHABET
    )


def test_codes_are_six_characters_from_the_alphabet_and_spread_over_all_of_it() -> None:
    """Short (six), drawn from the alphabet, and random: every symbol turns up, repeats are rare.

    Uniqueness itself is the database's job (``uq_ticket_reference_code``, with a redraw), so this
    checks the draw is spread rather than asserting no repeat, which would be a flaky birthday bet:
    2,000 codes from 887 million repeat with a probability of about 0.2%, never ten times over.
    """
    codes = [new_reference_code() for _ in range(2_000)]
    assert all(
        len(code) == 6 and set(code) <= set(REFERENCE_ALPHABET) for code in codes
    )
    assert set("".join(codes)) == set(REFERENCE_ALPHABET)
    assert len(set(codes)) >= len(codes) - 10


def test_a_code_is_written_in_two_groups_and_read_back_however_it_was_typed() -> None:
    """``K7M-4QP`` on the stub; ``k7m 4qp``, ``K7M4QP`` and `` K7M-4QP `` all find it."""
    assert format_reference_code("K7M4QP") == "K7M-4QP"
    for typed in ("K7M-4QP", "k7m 4qp", "K7M4QP", "  K7M-4QP "):
        assert parse_reference_code(typed) == "K7M4QP"


@pytest.mark.parametrize(
    "typed", ["K7M-4Q0", "K7M-4QO", "K7M-4Q1", "I7M4QP", "K7M4Q", "K7M4QPX", ""]
)
def test_a_mistyped_code_is_refused_rather_than_guessed(typed: str) -> None:
    """A confusable character, or the wrong length, is not silently turned into somebody's ticket."""
    assert parse_reference_code(typed) is None


def test_a_ticket_number_is_the_prefix_and_at_least_three_digits() -> None:
    """``A007`` lines up with ``A043`` on the board; a long day still prints ``A1000``."""
    assert format_ticket_number("A", 7) == "A007"
    assert format_ticket_number("T", 43) == "T043"
    assert format_ticket_number("P", 1000) == "P1000"
    with pytest.raises(ValueError, match="starts at 1"):
        format_ticket_number("A", 0)
