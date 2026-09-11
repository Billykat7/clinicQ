"""Phone numbers: the one place a number is normalised to E.164 (Issue 17).

A patient *is* a phone number, so two spellings of one number must never become two patients.
People write a South African mobile number at least five ways (``0821234567``, ``082 123 4567``,
``+27 82 123 4567``, ``+27 (0)82 123 4567``, ``0027 82 123 4567``), and a USSD gateway reports it as
a bare MSISDN (``27821234567``). :func:`normalize_phone` turns every one of them into
``+27821234567``, and every lookup, insert and uniqueness check goes through it; nothing else in
the codebase parses a number.

Rules, deliberately narrow:

* **South Africa first.** A number starting with the ``0`` trunk prefix is South African; so is a
  bare ``27`` followed by exactly nine digits (the MSISDN form). The national number must be nine
  digits and start with 1 to 8 (``0`` and ``9`` begin no valid SA number). Landlines pass: a
  number that cannot take an SMS simply never receives the code.
* **International fallback.** Anything written with a ``+`` or the ``00`` international prefix is
  taken as E.164: a country code that does not start with 0, and 8 to 15 digits in all (ITU-T
  E.164). The numbering plan of each country is not validated; delivery is the real test.
* **Refused:** letters, a number with no way to tell its country (``821234567``), and anything
  outside those lengths. Spaces, hyphens, dots and parentheses are ignored.
"""

import re
from typing import Final

from src.commons.exceptions import UnprocessableError

#: The country this deployment serves; a trunk-prefixed number belongs to it.
SOUTH_AFRICA_CODE: Final = "27"
#: Punctuation people write inside a number, ignored wherever it appears.
_PUNCTUATION: Final = re.compile(r"[\s\-.()]")
#: E.164 allows at most 15 digits; a real number has at least 8.
_E164_DIGITS: Final = range(8, 16)


class InvalidPhoneNumberError(UnprocessableError):
    """A phone number that cannot be normalised: HTTP 422, code ``patients.phone.invalid``.

    The message never repeats the number: it is personal information, and error envelopes are
    logged.
    """

    def __init__(self) -> None:
        """One message for every way a number can be wrong."""
        super().__init__(
            "Enter a mobile number such as 082 123 4567 or +27 82 123 4567.",
            code="patients.phone.invalid",
        )


def _south_african(national: str) -> str:
    """``+27`` and a validated nine-digit national number (a stray trunk ``0`` dropped)."""
    if national.startswith("0"):  # "+27 (0)82 ...", "27082 ..."
        national = national[1:]
    if len(national) != 9 or national[0] not in "12345678":
        raise InvalidPhoneNumberError()
    return f"+{SOUTH_AFRICA_CODE}{national}"


def normalize_phone(raw: str) -> str:
    """Return ``raw`` as an E.164 number (``+27821234567``), or raise :class:`InvalidPhoneNumberError`.

    Idempotent: a normalised number normalises to itself.
    """
    text = raw.strip()
    international = text.startswith("+")
    digits = _PUNCTUATION.sub("", text.removeprefix("+"))
    if not digits.isdigit() or not digits.isascii():
        raise InvalidPhoneNumberError()
    if not international and digits.startswith("00"):
        international, digits = True, digits[2:]
    if international:
        if digits.startswith(SOUTH_AFRICA_CODE):
            return _south_african(digits[len(SOUTH_AFRICA_CODE) :])
        if digits.startswith("0") or len(digits) not in _E164_DIGITS:
            raise InvalidPhoneNumberError()
        return f"+{digits}"
    if digits.startswith("0"):
        return _south_african(digits)
    if digits.startswith(SOUTH_AFRICA_CODE) and len(digits) == 11:
        return _south_african(digits[len(SOUTH_AFRICA_CODE) :])
    raise InvalidPhoneNumberError()


def mask_phone(e164: str) -> str:
    """``+27821234567`` → ``+27 ** *** 4567``: enough for a person to recognise their own number."""
    return f"{e164[:3]} ** *** {e164[-4:]}"
