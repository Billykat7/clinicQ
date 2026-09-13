"""Per-role nav-visibility expectations matrix (Issue #103).

Manual testing surfaced an authorization leak: portal roles (owner/tenant/vendor) hold READ
(and some CREATE/UPDATE) on shared resources so their *own* ownership-scoped portals work, but
that grant was also opening the whole-business management consoles — a tenant/owner/vendor saw
manager rail icons and could open the admin console pages. Issue #103 closed that with a
role-name wall (``is_management_role``); Issues #165/#172 replaced it with the grant's own scope
tier and deleted the wall, so every management console flag is gated on the verb **and** a
``business``-tier grant — policy, not a name.

This is the fast, deterministic half of the audit: it seeds the RBAC tables exactly as
``alembic upgrade head`` leaves them and asserts, role by role, which console flags
:func:`nav_visibility_for_user` returns — the documented expectations matrix, enforced so a future
regression (or a new console wired to a bare READ) fails here. The HTTP defence-in-depth half (a
direct URL to a hidden console 403s) lives in
``tests/integration/admin/test_rbac_console_gating.py``.

Per ``docs/IDE/RULES/testing-strategy.mdc`` these are the essential, isolated RBAC rules unit tests
are reserved for; allow/deny over real HTTP is the integration suite's job.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from src.commons.enums import GrantScope


def test_the_scope_ladder_is_read_off_the_enums_declaration_order() -> None:
    """``satisfies`` is a rank comparison, so a tier inserted later orders itself (Issue #171).

    Guards the one property that makes the ladder extensible: the comparison must never be a pair
    of equality checks against ``business``/``own``.
    """
    assert GrantScope.OWN.tier_rank < GrantScope.BUSINESS.tier_rank
    assert GrantScope.BUSINESS.satisfies(GrantScope.OWN) is True
    assert GrantScope.BUSINESS.satisfies(GrantScope.BUSINESS) is True
    assert GrantScope.OWN.satisfies(GrantScope.OWN) is True
    assert GrantScope.OWN.satisfies(GrantScope.BUSINESS) is False
