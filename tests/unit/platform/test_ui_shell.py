"""The UI shell's standing rules (Issue 5): one token set, nothing from a CDN, AA contrast.

Source and token checks, in the shape of ``test_no_inline_styles.py``: they read the shipped
templates and stylesheets, never a rendered page, so they stay inside
``docs/IDE/RULES/testing-strategy.mdc``. What a browser does with them (the htmx swap, the layouts
in both themes) is shown in the PR with screenshots; the routes' behaviour is covered by
``tests/integration/platform/test_ui_shell_routes.py``.

The rules, and why each can fail:

* **Nothing loads from another host at runtime.** No template or stylesheet names an ``http(s)://``
  or protocol-relative stylesheet, script or font. The board must render with the clinic's
  internet down, and a visitor's address goes to no CDN.
* **One token set.** The new stylesheets (components, layouts, dev) contain no colour literal:
  every colour is a ``var(--token)`` from ``site.css``. The kernel's ``admin.css`` and
  ``landing.css`` predate the rule and are not yet held to it.
* **Contrast meets WCAG 2.2 AA** for every text/background pair the components use, in both
  themes, computed from the tokens themselves. And the dark palette, written twice (for the OS
  preference and for the toggle), is the same palette in both places.
* **Every template extends a layout,** and only a layout extends ``base.html``.
* **Every ticket status and source can be drawn.** A new enum member without a badge or a label
  would reach a screen that cannot show it.
"""

import json
import re
from pathlib import Path

import pytest

from src.commons.enums import TicketSource, TicketStatus
from src.web.components import (
    TICKET_SOURCE_LABELS,
    TICKET_STATUS_BADGES,
    TOAST_EVENT,
    BadgeTone,
    ToastKind,
    toast_trigger,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = REPO_ROOT / "src" / "templates"
CSS = REPO_ROOT / "src" / "static" / "css"
FONTS = REPO_ROOT / "src" / "static" / "fonts"

#: Stylesheets written on the token rule from the start (Issue 5).
TOKEN_ONLY_STYLESHEETS = ("components.css", "layouts.css", "dev.css", "discover.css")

#: The three layouts, and the only templates allowed to extend base.html directly.
LAYOUTS = ("layouts/patient.html", "layouts/dashboard.html", "layouts/board.html")

#: A reference to another host: an absolute or protocol-relative URL in the attributes that load
#: code, style or fonts, or in a CSS url()/@import.
_REMOTE_IN_HTML = re.compile(
    r"<(?:link|script)\b[^>]*\b(?:href|src)\s*=\s*[\"'](?:https?:)?//", re.I
)
_REMOTE_IN_CSS = re.compile(r"(?:url\(|@import)\s*[\"']?(?:https?:)?//", re.I)

#: A colour written as a literal instead of a token.
_COLOUR_LITERAL = re.compile(
    r"#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(|"
    r":\s*(?:white|black|red|green|blue|gray|grey)\b",
    re.I,
)

#: The text/background token pairs the components put on screen, and what each is.
_TEXT_PAIRS = [
    ("ink", "bg", "body text"),
    ("ink", "surface", "text on a card"),
    ("muted", "bg", "secondary text"),
    ("muted", "surface", "secondary text on a card"),
    ("muted", "bg-soft", "muted badge"),
    (
        "on-brand",
        "brand",
        "primary button, current badge, ok toast, the board's number",
    ),
    ("on-brand", "brand-hover", "primary button, hovered"),
    ("brand-deep", "brand-soft", "ok badge"),
    ("brand-deep", "bg", "links, the patient's ticket number"),
    ("warn-ink", "warn-bg", "warning badge"),
    ("on-danger", "danger", "danger button, error toast"),
    ("danger-ink", "surface", "destructive text"),
    ("surface", "ink", "info toast"),
]

#: WCAG 2.2 success criterion 1.4.3 (AA): normal-size text needs 4.5:1.
_AA_TEXT = 4.5


def _strip_css_comments(text: str) -> str:
    """CSS without its comments, so prose about a colour is not mistaken for one."""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _block(css: str, selector: str) -> str:
    """The declarations of the first rule whose selector is ``selector``."""
    start = css.index(selector + " {")
    return css[start : css.index("}", start)]


def _declarations(block: str) -> dict[str, str]:
    """``--token: value`` pairs in a rule body."""
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))


def _palettes() -> dict[str, dict[str, str]]:
    """The light and dark palettes as ``site.css`` defines them (dark overrides light)."""
    css = _strip_css_comments((CSS / "site.css").read_text(encoding="utf-8"))
    light = _declarations(_block(css, ":root"))
    dark = {**light, **_declarations(_block(css, ':root[data-theme="dark"]'))}
    return {"light": light, "dark": dark}


def _luminance(hex_colour: str) -> float:
    """WCAG relative luminance of a ``#rrggbb`` colour."""
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two ``#rrggbb`` colours."""
    high, low = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_no_template_loads_code_style_or_fonts_from_another_host() -> None:
    """No ``<link>`` or ``<script>`` in any template points at another origin."""
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{text[: m.start()].count(chr(10)) + 1}"
        for path in sorted(TEMPLATES.rglob("*.html"))
        for text in [path.read_text(encoding="utf-8")]
        for m in _REMOTE_IN_HTML.finditer(text)
    ]
    assert offenders == [], (
        "Runtime CDN reference (vendor it under src/static/):\n" + "\n".join(offenders)
    )


def test_no_stylesheet_imports_or_fetches_from_another_host() -> None:
    """No ``url()`` or ``@import`` in any stylesheet points at another origin."""
    offenders = [
        path.name
        for path in sorted(CSS.glob("*.css"))
        if _REMOTE_IN_CSS.search(_strip_css_comments(path.read_text(encoding="utf-8")))
    ]
    assert offenders == []


def test_every_self_hosted_font_exists_with_its_licence() -> None:
    """Each ``@font-face`` source is a file on disk, and the OFL texts ship beside them."""
    css = (CSS / "site.css").read_text(encoding="utf-8")
    sources = re.findall(r'url\("/static/fonts/([^"]+)"\)', css)
    assert len(sources) == 4
    assert [name for name in sources if not (FONTS / name).is_file()] == []
    assert {p.name for p in FONTS.glob("OFL-*.txt")} == {
        "OFL-DMSans.txt",
        "OFL-Roboto.txt",
    }


@pytest.mark.parametrize("stylesheet", TOKEN_ONLY_STYLESHEETS)
def test_new_stylesheets_use_tokens_not_colours(stylesheet: str) -> None:
    """Every colour in the shell's stylesheets is a token from site.css."""
    text = _strip_css_comments((CSS / stylesheet).read_text(encoding="utf-8"))
    offenders = [
        f"{stylesheet}:{text[: m.start()].count(chr(10)) + 1}: {m.group(0)}"
        for m in _COLOUR_LITERAL.finditer(text)
    ]
    assert offenders == [], (
        "Colour literal (use a var(--token) from site.css):\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(("foreground", "background", "use"), _TEXT_PAIRS)
def test_text_contrast_meets_wcag_aa(
    theme: str, foreground: str, background: str, use: str
) -> None:
    """Each text/background pair the components use reaches 4.5:1 in both themes."""
    palette = _palettes()[theme]
    ratio = _contrast(palette[f"--{foreground}"], palette[f"--{background}"])
    assert ratio >= _AA_TEXT, (
        f"{theme}: --{foreground} on --{background} ({use}) is {ratio:.2f}:1"
    )


def test_both_dark_palettes_are_the_same_palette() -> None:
    """The OS-preference dark block and the toggle's dark block declare identical values."""
    css = _strip_css_comments((CSS / "site.css").read_text(encoding="utf-8"))
    media = css[css.index("@media (prefers-color-scheme: dark)") :]
    by_preference = _declarations(_block(media, ':root:not([data-theme="light"])'))
    by_toggle = _declarations(_block(css, ':root[data-theme="dark"]'))
    assert by_preference == by_toggle


def test_only_layouts_extend_base_and_every_page_extends_a_layout() -> None:
    """One base, three layouts: a page that extended base.html directly would skip its layout."""
    extends = {
        path.relative_to(TEMPLATES).as_posix(): match.group(1)
        for path in TEMPLATES.rglob("*.html")
        if (
            match := re.search(
                r'{%-?\s*extends\s+"([^"]+)"', path.read_text(encoding="utf-8")
            )
        )
    }
    assert {name for name, parent in extends.items() if parent == "base.html"} == set(
        LAYOUTS
    )
    assert {parent for name, parent in extends.items() if name not in LAYOUTS} <= set(
        LAYOUTS
    )


def test_every_ticket_status_has_a_badge_with_a_known_tone() -> None:
    """A status added to the enum must be given words and a tone before any screen meets it."""
    assert set(TICKET_STATUS_BADGES) == set(TicketStatus)
    assert all(
        shown.tone in BadgeTone and shown.label
        for shown in TICKET_STATUS_BADGES.values()
    )


def test_every_ticket_source_has_a_label() -> None:
    """Likewise for the channels a ticket can come from."""
    assert set(TICKET_SOURCE_LABELS) == set(TicketSource)


def test_toast_trigger_is_the_header_ui_feedback_listens_for() -> None:
    """``HX-Trigger`` carries the toast event name with its message and kind as JSON."""
    headers = toast_trigger("Queue refreshed", ToastKind.OK)
    assert json.loads(headers["HX-Trigger"]) == {
        TOAST_EVENT: {"message": "Queue refreshed", "kind": "ok"}
    }
    listener = (REPO_ROOT / "src" / "static" / "js" / "ui-feedback.js").read_text(
        encoding="utf-8"
    )
    assert f'addEventListener("{TOAST_EVENT}"' in listener
