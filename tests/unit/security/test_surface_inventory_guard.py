"""The standing surface⇄gate guard (Issue #170, M28).

Every UI-layer gap the M28 second wave fixed was found the way M27's sweep found its own: a human
read the templates and routes once, by hand. The guards that already existed all point at *code
shape* — no resource enum reappears (``test_rbac_enum_retired``), every ``require()`` call site
resolves against a manifest (``test_rbac_manifest_registry``), each ``NavDestination``'s gate matches
its console's dependency (``test_nav_enforcement_parity``), and the specific pairs #155 fixed stay
fixed (``test_rbac_issue155_surface_audit``). None of them walks the **set of surfaces**.

So this suite does. It runs ``scripts/lint_surface_gates.py`` over the tree, and — the part that
actually matters — **proves each check can fail**, by feeding it a deliberately ungated page route,
an ownership-only POST route and an ungated control. A guard that cannot fail passes forever, which
is precisely how the gaps this wave closed survived two prior audits.

**Source, not rendered HTML.** The lint parses ``src/web/routes.py``'s AST and scans template text.
Nothing here renders a page or asserts on a response body, so it does not run afoul of
``.cursor/rules/testing-strategy.mdc`` — that rule is about asserting rendered output, which is a
different thing. Stated here as well as in the script so a future cleanup does not delete either.
"""

from __future__ import annotations

import pytest

from scripts.lint_surface_gates import (
    _UNGATED_CONTROLS,
    _UNGATED_MUTATIONS,
    _UNGATED_PAGES,
    check_mutating_routes,
    check_page_routes,
    check_template_controls,
    run,
)

# A page route with no gate at all — what a new console would look like if someone forgot.
_UNGATED_PAGE_FIXTURE = '''
@router.get("/admin/widgets", response_class=HTMLResponse)
async def admin_widgets(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """A new console that forgot its gate."""
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)
    ctx = page_context(request, db, active_nav="widgets", page_title="Widgets")
    return templates.TemplateResponse(request, "admin/widgets.html", ctx)
'''

# The Issue #167 shape: authentication, CSRF and an ownership lookup, and no grant anywhere.
_OWNERSHIP_ONLY_POST_FIXTURE = '''
@router.post("/portal/widgets", response_class=HTMLResponse)
async def portal_create_widget(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    """Ownership answers *whose*; nothing here answers *what*."""
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)
    if not _apply_csrf_ok(request, "", get_settings()):
        return RedirectResponse(url="/portal", status_code=303)
    user = peek_user_from_refresh_cookie(db, request)
    tenant = resolve_portal_tenant(db, user)
    widgets_service.create_widget(db, tenant_id=tenant.id)
    return RedirectResponse(url="/portal?created=1", status_code=303)
'''

# A gated page, to prove the check is discriminating rather than simply always-red.
_GATED_PAGE_FIXTURE = '''
@router.get("/admin/widgets", response_class=HTMLResponse)
async def admin_widgets(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """A new console that remembered."""
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)
    ctx = page_context(request, db, active_nav="widgets", page_title="Widgets")
    if not ctx["nav"].visible("widgets"):
        return _forbidden_html(request, db)
    return templates.TemplateResponse(request, "admin/widgets.html", ctx)
'''


# A bare Delete button in an admin template — what a console looks like before Issue #168.
_UNGATED_CONTROL_TEMPLATE = """<div class="toolbar">
  <button type="button" class="btn btn-danger" id="w-delete">Delete</button>
</div>
"""

# The same control, both accepted ways: the Jinja gate for server-rendered controls, and the
# ``data-can-*`` verdict for the JS-rendered rows that cannot carry one.
_GATED_CONTROL_TEMPLATE = """{% if can('widgets', 'delete') %}
  <button type="button" class="btn btn-danger">Delete</button>
{% endif %}
<button type="button" data-can-delete="true">Delete</button>
"""


# --- The tree is clean ---------------------------------------------------------------------


def test_the_shipped_tree_has_no_ungated_surface() -> None:
    """After #165–#169, every page, mutation and control is gated or allow-listed with a reason."""
    findings = run()
    assert not findings, "\n".join(str(finding) for finding in findings)


@pytest.mark.parametrize(
    "allow_list",
    [_UNGATED_PAGES, _UNGATED_MUTATIONS, _UNGATED_CONTROLS],
    ids=["pages", "mutations", "controls"],
)
def test_every_allow_list_entry_carries_a_reason(allow_list: dict[str, str]) -> None:
    """An exception must be a decision on the record, not an omission.

    The allow-lists are the only way a surface escapes the guard, so an entry with an empty or
    perfunctory reason is how the guard would quietly erode. Enforced as a floor on the reason's
    length rather than left to review.
    """
    for key, reason in allow_list.items():
        assert reason and len(reason) > 20, (
            f"{key}: allow-list entry needs a real reason"
        )


# --- Each check can actually fail ------------------------------------------------------------


def test_the_page_check_fails_on_a_deliberately_ungated_page() -> None:
    """Fed a new console with no gate, the guard reports it — with the handler and the path."""
    findings = check_page_routes(_UNGATED_PAGE_FIXTURE)
    assert len(findings) == 1
    assert findings[0].kind == "page"
    assert "admin_widgets" in findings[0].where
    assert "/admin/widgets" in findings[0].where


def test_the_page_check_passes_the_same_page_once_it_is_gated() -> None:
    """Discriminating, not merely always-red: one added ``visible()`` call clears it."""
    assert check_page_routes(_GATED_PAGE_FIXTURE) == []


def test_the_mutation_check_fails_on_an_ownership_only_post() -> None:
    """Issue #167's finding, made permanent: an ownership lookup alone is not authorization."""
    findings = check_mutating_routes(_OWNERSHIP_ONLY_POST_FIXTURE)
    assert len(findings) == 1
    assert findings[0].kind == "mutation"
    assert "portal_create_widget" in findings[0].where
    assert "grant" in findings[0].detail


def test_the_mutation_check_is_not_satisfied_by_the_ownership_lookup_alone() -> None:
    """Stated as its own assertion because it is the exact substitution M28 exists to remove.

    The fixture *does* resolve the caller's own tenant — that is what made the five real routes look
    safe. The guard must still call it a finding.
    """
    assert "resolve_portal_tenant" in _OWNERSHIP_ONLY_POST_FIXTURE
    assert check_mutating_routes(_OWNERSHIP_ONLY_POST_FIXTURE)


def test_the_control_check_fails_on_an_ungated_control(tmp_path, monkeypatch) -> None:
    """Fed an admin template with a bare Delete button, the lint reports it.

    Written to a temporary template tree rather than the real one, so the check is exercised against
    a genuinely ungated control without ever shipping one.
    """
    import scripts.lint_surface_gates as lint

    admin = tmp_path / "admin"
    admin.mkdir()
    (admin / "widgets.html").write_text(
        _UNGATED_CONTROL_TEMPLATE,
        encoding="utf-8",
    )
    monkeypatch.setattr(lint, "_TEMPLATES", tmp_path)
    findings = check_template_controls()
    assert len(findings) == 1
    assert findings[0].kind == "control"
    assert "admin/widgets.html" in findings[0].where


def test_the_control_check_accepts_the_same_control_once_gated(
    tmp_path, monkeypatch
) -> None:
    """One ``{% if can(...) %}`` around it clears the finding — and a `data-can-*` marker does too.

    Both accepted forms are covered here because Issue #168 ships both: the Jinja gate for
    server-rendered controls, and the verdict attribute for the JS-rendered rows that cannot carry
    one.
    """
    import scripts.lint_surface_gates as lint

    admin = tmp_path / "admin"
    admin.mkdir()
    (admin / "widgets.html").write_text(
        _GATED_CONTROL_TEMPLATE,
        encoding="utf-8",
    )
    monkeypatch.setattr(lint, "_TEMPLATES", tmp_path)
    assert check_template_controls() == []
