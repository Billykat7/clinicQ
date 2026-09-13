"""Registry-driven nav & action visibility — the data-driven RBAC UX (Issue #104).

Navigation and in-page action rendering derive from one declarative registry
(:mod:`src.core.nav_registry`) evaluated against the caller's *effective* grant matrix
(:class:`src.core.nav_visibility.NavVisibility`). These are the essential, deterministic RBAC
rules unit tests are reserved for (``docs/IDE/RULES/testing-strategy.mdc``): they assert what
:meth:`~src.core.nav_visibility.NavVisibility.can`, ``visible`` and the group helpers return per
role — including a **custom** role built only from grants — so a future regression fails here.

The complementary "the API still enforces it" half (a direct URL/write without the grant is
refused over HTTP) lives in ``tests/integration/admin/``.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.commons.enums import GrantScope, PermissionVerb
from src.core.nav_registry import (
    NAV_DESTINATIONS,
    NAV_GROUPS,
    all_destination_keys,
    destination,
)
from src.core.nav_visibility import nav_visibility_for_user
from src.core.rbac import (
    default_role_permissions,
    default_system_roles,
    seeded_grant_scope,
)
from src.database.models import RbacRole, RolePermission

# A bespoke, non-system role that exists only through grants an admin builds in the RBAC console.
_CUSTOM_ROLE = "regional-viewer"


def _seed_defaults(db: Session) -> None:
    """Seed the system + portal roles and the base admin/portal grants (as the migrations do)."""
    for name, description in (*default_system_roles(),):
        db.add(RbacRole(name=name, description=description, is_system=True))
    seen: set[tuple[str, str]] = set()
    for role, resource, verb in (*default_role_permissions(),):
        if (role, resource) in seen:
            continue
        seen.add((role, resource))
        db.add(
            RolePermission(
                role=role,
                resource=resource,
                max_verb=verb.value,
                scope=seeded_grant_scope(role).value,
                created_at=datetime.now(UTC),
            )
        )


def _grant(
    db: Session,
    role: str,
    resource: str,
    verb: PermissionVerb,
    *,
    scope: GrantScope = GrantScope.BUSINESS,
) -> None:
    """Add a single ``(role, resource, max_verb, scope)`` grant row.

    ``scope`` defaults to ``business`` because the custom roles these tests build are admin-authored
    *console* roles, and since Issue #172 reaching a console is something an admin has to say
    explicitly — the tier no longer follows from the role not being called owner/tenant/vendor.
    """
    db.add(
        RolePermission(
            role=role,
            resource=resource,
            max_verb=verb.value,
            scope=scope.value,
            created_at=datetime.now(UTC),
        )
    )


@pytest.fixture
def seeded_db(session_factory: sessionmaker[Session]) -> Generator[Session]:
    """A session with the default RBAC seed plus one custom, READ-only management role."""
    with session_factory() as db:
        _seed_defaults(db)
        # A custom role a deployment might build: it can *see* the leases console (leases READ) but
        # holds no write verb, so it must get the console page yet none of its action buttons.
        db.add(
            RbacRole(
                name=_CUSTOM_ROLE,
                description="Read-only leasing viewer.",
                is_system=False,
            )
        )
        _grant(db, _CUSTOM_ROLE, "orders", PermissionVerb.READ)
        db.commit()
        yield db


def test_group_member_keys_resolve_to_destinations() -> None:
    """Every grouped-rail member key names a real destination in the same group."""
    keys = set(all_destination_keys())
    for grp in NAV_GROUPS:
        for member in grp.member_keys:
            assert member in keys, f"{grp.key} references unknown destination {member}"
            assert destination(member).group == grp.key


def test_every_destination_key_is_unique() -> None:
    """Destination keys are unique — the lookup table cannot silently drop a destination."""
    keys = [d.key for d in NAV_DESTINATIONS]
    assert len(keys) == len(set(keys))


def test_auth_disabled_shows_everything(seeded_db: Session) -> None:
    """With auth disabled (development) every destination and action renders."""
    nav = nav_visibility_for_user(seeded_db, None, auth_enabled=False)
    assert nav.show_icon_sidebar is True
    for key in all_destination_keys():
        assert nav.visible(key) is True
    assert nav.can("rbac", "delete") is True
