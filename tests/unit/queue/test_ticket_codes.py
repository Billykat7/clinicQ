"""A ticket's QR and short code, without a database (Issue 70).

* **the QR says the code**: its modules, read back from the SVG path the page and the stub draw, are exactly
  the QR symbol for ``CLINICQ:<code>``, the smallest version, in the alphanumeric mode;
* **the short code is unambiguous aloud**: no ``0 O 1 I L``, every character has one spoken form, and no two
  spoken forms are alike;
* **a scan and a typed code read the same**, and anything else (another QR, a ticket number, a mistyped ``0``)
  is not a code rather than a guess.
"""

from __future__ import annotations

import random
import re

import pytest
import qrcode
from qrcode.constants import ERROR_CORRECT_M

from src.modules.queue.sequence import REFERENCE_ALPHABET
from src.modules.queue.ticket_codes import (
    QR_PREFIX,
    QR_QUIET_ZONE,
    SPOKEN,
    qr_for,
    qr_payload,
    read_code,
    spoken,
)

_RUN = re.compile(r"M(\d+) (\d+)h(\d+)v1h-\3z")


def _modules(path: str) -> set[tuple[int, int]]:
    """The dark modules an SVG path of unit rows draws."""
    runs = _RUN.findall(path)
    assert "".join(f"M{x} {y}h{w}v1h-{w}z" for x, y, w in runs) == path, (
        "the path is only unit rows"
    )
    return {(int(x) + i, int(y)) for x, y, w in runs for i in range(int(w))}


#: Codes for the QR test: fixed ones, and twenty drawn from a seeded generator, so every test worker collects
#: the same parameters.
_SAMPLE = random.Random(70)
_CODES = [
    "K7M4QP",
    "222222",
    "ZZZZZZ",
    *("".join(_SAMPLE.choice(REFERENCE_ALPHABET) for _ in range(6)) for _ in range(20)),
]


@pytest.mark.parametrize("code", _CODES)
def test_the_drawn_qr_is_exactly_the_symbol_for_the_code(code: str) -> None:
    drawn = qr_for(code)
    assert drawn.payload == f"{QR_PREFIX}{code[:3]}-{code[3:]}" == qr_payload(code)

    symbol = qrcode.QRCode(
        error_correction=ERROR_CORRECT_M, border=QR_QUIET_ZONE, box_size=1
    )
    symbol.add_data(drawn.payload, optimize=0)
    symbol.make(fit=True)
    matrix = symbol.get_matrix()
    expected = {
        (x, y) for y, row in enumerate(matrix) for x, dark in enumerate(row) if dark
    }

    assert symbol.version == 1, (
        "the smallest symbol: the largest modules on a phone or a stub"
    )
    assert symbol.data_list[0].mode == qrcode.util.MODE_ALPHA_NUM
    assert drawn.size == 21 + 2 * QR_QUIET_ZONE
    assert _modules(drawn.path) == expected


def test_the_short_code_reads_aloud_without_ambiguity() -> None:
    assert not set("0O1IL") & set(REFERENCE_ALPHABET)
    assert set(SPOKEN) == set(REFERENCE_ALPHABET), (
        "every character has exactly one way to say it"
    )
    words = list(SPOKEN.values())
    assert len(set(words)) == len(words)
    assert spoken("K7M4QP") == "Kilo 7 Mike, 4 Quebec Papa"
    assert spoken("K7M-4QP") == spoken("K7M4QP")


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("CLINICQ:K7M-4QP", "K7M4QP"),
        ("clinicq:k7m-4qp", "K7M4QP"),
        ("  CLINICQ:K7M-4QP\n", "K7M4QP"),
        ("K7M-4QP", "K7M4QP"),
        ("k7m 4qp", "K7M4QP"),
        ("K7M4QP", "K7M4QP"),
        ("K0M-4QP", None),  # a zero is never in a code: mistyped, not guessed
        ("KOM-4QP", None),
        ("K7M-4Q", None),
        ("T004", None),  # a ticket number is not its code
        ("https://example.org/K7M-4QP", None),
        ("CLINICQ:", None),
        ("", None),
    ],
)
def test_a_scan_and_a_typed_code_read_the_same_and_nothing_else_is_a_code(
    raw: str, code: str | None
) -> None:
    assert read_code(raw) == code
