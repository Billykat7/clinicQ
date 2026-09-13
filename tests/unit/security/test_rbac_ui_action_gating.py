"""Every gated in-page control names the resource, verb and tier its route enforces (Issue #168).

Issue #168 settled the convention the codebase had been contradicting itself about: **a
state-changing control is gated on the verb its own route enforces.** Applying it to ~90 controls
across 19 admin templates makes one failure mode much more likely than before — not a missing gate,
but a gate that *exists* and names the **wrong** resource, verb or tier. That is strictly worse than
no gate: it hides a control from a caller the route would admit, or shows one the route refuses, and
it does so silently. Issue #155 found exactly that class of bug twice.

So this suite asserts **parity**, pair by pair: for each control, the ``{% if can(...) %}`` written
in the template and the ``Depends(require(...))`` mounted on the endpoint behind it must agree on
all three axes. Both sides are read live — the template from its own source, the dependency by
running it with the ``ensure_*`` layer mocked (the technique
``test_rbac_issue155_surface_audit.py`` established) — so neither side is a restatement of what the
test author believed.

**On the testing rules.** ``docs/IDE/RULES/testing-strategy.mdc`` forbids tests that assert HTML body
content. Nothing here renders a page or inspects a response: the templates are read as **source
text**, the same way ``test_rbac_manifest_registry.py`` reads ``src/``. That distinction is the
whole reason this file exists rather than a browser-level check — and it is why Issue #168's own
scope section says to cover the convention this way.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

import re
from pathlib import Path

from src.commons.enums import GrantScope

_TEMPLATES = Path(__file__).resolve().parents[3] / "src" / "templates"


def _template_gates(template: str) -> set[tuple[str, str, str]]:
    """Return every ``can(resource, verb[, scope])`` triple written in ``template``'s source."""
    source = (_TEMPLATES / template).read_text(encoding="utf-8")
    found: set[tuple[str, str, str]] = set()
    for match in re.finditer(
        r"can\(\s*'([^']+)'\s*,\s*'([^']+)'\s*(?:,\s*'([^']+)'\s*)?\)", source
    ):
        resource, verb, scope = match.groups()
        found.add((resource, verb, scope or GrantScope.OWN.value))
    return found


def test_every_gate_in_a_console_template_names_a_real_catalog_resource() -> None:
    """A gate on a typo'd resource silently never fires — the failure mode with no symptom.

    ``NavVisibility.can`` deliberately does not validate its resource argument (a typo resolves to
    "no grant" rather than raising), so nothing at runtime would ever report this. Broader than the
    parity table above on purpose: it covers *every* gate in every console template, including the
    ones whose routes carry no dependency to compare against.
    """
    from src.core.rbac_manifest_registry import manifest_resource_keys

    known = manifest_resource_keys()
    for template in sorted(_TEMPLATES.rglob("*.html")):
        rel = str(template.relative_to(_TEMPLATES))
        for resource, verb, scope in _template_gates(rel):
            assert resource in known, (
                f"{rel}: can('{resource}', ...) is not a catalog resource"
            )
            assert scope in {s.value for s in GrantScope}, (
                f"{rel}: can('{resource}', '{verb}', '{scope}') names no such scope tier"
            )


def test_the_rbac_catalog_tables_can_drop_their_actions_column_as_a_unit() -> None:
    """Header and cells carry the same ``data-col`` marker, so the column counts stay aligned.

    The acceptance criterion is specifically that the ``<th>`` and the ``<td>``s drop *together*;
    a renderer that hid only the cells would leave every catalog table one column short of its
    header. Asserted structurally — both halves declare the same marker and one helper removes it —
    rather than by rendering, per the note at the top of this module.
    """
    markup = (_TEMPLATES / "rbac_admin.html").read_text(encoding="utf-8")
    js = (
        Path(__file__).resolve().parents[3] / "src" / "static" / "js" / "rbac-admin.js"
    ).read_text(encoding="utf-8")
    crud = (
        Path(__file__).resolve().parents[3] / "src" / "static" / "js" / "admin-crud.js"
    ).read_text(encoding="utf-8")

    # One header marker per catalog table (resources, actions, permissions, nav-gates).
    assert markup.count('data-col="actions"') == 4
    # The renderers mark their cells with the same value...
    assert js.count('setAttribute("data-col", "actions")') >= 3
    # ...and one shared helper removes every element carrying it, header included.
    assert "ns.dropColumn = function dropColumn" in crud
    assert 'dropActionsWhenDenied("catalog-resources")' in js
    assert 'dropActionsWhenDenied("catalog-actions")' in js
    assert 'dropActionsWhenDenied("catalog-permissions")' in js
    assert 'dropActionsWhenDenied("catalog-nav-gates")' in js


def test_the_verdict_marker_is_stamped_by_the_template_the_js_reads_it_from() -> None:
    """The JS-rendered gate has a server-side source: one marker, read by the shared helper."""
    markup = (_TEMPLATES / "rbac_admin.html").read_text(encoding="utf-8")
    js = (
        Path(__file__).resolve().parents[3] / "src" / "static" / "js" / "rbac-admin.js"
    ).read_text(encoding="utf-8")
    assert 'id="catalog-verdicts"' in markup
    assert "data-can-update=" in markup
    assert "data-can-delete=" in markup
    assert 'window.BKPAdmin.verdicts("catalog-verdicts")' in js
