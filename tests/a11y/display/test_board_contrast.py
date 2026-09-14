"""Every colour pair on the waiting-room board passes WCAG 2.2 AA, in every theme (Issue 59).

The board's colours are tokens in ``src/static/css/board.css``: one set per theme (``dim``, ``bright``,
``high_contrast``). This test reads the tokens from the stylesheet itself, so a colour changed there is a
colour checked here, and computes the WCAG contrast ratio of every pair the board draws. The pairs cover
every state:

* text, which must reach **4.5:1** (success criterion 1.4.3). Nearly all of the board's text is "large"
  and would need only 3:1, but the board is read from five metres, so it holds the stricter bar;
* the new-call highlight against its panel, a state shown by a filled area, which must reach **3:1**
  (1.4.11, non-text contrast). Contrast is a ratio of luminance, so this is also what keeps a new call
  distinguishable on a greyscale screen.

What the browser really draws, element by element, is checked in
``tests/e2e/display/test_board_accessibility.py``. The two agree because the page uses only these tokens.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from src.commons.enums import BoardTheme

_CSS = Path(__file__).resolve().parents[3] / "src" / "static" / "css" / "board.css"

#: WCAG 2.2 AA minimums.
TEXT_MINIMUM: Final = 4.5
NON_TEXT_MINIMUM: Final = 3.0

#: ``(what, foreground token, background token, minimum)`` for everything the board draws.
PAIRS: Final[tuple[tuple[str, str, str, float], ...]] = (
    ("clinic name and clock", "ink", "bg", TEXT_MINIMUM),
    ("clinic initials", "bg", "accent", TEXT_MINIMUM),
    ("queue name, numbers served and up next", "ink", "panel", TEXT_MINIMUM),
    ("room", "accent", "panel", TEXT_MINIMUM),
    (
        "captions, statuses, waiting count, earlier calls, no-one-served dash",
        "muted",
        "panel",
        TEXT_MINIMUM,
    ),
    ("a new call's number, status and reason", "new-ink", "new-bg", TEXT_MINIMUM),
    ("health notice, page count, empty board", "muted", "bg", TEXT_MINIMUM),
    ("reconnecting line", "alert", "bg", TEXT_MINIMUM),
    ("new-call highlight against its panel", "new-bg", "panel", NON_TEXT_MINIMUM),
)


def theme_tokens(css: str, theme: BoardTheme) -> dict[str, str]:
    """``{token name without --kb-: hex colour}`` for one theme, the default filled in underneath."""
    base = re.search(r"\n\.kiosk \{([^}]*--kb-bg[^}]*)\}", css)
    assert base, "the dim theme's tokens (.kiosk { --kb-… }) are missing from board.css"
    tokens = dict(re.findall(r"--kb-([\w-]+):\s*(#[0-9a-fA-F]{6})", base.group(1)))
    if theme is not BoardTheme.DIM:
        block = re.search(
            rf'\.kiosk\[data-board-theme="{theme.value}"\] \{{([^}}]*)\}}', css
        )
        assert block, f"no token block for the {theme.value} theme in board.css"
        tokens |= dict(
            re.findall(r"--kb-([\w-]+):\s*(#[0-9a-fA-F]{6})", block.group(1))
        )
    return tokens


def relative_luminance(hex_colour: str) -> float:
    """WCAG 2.2 relative luminance of an ``#rrggbb`` colour."""
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(first: str, second: str) -> float:
    """WCAG 2.2 contrast ratio of two colours, from 1 to 21."""
    lighter, darker = sorted(
        (relative_luminance(first), relative_luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_the_contrast_arithmetic_matches_wcags_own_examples() -> None:
    """Black on white is 21:1, white on white 1:1, and #767676 on white the famous 4.54:1."""
    assert round(contrast_ratio("#000000", "#ffffff"), 2) == 21.0
    assert contrast_ratio("#ffffff", "#ffffff") == 1.0
    assert round(contrast_ratio("#767676", "#ffffff"), 2) == 4.54


@pytest.mark.parametrize("theme", list(BoardTheme), ids=[t.value for t in BoardTheme])
def test_every_pair_on_every_board_state_passes_wcag_aa(theme: BoardTheme) -> None:
    """Each theme: every text pair at 4.5:1 or more, the new-call highlight at 3:1 or more."""
    tokens = theme_tokens(_CSS.read_text(encoding="utf-8"), theme)
    failures = []
    for what, foreground, background, minimum in PAIRS:
        ratio = contrast_ratio(tokens[foreground], tokens[background])
        print(f"{theme.value:>13}  {ratio:5.2f}:1  (≥ {minimum})  {what}")  # noqa: T201 — the audit table
        if ratio < minimum:
            failures.append(f"{what}: {ratio:.2f}:1 < {minimum}:1")
    assert not failures, f"{theme.value} fails WCAG 2.2 AA: {failures}"


def test_the_check_fails_on_a_colour_that_does_not_pass() -> None:
    """A guard that cannot fail passes forever: the old dash colour on its panel was 1.4:1."""
    assert contrast_ratio("#2a3b57", "#15233a") < TEXT_MINIMUM
