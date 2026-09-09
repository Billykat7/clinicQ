"""FastAPI dependencies that enforce verb-based RBAC (Issue #5).

Three generic factories, and nothing else. Each resolves the caller's active roles and calls the
matching ``src.core.rbac`` entrypoint, raising 401 (unauthenticated) or 403 (under-privileged);
each module declares its own ``Annotated`` aliases locally, next to the routes they gate.

**There are no per-``(resource, verb)`` named dependency functions any more** (Issue #154, M27).
Adding a resource once meant editing two shared, hand-maintained chokepoints — the
``PermissionResource`` enum plus one ``require_<resource>_<verb>`` function per verb in this file —
and both are permanently deleted: a module declares its resources in its own
``src/modules/<name>/rbac_manifest.py`` and gates a route with
``Depends(require("its.key", "read"))``. See ``docs/architecture/rbac-module-self-registration.md``
§7. Two standing CI guards keep it that way: ``tests/unit/security/test_rbac_enum_retired.py``
fails the build if an enum of resource keys (or a named ``require_<resource>_<verb>`` dependency)
reappears under any name, and ``tests/unit/security/test_rbac_manifest_registry.py`` AST-walks
``src/`` to check that every call site's ``resource_key`` resolves against a registered manifest —
the enum's old typo-safety net, without the enum.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from src.commons.enums import GrantScope
from src.core.rbac import (
    ensure_management_permission_key,
    ensure_named_action_permission_key,
    ensure_permission_key,
)
from src.core.security import get_current_user
from src.database.session import get_db

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[dict, Depends(get_current_user)]


def require(resource_key: str, verb: str, *, scope: GrantScope = GrantScope.OWN):
    """Dependency factory: require ``verb`` (or higher) on ``resource_key`` (Issue #149).

    ``Depends(require("communications.messages", "read"))`` — no new named function needed for a
    manifest-registered resource, ever. Resolves via :func:`src.core.rbac.ensure_permission_key`
    against the live DB catalog, so a parent grant still cascades to a child resource and a DENY
    anywhere in the role closure or the resource tree still removes access.

    ``scope`` (Issue #166, M28) is the tier the caller's grant must reach — the grant's second axis,
    the same one a *surface* declares since Issue #165. It defaults to
    :data:`~src.commons.enums.GrantScope.OWN`, which every tier satisfies, so an existing call site
    is unchanged; a route that acts on the whole business passes ``BUSINESS``::

        Depends(require("maintenance.work_orders", "update", scope=GrantScope.BUSINESS))

    **Why this rather than :func:`require_management`** (both were on the table for #166): the
    argument names the axis at the call site, so a reader sees *which* tier is required rather than
    inferring it from a function name; it composes with the ladder, so #171's ``assigned`` tier
    needs no third factory; and it keeps one factory for the common case instead of forcing every
    route to choose between two near-identical names. ``require_management`` stays for its existing
    call sites and is now the ``BUSINESS`` case of this same argument.
    """

    async def dependency(db: DbSession, current_user: CurrentUser) -> None:
        ensure_permission_key(
            db, current_user, resource_key, verb, required_scope=scope
        )

    return dependency


def require_action(resource_key: str, action_key: str):
    """Dependency factory: require the named ``action_key`` on ``resource_key`` (Issue #149).

    The ``require`` counterpart for non-cumulative named actions (``sign``, ``approve``, ...);
    resolves via :func:`src.core.rbac.ensure_named_action_permission_key`.
    """

    async def dependency(db: DbSession, current_user: CurrentUser) -> None:
        ensure_named_action_permission_key(db, current_user, resource_key, action_key)

    return dependency


def require_management(resource_key: str, verb: str):
    """Dependency factory: require ``verb`` on ``resource_key`` **and** a ``business``-scoped grant.

    The ``require`` counterpart for whole-business consoles (see
    :func:`src.core.rbac.ensure_management_permission_key` for why these need a second gate on top
    of the verb). Signature unchanged by Issue #156 (M28): what changed is *how* that second gate
    decides — the caller's ``role_permission.scope`` on the resource, resolved by
    :func:`src.core.scope.resolve_scope_tier`, instead of whether their role's name happens to be
    one of three hardcoded portal names.
    """

    async def dependency(db: DbSession, current_user: CurrentUser) -> None:
        ensure_management_permission_key(db, current_user, resource_key, verb)

    return dependency
