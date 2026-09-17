"""No template writes an inline style, and no `<style>` block ships without the nonce (Issue #181).

`style-src 'unsafe-inline'` was pen-test finding **F-03**, accepted in M17 as "a follow-up, not a
blocker". It was removed in M30 once the remaining surface turned out to be four `style="…"`
attributes across three templates. That removal only stays true while nobody adds a fifth — and a
fifth would not fail loudly: the attribute would simply stop applying, and the page would render
slightly wrong in a way easy to miss and easy to "fix" by putting `'unsafe-inline'` back.

So this is the guard, in the same shape as the existing surface-gate and RBAC-snapshot guards: it
scans the shipped templates rather than trusting a policy line.

Two rules, and the difference between them is the whole point:

* **A style attribute is forbidden.** A CSP nonce covers `<style>` **elements**, never style
  *attributes* — there is no nonce that would make one work under this policy.
* **A `<style>` element is allowed, but only with the nonce.** One exists (the login modal), and it
  already carries it.

Deliberately not covered: JavaScript setting `element.style.*`. That is the CSSOM, which
`style-src` does not govern at all, so forbidding it here would be a rule with no security meaning.
What *would* matter is JS building an `innerHTML` string containing a style attribute, so the scan
covers `src/static/js/` for that one pattern.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.core.security_headers import (
    _generate_csp_nonce,
    build_content_security_policy,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_ROOT = REPO_ROOT / "src" / "templates"
JS_ROOT = REPO_ROOT / "src" / "static" / "js"

#: `style="…"` or `style='…'` as an HTML attribute — preceded by whitespace so a Jinja variable
#: named `…style="` (there are none, but the rule should be about attributes) cannot match.
_STYLE_ATTRIBUTE = re.compile(r"\sstyle\s*=\s*[\"']")

#: An opening `<style` tag, with or without attributes.
_STYLE_ELEMENT = re.compile(r"<style\b[^>]*>")

#: What a nonced `<style>` opening tag must contain. Matched on the substring rather than the exact
#: rendering so reordering the attributes does not fail the guard.
_NONCE_MARKER = "nonce="


def _templates() -> list[Path]:
    """Every shipped template, sorted for a stable failure message."""
    return sorted(TEMPLATE_ROOT.rglob("*.html"))


def test_no_template_carries_an_inline_style_attribute() -> None:
    """The rule that keeps `'unsafe-inline'` out of the policy.

    A style attribute cannot be nonced, so one arriving here is not a policy question — it is a
    declaration that belongs in `admin.css`. `.hint-under-label`, `.toolbar-label` and
    `.policy-editor` are the three classes Issue #181 created for exactly this; reach for one of
    those, or add a fourth.
    """
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{number}"
        for path in _templates()
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if _STYLE_ATTRIBUTE.search(line)
    ]
    assert offenders == [], (
        "inline style attribute(s) found — `style-src` has no `'unsafe-inline'`, so these will "
        "not apply:\n  " + "\n  ".join(offenders)
    )


def test_every_style_element_carries_the_csp_nonce() -> None:
    """A `<style>` block is allowed; an unnonced one is dead markup under this policy.

    It would fail silently — the browser drops the block and the page renders unstyled in that
    region — which is the failure mode most likely to be "fixed" by loosening the policy.
    """
    offenders = [
        f"{path.relative_to(REPO_ROOT)}: {tag}"
        for path in _templates()
        for tag in _STYLE_ELEMENT.findall(path.read_text(encoding="utf-8"))
        if _NONCE_MARKER not in tag
    ]
    assert offenders == [], (
        "`<style>` block(s) without a CSP nonce — add "
        '`nonce="{{ request.state.csp_nonce }}"`:\n  ' + "\n  ".join(offenders)
    )


def test_the_nonce_the_rule_above_asks_for_is_still_minted_and_still_in_the_policy() -> (
    None
):
    """A guard that passes because it found nothing is not a guard: prove the nonce path is alive.

    The login modal's ``<style nonce="…">`` used to be the one block keeping the rule above
    meaningful, and Issue 231 removed it with the modal — signing in is a set of pages now, styled
    from ``landing.css`` like the rest of the front door. **The rule still matters**, because the
    next person to add a ``<style>`` needs it, so the decision the old test asked for is this one:
    keep the rule, and check the thing it depends on instead of the last user of it.

    So: every request still gets a fresh nonce on ``request.state``, and the policy it is put into
    still names that nonce. If either stops being true, a ``<style nonce="…">`` added tomorrow
    would be blocked, and the rule above would be asking for something that does not work.
    """
    assert (
        sum(
            len(_STYLE_ELEMENT.findall(path.read_text(encoding="utf-8")))
            for path in _templates()
        )
        == 0
    ), (
        "a `<style>` element is back — the rule above now has a real user again, which is fine"
    )

    nonce = _generate_csp_nonce()
    assert nonce and nonce != _generate_csp_nonce(), (
        "the nonce must be fresh per request"
    )
    policy = build_content_security_policy(nonce)
    assert f"'nonce-{nonce}'" in policy


def test_no_javascript_builds_markup_containing_a_style_attribute() -> None:
    """`element.style.x = …` is fine (CSSOM, not governed by `style-src`); a built string is not.

    A `style="…"` written into `innerHTML` is an inline style attribute by another route, and it
    would be dropped exactly as a template one would.
    """
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{number}"
        for path in sorted(JS_ROOT.rglob("*.js"))
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if _STYLE_ATTRIBUTE.search(line)
    ]
    assert offenders == [], (
        "JavaScript builds markup with an inline style attribute:\n  "
        + "\n  ".join(offenders)
    )
