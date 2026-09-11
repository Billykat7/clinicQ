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

from src.commons.enums import GrantScope, UserRole
from src.core.rbac import (
    ensure_management_permission_key,
    ensure_named_action_permission_key,
    ensure_permission_key,
    ensure_roles_hold_permission,
)
from src.core.security import get_current_user
from src.database.models import Patient
from src.database.session import get_db

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[dict, Depends(get_current_user)]

#: The attribute every gate dependency carries: ``(resource_key, verb or action)``. The API route
#: guard (``tests/unit/security/test_api_route_gates.py``, Issue 18) reads it to prove every protected
#: route declares its permission through one of these factories.
RBAC_GATE_ATTRIBUTE = "__rbac_gate__"


def _tag[F](dependency: F, resource_key: str, verb: str) -> F:
    """Mark ``dependency`` as an RBAC gate on ``(resource_key, verb)`` and return it."""
    setattr(dependency, RBAC_GATE_ATTRIBUTE, (resource_key, verb))
    return dependency


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

    return _tag(dependency, resource_key, verb)


def require_action(resource_key: str, action_key: str):
    """Dependency factory: require the named ``action_key`` on ``resource_key`` (Issue #149).

    The ``require`` counterpart for non-cumulative named actions (``sign``, ``approve``, ...);
    resolves via :func:`src.core.rbac.ensure_named_action_permission_key`.
    """

    async def dependency(db: DbSession, current_user: CurrentUser) -> None:
        ensure_named_action_permission_key(db, current_user, resource_key, action_key)

    return _tag(dependency, resource_key, action_key)


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

    return _tag(dependency, resource_key, verb)


def require_patient(resource_key: str, verb: str):
    """Dependency factory: a signed-in **patient** whose role holds ``verb`` on ``resource_key``.

    Patients are not ``user`` rows (Issue 17), so the staff factories cannot resolve them; this one
    takes the patient session (:func:`~src.modules.patients.sessions.get_current_patient`, 401
    without one) and checks the ``patient`` role's grants through the same resolver (Issue 18), so
    the patient role's grants are load-bearing rather than decorative. Returns the patient, so a
    route can take it straight from the gate.
    """
    from src.modules.patients.sessions import get_current_patient

    # ``Depends`` in the default, not in an ``Annotated`` hint: this module defers annotations, and
    # the lazily imported dependency is not a module global a string hint could resolve against.
    def dependency(
        db: DbSession, patient: Patient = Depends(get_current_patient)
    ) -> Patient:
        ensure_roles_hold_permission(db, [UserRole.PATIENT.value], resource_key, verb)
        return patient

    return _tag(dependency, resource_key, verb)
