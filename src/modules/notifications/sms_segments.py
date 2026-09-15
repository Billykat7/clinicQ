"""How many billable parts an SMS is, and keeping it to one (Issue 65).

A gateway bills per **segment**, not per message. A text that fits the GSM 7-bit alphabet is one
segment up to 160 characters and then 153 per segment. One character outside that alphabet (an en dash,
a curly quote, "ê") switches the whole message to UCS-2, where one segment holds only 70 characters, and
a 90-character message quietly costs twice as much. So:

* :func:`to_gsm7` replaces the characters that commonly sneak in with their GSM equivalents: dashes,
  curly quotes, ellipses, non-breaking spaces, and the accented vowels Afrikaans uses that GSM lacks
  ("ê" becomes "e"). Characters with no equivalent are left alone, and the count then says UCS-2.
* :func:`count_segments` measures a text exactly: GSM characters in the extension table (``{ } [ ] ~ \\
  | ^ €``) take two septets.

The SMS transport normalises every message and refuses one longer than ``SMS_MAX_SEGMENTS``; the
queue's templates are written, and tested in every language, to fit one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

#: GSM 03.38 basic character set (one septet each).
GSM7_BASIC: Final = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
#: GSM 03.38 extension table (an escape and the character: two septets each).
GSM7_EXTENDED: Final = frozenset("^{}\\[~]|€\f")

#: Common characters outside GSM 7-bit and what they are sent as, by code point so none of them has to
#: be told apart by eye in this file.
_REPLACEMENTS: Final = {
    chr(code): replacement
    for codes, replacement in (
        (
            (0x2012, 0x2013, 0x2014, 0x2212, 0x2022),
            "-",
        ),  # figure, en, em dashes; minus; bullet
        ((0x2018, 0x2019, 0x201A), "'"),  # curly single quotes
        ((0x201C, 0x201D, 0x201E), '"'),  # curly double quotes
        ((0x2026,), "..."),  # ellipsis
        ((0x00A0, 0x2009, 0x202F), " "),  # no-break, thin and narrow no-break spaces
        ((0x00B7,), "."),  # middle dot
        # Afrikaans (and some Sesotho spellings) vowels GSM does not have: ê ë ô û î ï á í ó ú.
        ((0x00EA, 0x00EB), "e"),
        ((0x00F4, 0x00F3), "o"),
        ((0x00FB, 0x00FA), "u"),
        ((0x00EE, 0x00EF, 0x00ED), "i"),
        ((0x00E1,), "a"),
        ((0x00CA, 0x00CB), "E"),
        ((0x00D4,), "O"),
        ((0x00DB,), "U"),
        ((0x00CE, 0x00CF), "I"),
    )
    for code in codes
}

GSM7_SINGLE: Final = 160
GSM7_PART: Final = 153
UCS2_SINGLE: Final = 70
UCS2_PART: Final = 67


class SmsEncoding(StrEnum):
    """The two encodings an SMS can be sent in."""

    GSM7 = "gsm7"
    UCS2 = "ucs2"


@dataclass(frozen=True, slots=True)
class SegmentCount:
    """How a text will be sent: its encoding, its length in that encoding, and its billable parts."""

    encoding: SmsEncoding
    units: int
    segments: int


def to_gsm7(text: str) -> str:
    """``text`` with look-alike characters replaced by their GSM 7-bit equivalents."""
    return "".join(_REPLACEMENTS.get(char, char) for char in text)


def count_segments(text: str) -> SegmentCount:
    """Exactly how many segments ``text`` is sent as."""
    if all(char in GSM7_BASIC or char in GSM7_EXTENDED for char in text):
        units = sum(2 if char in GSM7_EXTENDED else 1 for char in text)
        segments = 1 if units <= GSM7_SINGLE else -(-units // GSM7_PART)
        return SegmentCount(SmsEncoding.GSM7, units, segments)
    # UTF-16 code units: a character outside the Basic Multilingual Plane (an emoji) takes two.
    units = len(text.encode("utf-16-le")) // 2
    segments = 1 if units <= UCS2_SINGLE else -(-units // UCS2_PART)
    return SegmentCount(SmsEncoding.UCS2, units, segments)
