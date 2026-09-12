"""Role-based access control: CRUD verbs as cumulative permission levels (Issue #5).

Ported from the ``maps`` project. ``READ < CREATE < UPDATE < DELETE``; a role's granted
maximum verb on a resource implies all lower verbs. Resources form a parent->child tree
so a parent grant cascades to children. Enforcement no-ops when ``AUTH_ENABLED`` is off
(development).

Resources are **plain string keys**, permanently (Issue #154, M27). The catalog's source of truth
is the DB ``resources`` table (Issue #139), whose shape is declared by the module manifests
registered in ``src.core.rbac_manifest_registry`` — there is no ``PermissionResource`` enum, no
generated-constants module and no successor class, per
``docs/architecture/rbac-module-self-registration.md`` §7. A standing CI guard
(``tests/unit/security/test_rbac_enum_retired.py``) fails the build if one is reintroduced.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from src.commons.enums import (
    GrantScope,
    PermissionAction,
    PermissionEffect,
    PermissionVerb,
    UserRole,
    is_cumulative_action,
)
from src.core.config import get_settings
from src.core.permission_usage import record_allow
from src.core.s3_logging import APP_TIMEZONE
from src.database.models import (
    Action,
    EffectiveRolePermission,
    Permission,
    RbacRole,
    Resource,
    ResourceDescendant,
    RoleHierarchy,
    RolePermission,
    User,
    UserRoleAssignment,
)

_VERB_ORDER: tuple[PermissionVerb, ...] = (
    PermissionVerb.READ,
    PermissionVerb.CREATE,
    PermissionVerb.UPDATE,
    PermissionVerb.DELETE,
)

RBAC_FORBIDDEN_CODE = "insufficient_permission"

# The cumulative verb values in ladder order, as plain strings — the rank scale every resolver
# indexes into. ``PermissionVerb`` is a ``StrEnum``, so a member and its value index identically.
_VERB_VALUES: tuple[str, ...] = tuple(verb.value for verb in _VERB_ORDER)


def permission_verb_rank(verb: PermissionVerb | str) -> int:
    """Return sort order for verb comparison (higher means more capability)."""
    return _VERB_VALUES.index(str(verb))


def _verb_from_rank(rank: int) -> str:
    """Return the verb value at ``rank`` (inverse of :func:`permission_verb_rank`)."""
    return _VERB_VALUES[rank]


# --------------------------------------------------------------------------------------
# Decision trace (Issue #174, M29) — the resolvers explaining their own steps
# --------------------------------------------------------------------------------------
#
# ``effective_verb_over_keys()``, ``resolve_scope_tier()`` and ``_surface_allowed()`` each compute a
# rich intermediate state and then throw all of it away, returning a verb, a tier or a bool. That is
# why M28 #164's reachability regression needed a hand-built probe across two git worktrees to
# establish, and why nobody could answer "what does the receptionist role reach today?" without writing
# code. The trace is that state, captured **on request only**.
#
# Two rules keep this honest, and both are asserted by
# ``tests/unit/security/test_rbac_decision_trace.py``:
#
# 1. ``trace=None`` is the default everywhere. The enforcement path passes nothing and allocates
#    nothing, so this issue is behaviour-preserving by construction.
# 2. A trace is never an *input* to a decision. Every resolver below appends to the list and reads
#    nothing back from it; the verdict is identical with and without one.


class TraceStage(StrEnum):
    """Which resolver stage a :class:`TraceStep` came from (Issue #174).

    Ordered as a decision is actually made — the roles in play, the grants they supply, the walk up
    the resource tree, the verb that falls out, then the tier, the instances it narrows to and,
    for a page, the surface gate that wraps all of it.
    """

    #: The principal's roles and each one's ``role_hierarchy`` closure (Issue #135).
    ROLE_CLOSURE = "role_closure"
    #: The cumulative grant rows those roles supply, before any resolution.
    CANDIDATE_GRANTS = "candidate_grants"
    #: One step per level walked from the target resource toward the catalog root.
    TREE_WALK = "tree_walk"
    #: The verb :func:`effective_verb_over_keys` resolved, and how.
    VERB_RESOLUTION = "verb_resolution"
    #: The :class:`~src.commons.enums.GrantScope` tier :func:`~src.core.scope.resolve_scope_tier`
    #: resolved (Issues #156/#171).
    SCOPE_TIER = "scope_tier"
    #: What a sub-``business`` tier narrows to for this principal (Issue #147/#171).
    INSTANCE_NARROWING = "instance_narrowing"
    #: The nav/surface gate — override lookup, required tier, verb or named action (Issues #146/#165).
    SURFACE_GATE = "surface_gate"


class TraceOutcome(StrEnum):
    """What a :class:`TraceStep` did to the decision in progress (Issue #174)."""

    #: A grant, override or level contributed something.
    MATCHED = "matched"
    #: Nothing here — the step that explains most surprises ("no grant on ``leases``, walked up").
    SKIPPED = "skipped"
    #: An ALLOW was held down by a DENY (deny-beats-allow) or by a required tier.
    CAPPED = "capped"
    #: The stage refused outright.
    DENIED = "denied"
    #: The stage's answer.
    FINAL = "final"


@dataclass(slots=True, frozen=True)
class TraceStep:
    """One step a resolver took, as ``docs/architecture/rbac-decision-transparency.md`` §3 specifies.

    ``stage``/``detail``/``outcome`` are the three fields the design names: the stage that produced
    it, a human-readable sentence an operator can read ("queues.call: ALLOW update (role:
    receptionist)"), and
    what it did to the decision.

    ``resource`` and ``value`` are **machine-readable anchors** on the same step, carried so a
    consumer never has to parse ``detail`` back apart. They are what lets
    :mod:`src.core.rbac_simulator` name the deciding grant without re-deriving *which* grant decided
    — a second implementation of the resolution rule, which is precisely what this issue exists to
    prevent. Both are ``None`` on a step that anchors to nothing.
    """

    stage: TraceStage
    detail: str
    outcome: TraceOutcome
    #: The resource key this step is about, when it is about one.
    resource: str | None = None
    #: The verb, tier or count this step settled on, as its wire string.
    value: str | None = None


#: What every resolver's ``trace`` parameter accepts: a caller-owned list steps are appended to, or
#: ``None`` (the default) for "explain nothing", which is what enforcement always passes.
Trace = list[TraceStep] | None


def _record(
    trace: Trace,
    stage: TraceStage,
    outcome: TraceOutcome,
    detail: str,
    *args: object,
    resource: str | None = None,
    value: str | None = None,
) -> None:
    """Append one :class:`TraceStep` to ``trace``, or do nothing at all when it is ``None``.

    The single guard every resolver funnels through, so "the enforcement path allocates nothing" is
    one ``if`` in one place rather than a condition repeated at every call site — where one would
    eventually be forgotten.

    ``detail`` is interpolated **lazily**, ``logging``-style (``"%s: ALLOW %s", key, verb``) rather
    than as an f-string at the call site. That is the whole point of the parameter shape: an
    f-string argument is built before the call and would make the enforcement path pay for a
    message it then discards, which is exactly the cost this issue promises not to add.
    """
    if trace is None:
        return
    trace.append(
        TraceStep(
            stage=stage,
            detail=detail % args if args else detail,
            outcome=outcome,
            resource=resource,
            value=value,
        )
    )


def granted_covers_required(
    granted_max: PermissionVerb | str | None,
    required: PermissionVerb | str,
) -> bool:
    """Return True if the granted maximum verb satisfies the required verb."""
    if granted_max is None:
        return False
    return permission_verb_rank(granted_max) >= permission_verb_rank(required)


def get_max_verb_for_role(
    db: Session,
    role: str,
    resource_key: str,
) -> str | None:
    """Load the verb of the ``role``'s own row on ``resource_key`` (None when no row).

    Returns the stored verb regardless of effect — an ALLOW or DENY row both report their
    ``max_verb`` here. Use :func:`get_effective_verb_for_role` for the resolved, deny-aware
    access level.
    """
    return db.execute(
        select(RolePermission.max_verb).where(
            RolePermission.role == role,
            RolePermission.resource == resource_key,
        )
    ).scalar_one_or_none()


def _load_hierarchy_adjacency(db: Session) -> dict[str, list[str]]:
    """Load every ``role_hierarchy`` edge as ``{role: [inherited roles]}`` in one query."""
    rows = db.execute(select(RoleHierarchy.role, RoleHierarchy.inherits_role)).all()
    adjacency: dict[str, list[str]] = {}
    for role, inherits_role in rows:
        adjacency.setdefault(role, []).append(inherits_role)
    return adjacency


def _closure_from_adjacency(role: str, adjacency: dict[str, list[str]]) -> set[str]:
    """Transitive closure of ``role`` over a prebuilt adjacency, cycle-guarded (BFS)."""
    seen: set[str] = set()
    stack: list[str] = [role]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(adjacency.get(current, ()))
    return seen


def role_closure(db: Session, role: str) -> set[str]:
    """Return ``role`` plus every role reachable through ``role_hierarchy`` (Issue #135).

    The transitive closure of the inheritance DAG, walked breadth-first with a visited set so a
    malformed cycle terminates instead of looping (the ancestor-walk guard used for the resource
    tree, applied to the role graph). With no edges seeded this is just ``{role}``, so flat roles
    resolve exactly as before.
    """
    return _closure_from_adjacency(role, _load_hierarchy_adjacency(db))


def role_inheritance_path(db: Session, role: str, granting_role: str) -> list[str]:
    """Return the shortest ``role_hierarchy`` path from ``role`` to ``granting_role`` (Issue #174).

    ``["nurse_doctor", "receptionist"]`` when ``nurse_doctor`` inherits ``receptionist``; ``[role]``
    when the two are the same role (the grant is held directly); ``[]`` when ``granting_role`` is not
    in ``role``'s closure at all.

    Breadth-first, so the path reported is the shortest one — an operator reading "inherited via
    nurse_doctor→receptionist" wants the edge that explains it, not an arbitrary walk through a
    diamond. Shares
    :func:`_load_hierarchy_adjacency` with :func:`role_closure`, so it can never disagree with the
    closure the resolvers actually use about which roles are reachable.
    """
    if role == granting_role:
        return [role]
    adjacency = _load_hierarchy_adjacency(db)
    seen: set[str] = {role}
    queue: list[list[str]] = [[role]]
    while queue:
        path = queue.pop(0)
        for nxt in adjacency.get(path[-1], ()):
            if nxt in seen:
                continue
            if nxt == granting_role:
                return [*path, nxt]
            seen.add(nxt)
            queue.append([*path, nxt])
    return []


def _effective_cache_populated(db: Session) -> bool:
    """Return True when the ``effective_role_permissions`` cache holds any rows (Issue #137).

    A single-row probe: on Postgres the statement-level trigger keeps the cache populated, so this
    is True and the request path reads it; under SQLite (tests) the cache stays empty, so this is
    False and callers fall back to the Python graph walk. Both paths resolve identically.
    """
    found = db.execute(
        select(EffectiveRolePermission.role).limit(1)
    ).scalar_one_or_none()
    return found is not None


def refresh_effective_role_permissions_py(db: Session) -> None:
    """Recompute the ``effective_role_permissions`` cache in Python (Issue #137).

    The portable twin of the Postgres ``refresh_effective_role_permissions()`` PL/pgSQL function:
    for every role, flatten its inheritance closure, pool the highest ALLOW verb and the strictest
    DENY verb **per resource**, and write one cache row per ``(role, resource, effect)``. Used to
    populate/verify the cache where triggers cannot run (SQLite tests), and as the parity twin of
    the SQL rebuild. Callers commit.

    Every ``role_permission`` resource key survives into the cache (Issue #154). The enum-typed
    predecessor silently dropped any row whose resource was not a ``PermissionResource`` member —
    a manifest-only key such as ``communications.messages.inbox`` — while the Postgres
    ``refresh_effective_role_permissions()`` it twins never filtered at all; keying on strings
    makes the two rebuilds agree.
    """
    db.execute(delete(EffectiveRolePermission))
    adjacency = _load_hierarchy_adjacency(db)
    role_names = list(db.execute(select(RbacRole.name)).scalars().all())

    # Own grants indexed by role, for the per-role closure union.
    own: dict[str, list[KeyedGrant]] = {}
    for role_value, resource_value, verb_value, effect_value in db.execute(
        select(
            RolePermission.role,
            RolePermission.resource,
            RolePermission.max_verb,
            RolePermission.effect,
        ).where(RolePermission.action.is_(None))  # cumulative rows only (Issue #140)
    ).all():
        typed = _keyed_grants_from_rows([(resource_value, verb_value, effect_value)])
        if typed:
            own.setdefault(role_value, []).append(typed[0])

    for role in role_names:
        closure = _closure_from_adjacency(role, adjacency)
        # Pool per resource across the closure: [max ALLOW rank, min DENY rank].
        pooled: dict[str, list[int | None]] = {}
        for member in closure:
            for res, verb, effect in own.get(member, ()):
                rank = permission_verb_rank(verb)
                agg = pooled.setdefault(res, [None, None])
                if effect == PermissionEffect.DENY.value:
                    if agg[1] is None or rank < agg[1]:
                        agg[1] = rank
                elif agg[0] is None or rank > agg[0]:
                    agg[0] = rank
        for res, (allow_rank, deny_rank) in pooled.items():
            if allow_rank is not None:
                db.add(
                    EffectiveRolePermission(
                        role=role,
                        resource=res,
                        verb=_verb_from_rank(allow_rank),
                        effect=PermissionEffect.ALLOW.value,
                    )
                )
            if deny_rank is not None:
                db.add(
                    EffectiveRolePermission(
                        role=role,
                        resource=res,
                        verb=_verb_from_rank(deny_rank),
                        effect=PermissionEffect.DENY.value,
                    )
                )


def active_role_assignments(
    db: Session,
    user_id: str,
    *,
    now: datetime | None = None,
) -> list[tuple[str, str | None, str | None]]:
    """Return a user's **active** ``(role, scope_type, scope_id)`` assignments (Issue #136).

    Active means not expired: ``expires_at is null or expires_at > now`` (lazy, query-time — no
    scheduler). ``now`` defaults to the current Africa/Johannesburg time (business timezone).
    """
    current = now or datetime.now(APP_TIMEZONE)
    rows = db.execute(
        select(
            UserRoleAssignment.role,
            UserRoleAssignment.scope_type,
            UserRoleAssignment.scope_id,
        ).where(
            UserRoleAssignment.user_id == user_id,
            or_(
                UserRoleAssignment.expires_at.is_(None),
                UserRoleAssignment.expires_at > current,
            ),
        )
    ).all()
    return [(role, scope_type, scope_id) for role, scope_type, scope_id in rows]


def _user_has_any_assignment(db: Session, user_id: str) -> bool:
    """Return True if the user has any ``user_roles`` row at all (active or not)."""
    found = db.execute(
        select(UserRoleAssignment.id)
        .where(UserRoleAssignment.user_id == user_id)
        .limit(1)
    ).scalar_one_or_none()
    return found is not None


def active_roles_for_user(
    db: Session,
    user: User,
    *,
    scope_type: str | None = None,
    scope_id: str | None = None,
    now: datetime | None = None,
) -> set[str]:
    """Union of a user's active, in-scope role names for RBAC resolution (Issue #136).

    An **unscoped** assignment (``scope_type is null``) always applies. A **scoped** assignment
    applies only when the check supplies a matching ``scope_type``/``scope_id`` — scope narrows
    *which instances* a role applies to. Expired assignments are already excluded.

    Compat fallback: a user with **no** ``user_roles`` rows at all resolves to the single-role
    mirror ``User.role`` (pre-migration state, or a user created directly). A user who *has* rows
    but none active/in-scope resolves to the empty set — an expired or out-of-scope-only user
    grants nothing.
    """
    roles: set[str] = set()
    for role, s_type, s_id in active_role_assignments(db, user.id, now=now):
        # An unscoped assignment always applies; a scoped one only within a matching scope.
        if s_type is None or (
            scope_type is not None and s_type == scope_type and s_id == scope_id
        ):
            roles.add(role)
    if not roles and not _user_has_any_assignment(db, user.id):
        return {user.role or UserRole.USER.value}
    return roles


def get_effective_verb_for_role(
    db: Session,
    role: str,
    resource_key: str,
) -> str | None:
    """Maximum verb on ``resource_key`` after inheritance + the resource tree, honouring DENY.

    Loads the union of grants over the role's inheritance closure (Issue #135) and delegates to
    :func:`effective_verb_over_keys` against the catalog's parent map, so this convenience wrapper
    and the ``require()`` enforcement path resolve identically: the highest inherited ALLOW verb,
    capped strictly below any inherited DENY (deny-beats-allow, Issue #134). A missing row on a
    child still inherits a parent's ALLOW; a DENY anywhere in the role closure or the resource tree
    still removes access.
    """
    grants = load_effective_grant_keys_for_roles(db, [role])
    return effective_verb_over_keys(grants, load_resource_parent_map(db), resource_key)


def _resource_permissions_for_roles(
    db: Session, roles: Iterable[str]
) -> dict[str, str]:
    """Return ``{resource_key: max_verb}`` over a role set, for every resource in the catalog.

    Walks :func:`load_resource_parent_map` — the DB catalog when seeded, the registered manifests
    otherwise — rather than a fixed enum (Issue #154), so a manifest-declared or admin-added
    resource reports its effective verb like any other. Omits resources with no effective verb.
    """
    grants = load_effective_grant_keys_for_roles(db, roles)
    parent_map = load_resource_parent_map(db)
    out: dict[str, str] = {}
    for key in parent_map:
        verb = effective_verb_over_keys(grants, parent_map, key)
        if verb is not None:
            out[key] = verb
    return out


def resource_permissions_for_role(
    db: Session,
    role: str,
) -> dict[str, str]:
    """Return ``{resource_key: max_verb}`` for API clients (e.g. GET /auth/me).

    Loads the role's inheritance closure once (Issue #135) and resolves every catalog resource in
    memory, so ``/auth/me`` reflects the union of inherited grants with deny-beats-allow.
    """
    return _resource_permissions_for_roles(db, [role])


def resource_permissions_for_user(db: Session, user: User) -> dict[str, str]:
    """Return ``{resource: max_verb}`` over a user's active **unscoped** assignments (Issue #136).

    ``/auth/me`` is a general "what can I do" map with no request scope, so only unscoped
    assignments contribute (scoped grants are contextual). Unions every such role's closure with
    deny-beats-allow; a user with no assignments falls back to the ``User.role`` mirror. Omits
    resources with no effective verb.
    """
    return _resource_permissions_for_roles(db, active_roles_for_user(db, user))


def role_inherits_cycle(
    db: Session,
    role: str,
    inherits_role: str,
) -> bool:
    """Return True if adding ``role -> inherits_role`` would create an inheritance cycle.

    A cycle appears exactly when ``role`` is already in the closure of ``inherits_role`` (so the
    new edge would close a loop back to ``role``). A self-edge is a trivial cycle. Used by the
    admin API to reject a cycle with ``400`` before it is ever persisted (Issue #135).
    """
    if role == inherits_role:
        return True
    return role in role_closure(db, inherits_role)


def raise_forbidden(
    resource_key: str,
    required: PermissionVerb | str,
) -> None:
    """Raise 403 with a consistent JSON-serializable detail body."""
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "message": "Insufficient permission for this action.",
            "code": RBAC_FORBIDDEN_CODE,
            "resource": str(resource_key),
            "required_verb": str(required),
        },
    )


def raise_forbidden_action(
    resource_key: str,
    action_key: str,
) -> None:
    """Raise 403 for a missing **named action** (Issue #140) with the same code as the verb path.

    The detail carries ``required_action`` (not ``required_verb``) so a client can tell a
    named-action refusal from a cumulative-verb one.
    """
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "message": "Insufficient permission for this action.",
            "code": RBAC_FORBIDDEN_CODE,
            "resource": str(resource_key),
            "required_action": action_key,
        },
    )


def _resolve_user_for_rbac(db: Session, current_user: dict[str, Any]) -> User:
    """Return the signed-in ``User`` row; raise 401 if unauthenticated, unknown or switched off.

    Delegates to :func:`src.core.security.resolve_active_user`, the one identity resolution every
    authenticated path shares (Issue 15), so a deactivated account is refused here too.
    """
    from src.core.security import resolve_active_user

    return resolve_active_user(db, current_user)


def _resolve_role_for_rbac(db: Session, current_user: dict[str, Any]) -> str:
    """Return the signed-in user's compat single-role string; raise 401 if missing.

    Retained for callers that still want the ``User.role`` mirror; RBAC enforcement now resolves
    the **union** of active assignments (see :func:`ensure_permission_key`).
    """
    return _resolve_user_for_rbac(db, current_user).role or UserRole.USER.value


# --------------------------------------------------------------------------------------
# Named non-cumulative actions & (resource, action) enforcement (Issue #140, M25)
# --------------------------------------------------------------------------------------
#
# A named action (``sign``, ``approve``) cannot be expressed on the cumulative ladder, so it is a
# first-class ``(resource, action)`` grant (``role_permission`` rows with ``action`` set) resolved
# here — deny-beats-allow, unioned over the role's inheritance closure, with the opt-in
# ``applies_to_descendants`` cascade folded in via ``resource_descendants``. This path is parallel
# to (never replaces) the cumulative-verb path, so existing CRUD-gated routes are untouched.

# One named-action grant: (resource_key, action_key, effect, applies_to_descendants).
NamedGrant = tuple[str, str, PermissionEffect, bool]


def load_named_action_grants_for_roles(
    db: Session, roles: Iterable[str]
) -> list[NamedGrant]:
    """Load the union of **named-action** grants over the roles' inheritance closures (Issue #140).

    Reads only ``role_permission`` rows with ``action`` set (cumulative rows are ignored here) over
    each role's ``role_hierarchy`` closure, so a named action inherited through a parent role counts.
    A stale/unknown effect defaults to ALLOW (the column default), matching the cumulative path.
    """
    role_list = list(dict.fromkeys(roles))
    if not role_list:
        return []
    closure: set[str] = set()
    for role in role_list:
        closure |= role_closure(db, role)
    if not closure:
        return []
    rows = db.execute(
        select(
            RolePermission.resource,
            RolePermission.action,
            RolePermission.effect,
            RolePermission.applies_to_descendants,
        ).where(
            RolePermission.role.in_(closure),
            RolePermission.action.is_not(None),
        )
    ).all()
    grants: list[NamedGrant] = []
    for resource_value, action_value, effect_value, cascade in rows:
        try:
            effect = PermissionEffect(effect_value or PermissionEffect.ALLOW.value)
        except ValueError:
            continue
        grants.append((resource_value, action_value, effect, bool(cascade)))
    return grants


def load_resource_descendants(db: Session) -> set[tuple[str, str]]:
    """Return the resource closure as ``(ancestor_key, descendant_key)`` pairs (cache-aware).

    Reads the precomputed ``resource_descendants`` table (Issue #139) when populated (Postgres, or a
    test that seeded it), translating ids to keys; otherwise falls back to the enum-derived closure
    (:func:`catalog_resource_closure`) so the cascade resolves identically on SQLite. Mirrors the
    effective-cache's populated-or-walk pattern.
    """
    rows = db.execute(
        select(ResourceDescendant.ancestor_id, ResourceDescendant.descendant_id)
    ).all()
    if not rows:
        return catalog_resource_closure()
    key_by_id: dict[str, str] = {
        row[0]: row[1] for row in db.execute(select(Resource.id, Resource.key)).all()
    }
    return {
        (key_by_id[ancestor], key_by_id[descendant])
        for ancestor, descendant in rows
        if ancestor in key_by_id and descendant in key_by_id
    }


def named_action_allowed(
    grants: Iterable[NamedGrant],
    descendants: set[tuple[str, str]],
    resource_key: str,
    action_key: str,
) -> bool:
    """Resolve whether a set of named grants ALLOWs ``(resource_key, action_key)`` (deny-wins).

    Pure and side-effect free — the correctness oracle the DB path delegates to. A grant on the
    resource itself always applies; a grant on an **ancestor** applies only when it is flagged
    ``applies_to_descendants`` (the opt-in cascade) and the pair is in the resource closure. ALLOW
    and DENY are pooled across every applicable grant, and **DENY wins** — so a child DENY overrides
    a cascaded parent ALLOW, exactly as the cumulative resolver caps below a DENY.
    """
    allow = False
    deny = False
    for grant_resource, grant_action, effect, cascade in grants:
        if grant_action != action_key:
            continue
        if grant_resource == resource_key:
            applies = True
        elif cascade and (grant_resource, resource_key) in descendants:
            # Cascade only *down* to a strict descendant (the self-pair is the branch above).
            applies = grant_resource != resource_key
        else:
            applies = False
        if applies:
            if effect is PermissionEffect.DENY:
                deny = True
            elif effect is PermissionEffect.ALLOW:
                allow = True
    return allow and not deny


def role_has_named_action(
    db: Session,
    roles: Iterable[str],
    resource_key: str,
    action_key: str,
) -> bool:
    """Return True when the roles' union grants the named ``(resource, action)`` (Issue #140)."""
    grants = load_named_action_grants_for_roles(db, roles)
    if not grants:
        return False
    return named_action_allowed(
        grants, load_resource_descendants(db), resource_key, action_key
    )


def ensure_permission_action(
    db: Session,
    current_user: dict[str, Any],
    resource_key: str,
    action: PermissionVerb | PermissionAction | str,
    *,
    scope_type: str | None = None,
    scope_id: str | None = None,
) -> None:
    """Ensure a ``(resource, action)`` capability — CRUD verb *or* named action (Issue #140).

    The single ``(resource, action)`` entrypoint: a cumulative CRUD verb dispatches to the verb
    path (:func:`ensure_permission_key`), a named action to
    :func:`ensure_named_action_permission_key`.
    """
    action_key = str(action)
    if is_cumulative_action(action_key):
        ensure_permission_key(
            db,
            current_user,
            resource_key,
            action_key,
            scope_type=scope_type,
            scope_id=scope_id,
        )
    else:
        ensure_named_action_permission_key(
            db,
            current_user,
            resource_key,
            action_key,
            scope_type=scope_type,
            scope_id=scope_id,
        )


# --------------------------------------------------------------------------------------
# Seed data (shared by the Alembic migration and tests)
# --------------------------------------------------------------------------------------


def default_system_roles() -> list[tuple[str, str]]:
    """Return the ``(name, description)`` roles every deployment is seeded with.

    The kernel's two system roles and ClinicQ's five (Issue 18). ``make seed-rbac`` inserts any that
    are missing (``sync_system_roles``); the golden decision snapshot pins every one of them.
    """
    return [
        (UserRole.ADMIN.value, "Full application access; system role."),
        (UserRole.USER.value, "Standard user; system role."),
        (
            UserRole.PATIENT.value,
            "A patient acting on their own record, through a phone session.",
        ),
        (
            UserRole.RECEPTIONIST.value,
            "Front desk: issues and moves tickets and calls next on any queue at their clinic.",
        ),
        (
            UserRole.NURSE_DOCTOR.value,
            "Calls next and completes visits on the queues they are assigned to.",
        ),
        (
            UserRole.CLINIC_MANAGER.value,
            "Runs their clinic: profile, settings, display mode, staff, queues and reports.",
        ),
        (
            UserRole.PLATFORM_ADMIN.value,
            "ClinicQ operator: onboarding, support and aggregate reports across clinics.",
        ),
    ]


def default_role_permissions() -> list[tuple[str, str, PermissionVerb]]:
    """Return the kernel's system ``(role, resource, max_verb)`` grants, seeded by ``make seed-rbac``.

    Admin holds DELETE on ``users``, ``logs``, ``rbac`` and the dashboard; standard users get READ
    on the dashboard. Everything else, the ClinicQ roles included, is declared in manifests.
    """
    admin = UserRole.ADMIN.value
    user = UserRole.USER.value
    return [
        (admin, "dashboard", PermissionVerb.DELETE),
        (admin, "users", PermissionVerb.DELETE),
        (admin, "logs", PermissionVerb.DELETE),
        (admin, "rbac", PermissionVerb.DELETE),
        (user, "dashboard", PermissionVerb.READ),
    ]


# --------------------------------------------------------------------------------------
# Portal role permission matrix (Issue #58 / M9)
# --------------------------------------------------------------------------------------
#
# The four portals are only as separate as the permissions behind them. This matrix is the
# single source of truth for what each role may do, resource by resource; migration ``0026``
# seeds it and ``tests/test_rbac_matrix.py`` verifies the seeded data equals it. RBAC decides
# *what* verb a role holds; ownership is the documented second gate that decides *whose* rows
# the role may touch (see ``docs/architecture/rbac-matrix.md``).
#
# Verbs are cumulative (``READ < CREATE < UPDATE < DELETE``): a listed verb implies every lower
# verb on the same resource. A resource omitted for a role means no grant (no effective verb,
# subject to parent->child inheritance in the resource tree).


# The tier each **seeded** role's shipped grants carry (Issue #172, M28).
#
# This is *seed data*, not a derivation. Until Issue #172 the same three role names lived here as
# ``PORTAL_ONLY_ROLES``, behind ``is_management_role()``, and every runtime question about how wide
# a caller reaches was answered by asking whether their role's name was on that list — so a brand
# new custom role was "management" by default, purely for not being on it, and a grant an admin
# created for it was stored at the **widest** tier silently. That derivation is deleted; a role's
# name now decides nothing, anywhere.
#
# What remains is a table saying what the roles this project *ships* are seeded as, which is an
# ordinary property of seed data — the same kind of statement ``PORTAL_ROLE_MATRIX`` makes about
# verbs. A role not listed here (every custom role an admin creates) is seeded at
# :data:`~src.commons.enums.GrantScope.OWN`, the narrowest tier: see :func:`default_grant_scope`.
SEEDED_ROLE_GRANT_SCOPES: dict[str, GrantScope] = {
    # The kernel's system roles: whole-platform consoles and the base signed-in surfaces.
    UserRole.ADMIN.value: GrantScope.BUSINESS,
    UserRole.USER.value: GrantScope.BUSINESS,
    # ClinicQ (Issue 18). Staff reach what their assignments name: the sites they hold a role at
    # (and, for a nurse, the queues they are assigned to). A manifest grant may narrow further,
    # as the nurse's call-next grant does (``own``: only the queues assigned to them).
    UserRole.RECEPTIONIST.value: GrantScope.ASSIGNED,
    UserRole.NURSE_DOCTOR.value: GrantScope.ASSIGNED,
    UserRole.CLINIC_MANAGER.value: GrantScope.ASSIGNED,
    # The operator reaches every clinic; reading one they are not assigned to is explicit and
    # audited by the site guard (Issue 19), because this role is not scope-exempt.
    UserRole.PLATFORM_ADMIN.value: GrantScope.BUSINESS,
    # A patient reaches only their own record.
    UserRole.PATIENT.value: GrantScope.OWN,
}


def default_grant_scope(role: str | None) -> GrantScope:
    """Return the :class:`GrantScope` a **newly created** grant carries (Issues #156, #172).

    :data:`~src.commons.enums.GrantScope.OWN` — the narrowest tier — for every role, without
    exception and without reading the role's name.

    **This is the deny-first, own-first rule** (Issue #172, M28). The engine has always been
    deny-first for *verbs*: no grant resolves to no access, and a DENY caps every ALLOW below it.
    It was allow-first for *breadth*, because this function returned ``business`` for any role whose
    name was not one of three literals — and it is the ORM column default, so a grant an admin
    created from the console for a new ``agent`` role was stored at the widest tier silently, handing
    them the whole portfolio. A grant now starts at the narrowest breadth that can satisfy it, and
    widening is an explicit, audited act.

    Not to be confused with :func:`seeded_grant_scope`, which answers a different question: what
    tier the roles this project *ships* are seeded at. That one is seed data an operator can edit
    afterwards; this one is what a grant with nothing said about it means.
    """
    return GrantScope.OWN


def seeded_grant_scope(role: str | None) -> GrantScope:
    """Return the tier ``role``'s **shipped seed** grants carry (Issue #172, M28).

    A lookup in :data:`SEEDED_ROLE_GRANT_SCOPES`, falling back to :func:`default_grant_scope` — the
    narrowest tier — for any role not in it, which is every role an admin creates. Read by the seed
    paths only:

    * :func:`~src.core.rbac_manifest_sync.sync_default_grants`, for a shipped grant whose manifest
      leaves ``scope`` unset;
    * :func:`default_role_permission_scopes`, the rendered ``(role, resource) -> scope`` map the
      matrix generator and the parity suites read.

    Never an authorization input, and never the default for a console-created grant: those go
    through :func:`default_grant_scope`. The split is the point — "what did we ship" and "what does
    a new grant mean" were the same function until Issue #172, which is how the widest tier became
    the silent default.
    """
    return SEEDED_ROLE_GRANT_SCOPES.get(
        (role or "").strip().lower(), default_grant_scope(role)
    )


# --------------------------------------------------------------------------------------
# M25 — DB resource/action/permission catalog (Issue #139)
# --------------------------------------------------------------------------------------
#
# The catalog lives in the ``resources``/``actions``/``permissions`` tables. Since Issue #154 (M27)
# its shape is declared entirely by the module manifests registered in
# ``src.core.rbac_manifest_registry`` — the ``PermissionResource`` enum these helpers were once
# built from is permanently deleted, and the historical Alembic seeds (``0052`` and friends) carry
# their own frozen copies of the enum-era rows rather than importing live application data, so a
# migration cannot change meaning as modules evolve. The Python twins below mirror the Postgres
# ``refresh_resource_descendants()`` PL/pgSQL for SQLite (tests), exactly as the M24 effective-cache
# twin (:func:`refresh_effective_role_permissions_py`) mirrors its rebuild function.


def catalog_display_name(key: str) -> str:
    """Return a human label for a resource/action ``key`` (dotted segments title-cased).

    ``payment.late_fees`` -> ``Payment / Late Fees``; ``read`` -> ``Read``. Deterministic so the
    seed migration and the parity guard derive the same names from the same keys.
    """
    return " / ".join(segment.replace("_", " ").title() for segment in key.split("."))


def default_actions() -> list[tuple[str, str, str]]:
    """Return the seeded ``actions`` catalog as ``(key, name, description)``.

    The four cumulative CRUD verbs (``read``/``create``/``update``/``delete``), seeded so nothing
    changes behaviourally; genuinely non-cumulative actions (``sign``, ``approve``) arrive in Issue
    #140. ``key`` mirrors a :class:`~src.commons.enums.PermissionVerb` value.
    """
    return [
        (PermissionVerb.READ.value, "Read", "View or list records."),
        (PermissionVerb.CREATE.value, "Create", "Create a new record."),
        (PermissionVerb.UPDATE.value, "Update", "Modify an existing record."),
        (PermissionVerb.DELETE.value, "Delete", "Remove or soft-delete a record."),
    ]


def default_resources() -> list[tuple[str, str, str | None, str | None]]:
    """Return the full ``resources`` catalog as ``(key, name, parent_key, description)``.

    Built from every manifest registered in ``ALL_MANIFESTS`` (Issue #154) — each module declares
    its own subtree in its own ``rbac_manifest.py``, so this reproduces the code catalog exactly:
    keys, names and the parent hierarchy, with each manifest's root emitted before its descendants.
    ``parent_key`` is ``None`` for a root resource. A lazy import: manifests import helpers from
    this module, so a module-level import would be circular.
    """
    from src.core.rbac_manifest_registry import manifest_resource_rows

    return manifest_resource_rows()


def permission_key(resource_key: str, action_key: str) -> str:
    """Compose the human-readable permission key ``resource:action``.

    Not a stored column — Postgres generated columns cannot reference other tables
    (``.btk/RBAC/rbac-design.md`` §6) — so it is computed here from the joined resource + action
    keys wherever a single label is needed (e.g. the admin grid, Issue #141).
    """
    return f"{resource_key}:{action_key}"


def catalog_resource_closure() -> set[tuple[str, str]]:
    """Return the resource-tree closure as ``(ancestor_key, descendant_key)`` pairs (incl. self).

    The exact contents ``resource_descendants`` must hold for the declared tree: every resource
    paired with itself, plus every ``(ancestor, descendant)`` implied by the manifest-declared
    parent map (Issue #154). The correctness oracle the SQL rebuild and the Python twin are both
    checked against. The ancestor walk is cycle-guarded, like every other parent walk here.
    """
    from src.core.rbac_manifest_registry import manifest_parent_map

    parents = manifest_parent_map()
    pairs: set[tuple[str, str]] = set()
    for key in parents:
        pairs.add((key, key))
        seen = {key}
        current = parents[key]
        while current is not None and current not in seen:
            pairs.add((current, key))
            seen.add(current)
            current = parents.get(current)
    return pairs


def _cumulative_actions_by_resource() -> dict[str, tuple[str, ...]]:
    """Return ``{resource_key: cumulative actions}`` as the registered manifests declare them.

    Almost every node inherits the four CRUD verbs; ``reporting`` restricts itself to ``("read",)``
    (Issue #153 — nothing writes to a report), so the cross-product below must be per-resource
    rather than "every resource x every action".
    """
    from src.core.rbac_manifest import iter_resources
    from src.core.rbac_manifest_registry import ALL_MANIFESTS

    return {
        node.full_key: node.actions
        for manifest in ALL_MANIFESTS
        for node in iter_resources(manifest)
    }


def seed_resource_catalog(db: Session) -> None:
    """Seed ``resources``/``actions``/``permissions`` in Python from the registered manifests.

    Idempotent by natural key (resource/action ``key`` and the ``(resource_id, action_id)`` pair):
    re-running inserts nothing. Used under SQLite (tests) and in dev, where the Alembic seeds do
    not run. Callers commit. Resources are inserted first (parents wired by ``key``), then each
    resource is paired with the cumulative actions its own manifest node declares.

    Since Issue #154 this reads the manifests rather than the deleted ``PermissionResource`` enum,
    so it is a twin of the *deploy* (``alembic upgrade head && python -m
    src.core.rbac_manifest_sync``) rather than of migration ``0052`` alone — which is what a test
    or a dev database actually wants. It deliberately does **not** seed ``nav_gate_overrides``
    defaults; that stays :func:`~src.core.rbac_manifest_sync.sync_module_manifest`'s job, so a test
    that seeds the catalog does not silently acquire DB-authoritative nav gates.
    """
    resource_by_key: dict[str, Resource] = {
        r.key: r for r in db.execute(select(Resource)).scalars().all()
    }
    rows = default_resources()
    for key, name, _parent_key, description in rows:
        if key not in resource_by_key:
            row = Resource(key=key, name=name, description=description, is_system=True)
            db.add(row)
            resource_by_key[key] = row
    db.flush()  # assign ids before wiring parents
    for key, _name, parent_key, _description in rows:
        if parent_key is not None:
            resource_by_key[key].parent_id = resource_by_key[parent_key].id

    action_by_key: dict[str, Action] = {
        a.key: a for a in db.execute(select(Action)).scalars().all()
    }
    for key, name, description in default_actions():
        if key not in action_by_key:
            action = Action(key=key, name=name, description=description, is_system=True)
            db.add(action)
            action_by_key[key] = action
    db.flush()

    actions_by_resource = _cumulative_actions_by_resource()
    existing_pairs = {
        (p.resource_id, p.action_id)
        for p in db.execute(select(Permission)).scalars().all()
    }
    for key, resource in resource_by_key.items():
        for action_key in actions_by_resource.get(key, ()):
            declared_action = action_by_key.get(action_key)
            if declared_action is None:
                # A named action (``sign``, ``send``, ...) this function does not seed; those are
                # layered on by :func:`seed_named_catalog`.
                continue
            pair = (resource.id, declared_action.id)
            if pair not in existing_pairs:
                db.add(
                    Permission(resource_id=resource.id, action_id=declared_action.id)
                )
                existing_pairs.add(pair)


def refresh_resource_descendants_py(db: Session) -> None:
    """Rebuild ``resource_descendants`` from ``resources`` in Python (twin of the PL/pgSQL fn).

    The portable twin of the Postgres ``refresh_resource_descendants()``: truncate and repopulate
    the closure — every resource paired with itself and each of its transitive descendants. Used to
    populate/verify the cache under SQLite (tests), where the statement-level trigger cannot run.
    The walk is cycle-guarded (a malformed parent chain terminates). Callers commit.

    **Do not call this directly from production code — use :func:`rebuild_resource_closure`.** On
    Postgres this collides with the ``trg_resources_changed`` trigger that already owns the closure;
    see that function's docstring for the failure mode.
    """
    db.execute(delete(ResourceDescendant))
    rows = db.execute(select(Resource.id, Resource.parent_id)).all()
    children: dict[str, list[str]] = {}
    for resource_id, parent_id in rows:
        if parent_id is not None:
            children.setdefault(parent_id, []).append(resource_id)
    for ancestor_id, _parent_id in rows:
        stack = [ancestor_id]
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            db.add(ResourceDescendant(ancestor_id=ancestor_id, descendant_id=current))
            stack.extend(children.get(current, ()))


def rebuild_resource_closure(db: Session) -> None:
    """Bring ``resource_descendants`` up to date after a resource-tree change, on any dialect.

    The one entrypoint production code should call. Which engine maintains the closure differs by
    dialect, and getting that wrong is not a no-op:

    * **Postgres** owns it in the database. ``0052``'s statement-level ``AFTER INSERT OR UPDATE OR
      DELETE`` trigger on ``resources`` (``trg_resources_changed``) rebuilds the whole closure — and
      the M24 effective-permission cache with it — after *every* statement that touches the tree. So
      by the time any caller here returns, the closure is already correct and there is nothing to do.
      Running the Python twin anyway raises ``IntegrityError`` on ``pk_resource_descendants``: the
      twin's ``DELETE`` is flushed immediately, but its re-``INSERT``\\ s are ORM-pending and land
      *after* the next ``resources`` write re-fires the trigger, so the two rebuilds collide on the
      rows they both just wrote.
    * **SQLite** (tests) has no trigger support, so the twin is the only thing that maintains it.

    Callers commit.
    """
    if db.get_bind().dialect.name != "postgresql":
        refresh_resource_descendants_py(db)


# --------------------------------------------------------------------------------------
# Named-action catalog & grants (Issue #140) — seeded on top of the Issue #139 CRUD catalog
# --------------------------------------------------------------------------------------


def default_named_actions() -> list[tuple[str, str, str]]:
    """Return the named (non-cumulative) ``actions`` seeded by M25/M27 as ``(key, name, description)``."""
    return [
        (
            PermissionAction.SIGN.value,
            "Sign",
            "Execute a signature. Not a cumulative rung above delete: a role may sign "
            "without being able to delete, and the reverse.",
        ),
        (
            PermissionAction.APPROVE.value,
            "Approve",
            "Approve a decision. A capability of its own, not 'more than' updating.",
        ),
        (
            PermissionAction.REJECT.value,
            "Reject",
            "Reject a decision. The counterpart of approve, and equally not a cumulative verb.",
        ),
    ]


def default_named_permissions() -> list[tuple[str, str]]:
    """Return the ``(resource_key, action_key)`` catalog rows for every named action.

    Derived from the registered manifests (Issue #154), each of which declares its own
    ``named_actions``. Sorted for a deterministic seed order. Deriving the list from the manifests
    rather than from a table here is what makes this Python twin and the deploy agree — a module
    that adds a named action needs no edit outside its own directory.
    """
    from src.core.rbac_manifest import named_action_keys
    from src.core.rbac_manifest_registry import ALL_MANIFESTS

    return sorted(
        {pair for manifest in ALL_MANIFESTS for pair in named_action_keys(manifest)}
    )


def default_named_action_grants() -> list[tuple[str, str, str, str, bool]]:
    """Return the seeded named-action grants as ``(role, resource, action, effect, cascade)``.

    Empty in the kernel, and that is the design rather than a gap: a named action is a *domain*
    capability — sign this, approve that — so the module that owns the verb declares it on its own
    manifest's ``named_actions`` and ships the grant there, through
    :attr:`~src.core.rbac_manifest.ModuleManifest.grants`. Nothing outside that module is edited,
    which is the point of the manifest framework.

    This helper stays as the seam for a grant that genuinely cannot live on one manifest — one that
    spans two modules, say. Rows are ALLOW and scoped (no descendant cascade), and should target a
    sub-resource carrying no cumulative grant, so a named action never collides with the CRUD rows
    a role inherits from a parent.
    """
    return []


def seed_named_catalog(db: Session) -> None:
    """Seed the named actions + their ``(resource, action)`` permissions (Python twin, idempotent).

    Layered on top of :func:`seed_resource_catalog` (the CRUD catalog); used under SQLite (tests).
    Callers commit. Any action a manifest declares that :func:`default_named_actions` has no
    hand-written description for (``send``, Issue #145) is created from its key alone, exactly as
    :func:`~src.core.rbac_manifest_sync.sync_module_manifest` does — otherwise the manifest-derived
    :func:`default_named_permissions` would reference an action row that does not exist.
    """
    action_keys = {a.key for a in db.execute(select(Action)).scalars().all()}
    for key, name, description in default_named_actions():
        if key not in action_keys:
            db.add(Action(key=key, name=name, description=description, is_system=True))
            action_keys.add(key)
    for _resource_key, action_key in default_named_permissions():
        if action_key not in action_keys:
            db.add(
                Action(
                    key=action_key,
                    name=catalog_display_name(action_key),
                    is_system=True,
                )
            )
            action_keys.add(action_key)
    db.flush()

    resource_id_by_key = {
        r.key: r.id for r in db.execute(select(Resource)).scalars().all()
    }
    action_id_by_key = {a.key: a.id for a in db.execute(select(Action)).scalars().all()}
    existing_pairs = {
        (p.resource_id, p.action_id)
        for p in db.execute(select(Permission)).scalars().all()
    }
    for resource_key, action_key in default_named_permissions():
        pair = (resource_id_by_key[resource_key], action_id_by_key[action_key])
        if pair not in existing_pairs:
            db.add(Permission(resource_id=pair[0], action_id=pair[1]))


def seed_named_action_grants(db: Session) -> None:
    """Seed the named-action ``role_permission`` grants (Python twin of the ``0053`` seed).

    Resolves each grant's ``permission_id`` from the seeded catalog. Idempotent on
    ``(role, resource, action)``; used under SQLite (tests). Callers commit.
    """
    permission_id_by_pair: dict[tuple[str, str], str] = {}
    for permission, resource, action in db.execute(
        select(Permission, Resource.key, Action.key)
        .join(Resource, Permission.resource_id == Resource.id)
        .join(Action, Permission.action_id == Action.id)
    ).all():
        permission_id_by_pair[(resource, action)] = permission.id

    existing = {
        (rp.role, rp.resource, rp.action)
        for rp in db.execute(
            select(RolePermission).where(RolePermission.action.is_not(None))
        )
        .scalars()
        .all()
    }
    for (
        role,
        resource_key,
        action_key,
        effect,
        cascade,
    ) in default_named_action_grants():
        if (role, resource_key, action_key) in existing:
            continue
        db.add(
            RolePermission(
                role=role,
                resource=resource_key,
                action=action_key,
                max_verb=None,
                scope=seeded_grant_scope(role).value,
                permission_id=permission_id_by_pair.get((resource_key, action_key)),
                effect=effect,
                applies_to_descendants=cascade,
            )
        )


# --------------------------------------------------------------------------------------
# Communications named-action grants (Issue #145, M26)
#
# ``communications.alerts.drafts:send`` is a manifest-only resource/action pair (declared in
# ``src.modules.communications.rbac_manifest``, and (unlike ``sign``/``approve``) never a
# ``PermissionAction``
# enum member), created in the DB catalog by ``sync_module_manifest`` at deploy time — *after*
# migrations run. A migration that seeded this grant by joining through the ``permissions`` catalog
# (the ``0053`` pattern above) would therefore find nothing to join against. It doesn't need to:
# ``role_has_named_action`` resolves purely off ``role_permission.resource``/``.action`` string
# columns, never through ``permission_id`` (which exists for audit/FK purposes only), so the grant
# can be seeded with ``permission_id`` left NULL, independent of catalog-seed ordering.
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# Issue #167 (M28) — the grants the ownership-only portal mutations now require
# --------------------------------------------------------------------------------------
#
# Five state-changing web routes carried no RBAC check at all: only authentication, CSRF and an
# ownership lookup. "Ownership implies authorization" is the same substitution M28 exists to remove
# — an explicit DENY could not stop any of them, because no code path consulted a grant. Each now
# checks the grant for the resource **it actually mutates**, in addition to (never instead of) the
# ownership check. Two of the five needed a grant the seeds did not yet carry.


# --------------------------------------------------------------------------------------
# Catalog-aware (string-keyed) resolution — the one and only resolver (Issues #141/#149/#154)
# --------------------------------------------------------------------------------------
#
# There is no second, enum-typed resolver any more: enforcement, the admin permissions grid and the
# nav layer all resolve over resource *keys* and the catalog's parent map, so a resource an admin
# adds at runtime or a module declares in its manifest resolves exactly like a long-standing one.

KeyedGrant = tuple[str, str, str]  # (resource_key, verb_value, effect_value)


def load_resource_parent_map(db: Session) -> dict[str, str | None]:
    """Return ``{resource_key: parent_key}`` from the DB catalog, or the manifests as a fallback.

    Reads the ``resources`` table when populated (the source of truth from Issue #139); otherwise
    falls back to the manifest-declared parent map (Issue #154 — the successor to the deleted
    ``PERMISSION_RESOURCE_PARENT``) so resolution is identical before the catalog is seeded (a
    fresh dev DB, a test that seeds nothing, or the production window between
    ``alembic upgrade head`` and ``python -m src.core.rbac_manifest_sync``). Either way, any
    manifest-declared key the DB does not know about yet is filled in from the manifests, so a
    freshly declared resource still resolves its parent chain before the next sync runs.
    """
    from src.core.rbac_manifest_registry import manifest_parent_map

    rows = db.execute(select(Resource.key, Resource.parent_id, Resource.id)).all()
    key_by_id = {row.id: row.key for row in rows}
    parent_map: dict[str, str | None] = {
        row.key: (key_by_id.get(row.parent_id) if row.parent_id is not None else None)
        for row in rows
    }
    for key, parent in manifest_parent_map().items():
        parent_map.setdefault(key, parent)
    return parent_map


def _keyed_grants_from_rows(rows: Iterable[Any]) -> list[KeyedGrant]:
    """Convert cumulative ``(resource, max_verb, effect)`` rows to keyed grant triples.

    Every resource key survives, including one an admin added at runtime that no manifest declares.
    Rows with an unknown verb are skipped (a named action leaks no cumulative rung).
    """
    verbs = {verb.value for verb in PermissionVerb}
    grants: list[KeyedGrant] = []
    for resource_value, verb_value, effect_value in rows:
        if verb_value not in verbs:
            continue
        grants.append(
            (resource_value, verb_value, effect_value or PermissionEffect.ALLOW.value)
        )
    return grants


def load_role_grant_keys(db: Session, role: str) -> list[KeyedGrant]:
    """Load a role's own cumulative grants as ``(resource_key, verb, effect)`` (no enum filter)."""
    rows = db.execute(
        select(
            RolePermission.resource,
            RolePermission.max_verb,
            RolePermission.effect,
        ).where(RolePermission.role == role, RolePermission.action.is_(None))
    ).all()
    return _keyed_grants_from_rows(rows)


def _cache_effective_grant_keys_for_roles(
    db: Session, roles: Iterable[str]
) -> list[KeyedGrant]:
    """Read the precomputed cache for ``roles`` without enum-filtering (Issue #149).

    The keyed twin of :func:`_cache_effective_grants_for_roles`: same cache rows, but every
    resource key survives (a runtime-added catalog resource included), not only enum members.
    """
    role_list = list(roles)
    if not role_list:
        return []
    rows = db.execute(
        select(
            EffectiveRolePermission.resource,
            EffectiveRolePermission.verb,
            EffectiveRolePermission.effect,
        ).where(EffectiveRolePermission.role.in_(role_list))
    ).all()
    return _keyed_grants_from_rows(rows)


def _walk_effective_grant_keys_for_roles(
    db: Session, roles: Iterable[str]
) -> list[KeyedGrant]:
    """Union of keyed cumulative grants over the roles' inheritance closures, by graph walk.

    The keyed twin of :func:`_walk_effective_grants_for_roles`, used when the effective-cache is
    not populated (dev / SQLite).
    """
    closure: set[str] = set()
    for role in roles:
        closure |= role_closure(db, role)
    if not closure:
        return []
    rows = db.execute(
        select(
            RolePermission.resource,
            RolePermission.max_verb,
            RolePermission.effect,
        ).where(
            RolePermission.role.in_(closure),
            RolePermission.action.is_(None),
        )
    ).all()
    return _keyed_grants_from_rows(rows)


def load_effective_grant_keys_for_roles(
    db: Session, roles: Iterable[str]
) -> list[KeyedGrant]:
    """Load the union of keyed effective grants over several roles' closures (Issue #149).

    Cache-aware (Postgres) with a Python graph-walk fallback (dev / SQLite). Every resource key
        survives, so :func:`~src.api.rbac_deps.require` resolves correctly for any DB-catalog resource,
        manifest-declared or admin-added at runtime.
    """
    role_list = list(dict.fromkeys(roles))
    if not role_list:
        return []
    if _effective_cache_populated(db):
        return _cache_effective_grant_keys_for_roles(db, role_list)
    return _walk_effective_grant_keys_for_roles(db, role_list)


def load_effective_grant_keys_for_role(db: Session, role: str) -> list[KeyedGrant]:
    """Load the union of cumulative grants over ``role``'s inheritance closure, keyed by string.

    Walks the ``role_hierarchy`` closure directly (no effective-permission cache read) and unions
        each role's own cumulative rows. Used only by the admin grid (Issue #141), which renders a
        role's grants as stored rather than as the cache last flattened them.
    """
    closure = role_closure(db, role)
    if not closure:
        return []
    rows = db.execute(
        select(
            RolePermission.resource,
            RolePermission.max_verb,
            RolePermission.effect,
        ).where(RolePermission.role.in_(closure), RolePermission.action.is_(None))
    ).all()
    return _keyed_grants_from_rows(rows)


def effective_verb_over_keys(
    grants: Iterable[KeyedGrant],
    parent_by_key: dict[str, str | None],
    resource_key: str,
    *,
    trace: Trace = None,
) -> str | None:
    """Resolve the effective CRUD verb on ``resource_key`` over keyed grants + a DB parent map.

    The cumulative-verb resolver: pools the highest ALLOW and the
    strictest DENY per resource, then walks ``resource_key`` toward the tree root via
    ``parent_by_key``, capping the highest inherited ALLOW strictly below the strictest inherited
    DENY (deny-beats-allow). Returns the verb value or ``None``. Cycle-guarded.

    ``trace`` (Issue #174, M29) is an optional caller-owned list of :class:`TraceStep`. Left at its
    ``None`` default — which is what every enforcement call site passes — this function is
    byte-for-byte the resolver it has always been: nothing is recorded, nothing is formatted and
    nothing is allocated. Supplied, it reports **every** level walked including the ones that
    matched nothing, because "no grant on ``leases``, walked to parent ``None``" is the step that
    explains most surprises. The trace is written and never read: the returned verb is identical
    either way.
    """
    order = [verb.value for verb in _VERB_ORDER]
    by_resource: dict[str, list[int | None]] = {}
    row_count = 0
    for res_key, verb_value, effect_value in grants:
        row_count += 1
        rank = order.index(verb_value)
        agg = by_resource.setdefault(res_key, [None, None])
        if effect_value == PermissionEffect.DENY.value:
            if agg[1] is None or rank < agg[1]:
                agg[1] = rank
        elif agg[0] is None or rank > agg[0]:
            agg[0] = rank
    _record(
        trace,
        TraceStage.CANDIDATE_GRANTS,
        TraceOutcome.MATCHED if by_resource else TraceOutcome.SKIPPED,
        "%d cumulative grant row(s) pooled over %d resource(s)",
        row_count,
        len(by_resource),
        resource=resource_key,
    )

    allow_rank: int | None = None
    deny_rank: int | None = None
    # Which level in the chain supplied the winning ALLOW / strictest DENY — the anchors the
    # explainer needs to name the deciding grant without re-deriving which one decided.
    allow_source: str | None = None
    deny_source: str | None = None
    current: str | None = resource_key
    seen: set[str] = set()
    while current is not None and current not in seen:
        seen.add(current)
        pooled = by_resource.get(current)
        parent = parent_by_key.get(current)
        if pooled is None:
            _record(
                trace,
                TraceStage.TREE_WALK,
                TraceOutcome.SKIPPED,
                "%s: no grant, walked to parent %s",
                current,
                parent if parent is not None else "None (catalog root)",
                resource=current,
            )
        else:
            pooled_allow, pooled_deny = pooled
            if pooled_allow is not None and (
                allow_rank is None or pooled_allow > allow_rank
            ):
                allow_rank = pooled_allow
                allow_source = current
            if pooled_deny is not None and (
                deny_rank is None or pooled_deny < deny_rank
            ):
                deny_rank = pooled_deny
                deny_source = current
            _record(
                trace,
                TraceStage.TREE_WALK,
                TraceOutcome.MATCHED,
                "%s: ALLOW %s, DENY %s",
                current,
                order[pooled_allow] if pooled_allow is not None else "-",
                order[pooled_deny] if pooled_deny is not None else "-",
                resource=current,
                value=order[pooled_allow] if pooled_allow is not None else None,
            )
        current = parent

    if allow_rank is None:
        _record(
            trace,
            TraceStage.VERB_RESOLUTION,
            TraceOutcome.DENIED,
            "no ALLOW anywhere in %s's chain: no access",
            resource_key,
            resource=resource_key,
        )
        return None
    if deny_rank is None:
        _record(
            trace,
            TraceStage.VERB_RESOLUTION,
            TraceOutcome.FINAL,
            "effective verb %s from the ALLOW on %s (no DENY in the chain)",
            order[allow_rank],
            allow_source,
            resource=allow_source,
            value=order[allow_rank],
        )
        return order[allow_rank]
    capped = min(allow_rank, deny_rank - 1)
    if capped < 0:
        _record(
            trace,
            TraceStage.VERB_RESOLUTION,
            TraceOutcome.DENIED,
            "DENY %s on %s beats every ALLOW in %s's chain: no access",
            order[deny_rank],
            deny_source,
            resource_key,
            resource=deny_source,
            value=order[deny_rank],
        )
        return None
    _record(
        trace,
        TraceStage.VERB_RESOLUTION,
        TraceOutcome.CAPPED if capped < allow_rank else TraceOutcome.FINAL,
        "effective verb %s (ALLOW %s on %s, DENY %s on %s)",
        order[capped],
        order[allow_rank],
        allow_source,
        order[deny_rank],
        deny_source,
        resource=deny_source if capped < allow_rank else allow_source,
        value=order[capped],
    )
    return order[capped]


# --------------------------------------------------------------------------------------
# Enforcement (Issue #149, M26) — the generic ``require()`` factories' backend
# --------------------------------------------------------------------------------------
#
# The only enforcement path there is (Issue #154 deleted the enum-typed twins these were introduced
# alongside). Each resolves against the live DB catalog via
# :func:`load_effective_grant_keys_for_roles` + :func:`effective_verb_over_keys`, so a
# manifest-declared resource and one an admin added at runtime enforce identically. Used
# exclusively by :func:`src.api.rbac_deps.require` / ``require_management`` / ``require_action``.


def ensure_permission_key(
    db: Session,
    current_user: dict[str, Any],
    resource_key: str,
    required_verb: str,
    *,
    required_scope: GrantScope = GrantScope.OWN,
    scope_type: str | None = None,
    scope_id: str | None = None,
) -> None:
    """Ensure the authenticated user has at least ``required_verb`` on ``resource_key``.

    Resolves the **union** of the user's active, in-scope role assignments (Issue #136) — an
    expired assignment grants nothing, and a scoped assignment only counts when the route supplies
    a matching ``scope_type``/``scope_id`` — then the resource tree with deny-beats-allow, walking
    ``resource_key`` toward its root via :func:`load_resource_parent_map`. No-ops when auth is
    disabled (development). Raises 401 if not authenticated, 403 if the effective verb is
    insufficient.

    ``required_scope`` (Issue #166, M28) is the grant's **second axis**: the
    :class:`~src.commons.enums.GrantScope` tier the caller's grant on this resource must reach,
    compared on the ladder (:meth:`~src.commons.enums.GrantScope.satisfies`). It defaults to
    :data:`~src.commons.enums.GrantScope.OWN` — the narrowest tier, which *every* tier satisfies —
    so an existing call site's behaviour is unchanged. A route that acts on the **whole platform**
    (onboarding a clinic, an aggregate report) passes ``BUSINESS``, which is what stops a narrower
    grant from authorizing it: a nurse's ``queues.call:UPDATE`` at ``own`` is exactly the grant that
    lets them call their own queue, and exactly the grant that must not let them call every
    queue.
    """
    if not get_settings().auth_enabled:
        return
    from src.core.scope import resolve_scope_tier

    user = _resolve_user_for_rbac(db, current_user)
    roles = active_roles_for_user(db, user, scope_type=scope_type, scope_id=scope_id)
    # Skipped entirely for the ``OWN`` default: every tier satisfies it, so the extra resolution
    # would be a per-request query answering a question with only one possible outcome.
    if required_scope is not GrantScope.OWN:
        tier = resolve_scope_tier(
            db, user, resource_key, scope_type=scope_type, scope_id=scope_id
        )
        if not tier.satisfies(required_scope):
            raise_forbidden(resource_key, required_verb)
    grants = load_effective_grant_keys_for_roles(db, roles)
    granted = effective_verb_over_keys(
        grants, load_resource_parent_map(db), resource_key
    )
    if granted is None or permission_verb_rank(
        PermissionVerb(granted)
    ) < permission_verb_rank(PermissionVerb(required_verb)):
        raise_forbidden(resource_key, required_verb)
    # Issue #176 (M29): the grant carried a real request, so record that it is load-bearing. On an
    # **allow** only — a denial is already visible through the 403 logging path and says nothing
    # about whether a grant is needed. Buffered in process, never written here: this must add no
    # per-request query, which ``tests/unit/security/test_permission_usage.py`` asserts by counting
    # them. ``ensure_management_permission_key`` reaches this line too (it delegates here with
    # ``required_scope=BUSINESS``), so the third chokepoint is covered without double-counting.
    record_allow(roles, resource_key, required_verb)


def ensure_roles_hold_permission(
    db: Session, roles: Iterable[str], resource_key: str, required_verb: str
) -> None:
    """Ensure a fixed set of roles holds ``required_verb`` on ``resource_key``; 403 otherwise.

    The check for a principal that is not a ``user`` row: a patient (Issue 18), whose one role is
    ``patient``. Same resolution as :func:`ensure_permission_key` (inheritance, the resource tree,
    deny-beats-allow), without the account lookup. No-ops when auth is disabled.
    """
    if not get_settings().auth_enabled:
        return
    role_list = list(roles)
    granted = effective_verb_over_keys(
        load_effective_grant_keys_for_roles(db, role_list),
        load_resource_parent_map(db),
        resource_key,
    )
    if granted is None or permission_verb_rank(
        PermissionVerb(granted)
    ) < permission_verb_rank(PermissionVerb(required_verb)):
        raise_forbidden(resource_key, required_verb)
    record_allow(role_list, resource_key, required_verb)


def ensure_management_permission_key(
    db: Session,
    current_user: dict[str, Any],
    resource_key: str,
    required_verb: str,
    *,
    scope_type: str | None = None,
    scope_id: str | None = None,
) -> None:
    """Like :func:`ensure_permission_key`, but also require a **business-scoped** grant.

    A whole-platform **list/detail** API shows every row, not the caller's slice. A role holds a
    verb on a shared resource so its *own* narrowed view works (a nurse reads the queues at their
    clinic; ``queues:read``), so the bare verb check in :func:`ensure_permission_key` would wrongly
    admit such a caller to the whole-platform API. This gate refuses them with 403 even when their
    grant holds the verb.

    **Issue #156 (M28) swapped what "such a caller" means.** Until then this asked
    ``is_management_role()`` — is the caller's role name on a fixed list — a wall no grant could
    open and no grant could narrow. It now asks
    :func:`~src.core.scope.resolve_scope_tier`: does the caller's *grant* on this resource reach the
    whole ``business``, or only a slice of it? Because Issue #156's backfill wrote exactly the
    tier that name check implied for every pre-existing grant, this was a mechanism swap with
    byte-for-byte identical outcomes — signature, exceptions and refusals unchanged — while making
    the decision configurable data from there on. Issue #172 then deleted the name check itself, so
    nothing derives breadth from a role's name anywhere. Under multi-role assignment (Issue #136)
    the widest tier any active role grants wins, the same union semantics the verb resolution
    already has.

    Narrowed reads a caller *does* use (e.g. a clinic manager's own clinic's reports, which pair
    the verb with a site narrowing) must keep using :func:`ensure_permission_key` plus
    :func:`~src.core.scope.resolve_scope`, not this — that is the pattern Issues #157–#159 migrate
    each console onto.

    **Issue #166 (M28)** collapsed this into :func:`ensure_permission_key`'s new ``required_scope``
    argument, of which this is now the ``BUSINESS`` case — one tier comparison in one place, on the
    ladder, so the ``assigned`` tier Issue #171 adds needs no second implementation. Behaviour,
    signature and exceptions are unchanged.

    No-ops when auth is disabled. Raises 401 if not authenticated, 403 if the caller's scope on the
    resource does not reach ``business`` or the effective verb is insufficient.
    """
    ensure_permission_key(
        db,
        current_user,
        resource_key,
        required_verb,
        required_scope=GrantScope.BUSINESS,
        scope_type=scope_type,
        scope_id=scope_id,
    )


def ensure_named_action_permission_key(
    db: Session,
    current_user: dict[str, Any],
    resource_key: str,
    action_key: str,
    *,
    scope_type: str | None = None,
    scope_id: str | None = None,
) -> None:
    """Ensure the user holds the named ``(resource, action)`` capability (Issue #140).

    The named-action counterpart to :func:`ensure_permission_key`: a thin wrapper over
    :func:`role_has_named_action`, which resolves the union of the user's active, in-scope roles
    with deny-beats-allow and the opt-in descendant cascade. No-ops when auth is disabled. Raises
    401 if not authenticated, 403 (``required_action``) if the action is not granted.
    """
    if not get_settings().auth_enabled:
        return
    user = _resolve_user_for_rbac(db, current_user)
    roles = active_roles_for_user(db, user, scope_type=scope_type, scope_id=scope_id)
    if not role_has_named_action(db, roles, resource_key, action_key):
        raise_forbidden_action(resource_key, action_key)
    # Issue #176: the named-action chokepoint, recorded on the same terms. ``permission_usage.verb``
    # carries a named action key here rather than a CRUD verb — both are things a role holds and
    # both are things an operator prunes, and the two catalogs are disjoint so they never collide.
    record_allow(roles, resource_key, action_key)
