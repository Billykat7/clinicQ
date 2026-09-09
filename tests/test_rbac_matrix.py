"""Portal role & permission matrix tests (Issue #58 / M9).

Pins the four portal roles and their permission matrix — the single source of truth in
:data:`src.core.rbac.PORTAL_ROLE_MATRIX` that migration ``0026`` seeds and
``docs/architecture/rbac-matrix.md`` documents — so the seed, the code and the doc can never
drift. Each role's *effective* verb (after parent->child inheritance) is asserted resource by
resource against a database seeded exactly as ``alembic upgrade head`` leaves it.

Since Issue #154 (M27) two things changed shape here, though no expectation did. Resources are
plain string keys and the tree they inherit through comes from the registered module manifests
(``permission_resource_ancestors`` is re-derived locally as :func:`_ancestors`, since the enum that
answered it is deleted). And ``matrix_resources()`` now covers every manifest-declared resource,
not the 38 the enum happened to hold — so the expectations merge ``PORTAL_ROLE_MATRIX`` with each
module's own grant seed (:func:`~src.core.rbac.default_reporting_role_permissions` today), which is
what a deployed database actually holds.

These are essential, isolated RBAC rules (per ``.cursor/rules/testing-strategy.mdc`` unit tests
are reserved for exactly this); allow/deny over real HTTP is covered by the admin and
``/auth/me`` integration suites.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.


def test_the_surface_matrix_covers_every_shipped_surface() -> None:
    """All 38, so a surface added later cannot be quietly missing from the document."""
    from scripts.generate_surface_matrix import render_block
    from src.core.nav_registry import NAV_DESTINATIONS
    from src.core.rbac_manifest import iter_resources
    from src.core.rbac_manifest_registry import ALL_MANIFESTS

    block = render_block()
    for dest in NAV_DESTINATIONS:
        assert f"| `{dest.key}` |" in block, (
            f"{dest.key} missing from the surface matrix"
        )
    for manifest in ALL_MANIFESTS:
        for node in iter_resources(manifest):
            if node.nav is None:
                continue
            assert f"| `{node.full_key}` |" in block, (
                f"{node.full_key} missing from the surface matrix"
            )
