"""Data-scoping resolver: RBAC answers *what*, this answers *whose rows* (Issue #147, M26).

``docs/architecture/rbac-matrix.md``'s "Two gates" section draws the distinction. RBAC decides
whether a caller may read *leases at all*; this decides *which* leases. The two are separate on
purpose — collapsing them is how a role ends up either seeing the whole business or nothing.

**The scope tier** (Issue #156): :func:`resolve_scope` reads the ``role_permission.scope``
condition on the caller's own grants and resolves what it means for that resource's shape. It is
the replacement for an ``is_management_role()`` name check as an authorization input, so *which
rows a caller sees is a property of the grant an admin wrote, not of the name their role happens to
have*.

**Issue #171** made that a three-rung ladder:

.. code-block:: text

    own        the rows the caller is the subject of
    assigned   own, plus the instances their user_roles(scope_type=...) rows name
    business   every row the resource has (optionally narrowed per principal, below)

The tiers are compared by :meth:`~src.commons.enums.GrantScope.satisfies` — a rank comparison on
the enum's declaration order — never by naming them at a call site.

**What the kernel resolves, and what you add.** The kernel owns the *tier* half completely: every
function below that walks grants, roles and the resource tree is finished and needs no edit. It
resolves exactly one narrowing axis — :attr:`ScopeNarrowing.instance_ids`, the ids a caller's role
assignments name — because that is the only axis that exists before your domain does.

The *shape* half is yours. A resource whose rows hang off a customer, a site, a case file expresses
"the caller's own rows" through a column the kernel has never heard of. To add one:

1. add a member to :class:`~src.commons.enums.ScopeShape`;
2. declare it on the owning module's ``ResourceSpec.scope_shape``, next to the resource tree;
3. resolve it in :func:`own_instance_ids` (first-person rows) and, if it needs its own id set,
   add a field to :class:`ScopeNarrowing` and populate it in :func:`resolve_scope`.

Nothing else changes: the tier resolution, the tree walk and the router-facing helpers all work off
whatever :func:`resolve_scope` returns.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import (
    AssignmentScopeType,
    GrantScope,
    PermissionEffect,
    ScopeShape,
)
from src.core.rbac import (
    Trace,
    TraceOutcome,
    TraceStage,
    _record,
    active_role_assignments,
    active_roles_for_user,
    load_resource_parent_map,
    role_closure,
)
from src.database.models import RbacRole, RolePermission, User

#: The ``UserRoleAssignment.scope_type`` value the RBAC console writes when an admin assigns a role
#: over specific instances rather than the whole business. A project that scopes assignments over
#: more than one kind of thing adds members to :class:`~src.commons.enums.AssignmentScopeType` and
#: branches on them in :func:`assignment_instance_ids`.
ASSIGNMENT_SCOPE_TYPE = AssignmentScopeType.INSTANCE.value


def is_scope_exempt(db: Session, role: str) -> bool:
    """Return the role's ``rbac_role.is_scope_exempt`` flag (``False`` for an unknown role).

    Exported (not just this module's internal use in :func:`business_instance_ids`) so any other
    resolver that needs the same "sees everything vs. only its own" split reads it off the same
    DB-editable role flag instead of hand-rolling a role-name check of its own.
    """
    return bool(
        db.execute(
            select(RbacRole.is_scope_exempt).where(RbacRole.name == role)
        ).scalar_one_or_none()
    )


def assignment_instance_ids(db: Session, user: User) -> frozenset[str]:
    """Return the instances ``user``'s scoped role assignments name.

    The raw instance list behind :data:`~src.commons.enums.GrantScope.ASSIGNED` — the one read of
    ``user_roles`` that answers "which instances was this principal given", used by both the
    ``assigned`` tier (:func:`resolve_scope`) and the ``business`` tier's optional per-principal
    narrowing (:func:`business_instance_ids`). Expired and out-of-scope assignments are already
    excluded by :func:`~src.core.rbac.active_role_assignments`.

    Never ``None``: an empty set means "assigned to nothing", which for the ``assigned`` tier means
    *no rows*. The unrestricted sentinel belongs to :func:`business_instance_ids` alone.
    """
    return frozenset(
        scope_id
        for _role, scope_type, scope_id in active_role_assignments(db, str(user.id))
        if scope_type == ASSIGNMENT_SCOPE_TYPE and scope_id is not None
    )


def own_instance_ids(db: Session, user: User) -> frozenset[str]:
    """Return every instance ``user`` has a **first-person** relationship with.

    The ``own`` rung's id set: the rows the caller *is the subject of*, as opposed to the rows
    someone put them in charge of (that is :func:`assignment_instance_ids`, the ``assigned`` rung
    stacked on top of this one).

    **The kernel has no first-person relationships to resolve** — "the clinicq you own", "the
    cases filed against you" are domain facts — so this returns the empty set, which correctly
    means *no rows* for an ``own``-tier caller until a project defines what own-ness is. Fill it in
    with the queries your domain answers that question with; keep it a strict subset of what
    ``assigned`` reaches, because :meth:`~src.commons.enums.GrantScope.satisfies` treats the rungs
    as a ladder.
    """
    return frozenset()


def business_instance_ids(db: Session, user: User) -> set[str] | None:
    """Return the ``business`` tier's narrowing for ``user``, or ``None`` for unrestricted.

    A ``business`` grant reaches the whole business; this is the one thing that may still narrow
    it, and it only ever narrows. An exempt role (``rbac_role.is_scope_exempt``, seeded ``true``
    for ``admin``) is unrestricted regardless; a caller holding one or more scoped assignments is
    narrowed to exactly those instances; a caller holding none is unrestricted, because their
    **grant** says ``business`` — an explicit, audited choice an admin wrote, not an unconfigured
    default resolving open.

    This is deliberately *not* the ``assigned`` tier: there, "assigned to nothing" means nothing.
    The difference is which tier the grant carries, never the caller's role name.
    """
    if is_scope_exempt(db, (user.role or "").strip()):
        return None
    assigned = assignment_instance_ids(db, user)
    return set(assigned) or None


# --------------------------------------------------------------------------------------
# The scope-tier resolver (Issue #156, extended to three tiers by Issue #171)
# --------------------------------------------------------------------------------------
#
# For **this** caller on **this** resource, how wide does the grant reach — and, when it is not the
# whole business, what does the narrowing mean for a resource of that shape?
#
# Resource shapes: each module **declares its own** on its manifest's resource tree
# (``ResourceSpec.scope_shape``, inherited by descendants exactly as ``actions`` is), because the
# catalog's one source of truth is the registered manifests (Issue #154) — never a table in this
# file.

#: The shape a resource whose manifest declares none resolves to. Instance-derived is the safe
#: default: its narrowing set is empty for a caller assigned nothing, so an undeclared resource
#: fails closed, never open.
DEFAULT_SCOPE_SHAPE = ScopeShape.INSTANCE_DERIVED


def scope_shape_for(resource_key: str) -> ScopeShape:
    """Return the :class:`ScopeShape` declared for ``resource_key`` by its module's manifest.

    Manifest-derived, never a table in this file: the resource catalog's one source of truth is the
    registered module manifests (Issue #154), so a module declares its own shape next to its own
    resource tree and a resource added tomorrow narrows correctly without editing this module. A
    key no manifest declares (an admin-created runtime resource) falls back to
    :data:`DEFAULT_SCOPE_SHAPE`.
    """
    from src.core.rbac_manifest_registry import manifest_scope_shape_map

    return manifest_scope_shape_map().get(resource_key, DEFAULT_SCOPE_SHAPE)


@dataclass(frozen=True, slots=True)
class ScopeNarrowing:
    """The identity sets a sub-``business`` caller's rows may hang off (Issues #156, #171).

    Returned by :func:`resolve_scope` instead of :data:`~src.commons.enums.GrantScope.BUSINESS`
    when the caller's grant on the resource is ``own`` **or** ``assigned``. A consumer applies the
    field its resource's :class:`ScopeShape` names.

    ``tier`` says which rung produced these sets, because the two rungs narrow differently:
    ``own`` is first-person, while ``assigned`` adds the instances someone put the caller in charge
    of.

    An **empty** set means "no rows" — never "unrestricted". ``None`` (the unrestricted sentinel
    :func:`business_instance_ids` uses) is deliberately impossible here: a sub-business caller is
    by definition restricted.

    Add a field per narrowing axis your domain needs, and populate it in :func:`resolve_scope`.
    """

    #: Which rung resolved these sets — ``OWN`` or ``ASSIGNED``, never ``BUSINESS``.
    tier: GrantScope
    #: The signed-in caller (``User.id``) — the participant identity for THREAD_PARTICIPANT.
    user_id: str
    #: The resource's shape, from :func:`scope_shape_for` — which field below to apply.
    shape: ScopeShape
    #: Instances the caller is the subject of, plus — at ``assigned`` — the instances their role
    #: assignments name.
    instance_ids: frozenset[str]


def _allowed_scope_rows(db: Session, roles: Iterable[str]) -> list[tuple[str, str]]:
    """Return ``(resource, scope)`` for every ALLOW cumulative grant in ``roles``' closures.

    DENY rows are excluded on purpose: a DENY removes access outright (deny-beats-allow, resolved
    by :func:`~src.core.rbac.effective_verb_over_keys` before scope is ever consulted), so the tier
    it happens to carry says nothing about how wide the *remaining* access reaches.

    A row with no tier at all coalesces to :data:`~src.commons.enums.GrantScope.OWN`, the narrowest
    rung. Unreachable in practice — the column is ``NOT NULL`` and migration ``0069`` pinned every
    row before the default moved — but it used to coalesce to ``business``, and an unreachable
    branch that resolves "nothing was said" to "everything" is exactly the shape Issue #172 exists
    to remove.
    """
    closure: set[str] = set()
    for role in roles:
        closure |= role_closure(db, role)
    if not closure:
        return []
    rows = db.execute(
        select(RolePermission.resource, RolePermission.scope).where(
            RolePermission.role.in_(closure),
            RolePermission.action.is_(None),
            RolePermission.effect == PermissionEffect.ALLOW.value,
        )
    ).all()
    return [(resource, scope or GrantScope.OWN.value) for resource, scope in rows]


def _tier_over_tree(
    by_resource: dict[str, set[str]],
    parent_by_key: dict[str, str | None],
    resource_key: str,
    *,
    trace: Trace = None,
) -> GrantScope:
    """Walk ``resource_key`` toward the catalog root and return the tier the nearest grant carries.

    The single implementation of the resolution rule both :func:`resolve_scope_tier` (one key, one
    caller) and :func:`scope_tiers_for_roles` (every key, the nav layer) apply — see
    :func:`resolve_scope_tier`'s docstring for the rule itself. The **widest** tier at the deciding
    level wins, picked by :attr:`~src.commons.enums.GrantScope.tier_rank` rather than by naming the
    tiers, which is why Issue #171's ``assigned`` rung ordered itself here with no edit at all.

    ``trace`` (Issue #174, M29) records the same walk :func:`~src.core.rbac.effective_verb_over_keys`
    records for the verb, including the levels that carried no grant. ``None`` — what the nav layer
    and every enforcement path pass — records nothing and costs nothing.
    """
    current: str | None = resource_key
    seen: set[str] = set()
    while current is not None and current not in seen:
        seen.add(current)
        scopes = by_resource.get(current)
        if scopes:
            tier = max(
                (GrantScope(scope) for scope in scopes),
                key=lambda tier: tier.tier_rank,
            )
            _record(
                trace,
                TraceStage.SCOPE_TIER,
                TraceOutcome.FINAL,
                "%s: nearest tier-carrying grant is %s (widest of %s)",
                current,
                tier.value,
                ", ".join(sorted(scopes)),
                resource=current,
                value=tier.value,
            )
            return tier
        parent = parent_by_key.get(current)
        _record(
            trace,
            TraceStage.SCOPE_TIER,
            TraceOutcome.SKIPPED,
            "%s: no ALLOW grant carrying a tier, walked to parent %s",
            current,
            parent if parent is not None else "None (catalog root)",
            resource=current,
        )
        current = parent
    _record(
        trace,
        TraceStage.SCOPE_TIER,
        TraceOutcome.FINAL,
        "no tier anywhere in %s's chain: the closed default %s",
        resource_key,
        GrantScope.OWN.value,
        resource=resource_key,
        value=GrantScope.OWN.value,
    )
    return GrantScope.OWN


def scope_tiers_for_roles(
    db: Session,
    roles: Iterable[str],
    resource_keys: Iterable[str],
    *,
    trace: Trace = None,
) -> dict[str, GrantScope]:
    """Return ``{resource_key: effective tier}`` for ``roles`` over many keys in one pass.

    The bulk form of :func:`resolve_scope_tier`, added by Issue #165 (M28) for the nav/surface
    layer, which needs the tier for *every* catalog key on every request and cannot afford
    :func:`resolve_scope_tier`'s per-key role lookup and parent-map load. Takes already-resolved
    **role names** rather than a ``User`` deliberately: the caller
    (:func:`~src.core.nav_visibility._effective_grants`) resolves its verbs from the very same role
    list, so the tier and the verb can never be resolved from different role sets.

    ``trace`` (Issue #174, M29) is passed straight through to :func:`_tier_over_tree` for every key.
    The nav layer, which asks for the whole catalog at once, always leaves it ``None``; the
    simulator asks for one key at a time, so the steps it collects describe one walk.
    """
    by_resource: dict[str, set[str]] = {}
    for resource, scope in _allowed_scope_rows(db, roles):
        by_resource.setdefault(resource, set()).add(scope)
    parent_by_key = load_resource_parent_map(db)
    return {
        key: _tier_over_tree(by_resource, parent_by_key, key, trace=trace)
        for key in resource_keys
    }


def resolve_scope_tier(
    db: Session,
    user: User,
    resource_key: str,
    *,
    scope_type: str | None = None,
    scope_id: str | None = None,
    trace: Trace = None,
) -> GrantScope:
    """Resolve the caller's effective :class:`GrantScope` on ``resource_key`` (Issue #156, M28).

    The replacement for ``is_management_role(role)`` as an authorization input: the answer now comes
    from the caller's **grants**, not their role's name.

    Resolution order — the same union-of-active-roles, inheritance-aware, tree-walking order every
    other grant lookup uses:

    1. The union of the user's **active, in-scope** roles (Issue #136) and each one's inheritance
       closure (Issue #135) supplies the candidate ALLOW grants.
    2. Walk ``resource_key`` toward the catalog root. The **nearest** resource in that chain
       carrying any grant decides — a grant on the resource itself overrides one inherited from its
       parent, exactly as a verb does.
    3. At that level, the **widest** tier any of the caller's roles grants wins: a user holding both
       a portal role (``own``) and a management role (``business``) is management, the same union
       semantics multi-role verb resolution already has.
    4. No grant anywhere in the chain → ``OWN``, the closed default. Such a caller fails the verb
       check regardless, so this only ever decides how a *grant-less* caller is refused.

    ``trace`` (Issue #174, M29) records the resolved role set and then every level of the walk.
    ``None`` — what :func:`~src.core.rbac.ensure_permission_key` passes — records nothing.
    """
    roles = active_roles_for_user(db, user, scope_type=scope_type, scope_id=scope_id)
    _record(
        trace,
        TraceStage.ROLE_CLOSURE,
        TraceOutcome.MATCHED if roles else TraceOutcome.SKIPPED,
        "active, in-scope role(s) for the principal: %s",
        ", ".join(sorted(roles)) or "none",
        resource=resource_key,
    )
    return scope_tiers_for_roles(db, roles, [resource_key], trace=trace)[resource_key]


def resolve_scope(
    db: Session,
    user: User,
    resource_key: str,
    *,
    scope_type: str | None = None,
    scope_id: str | None = None,
    trace: Trace = None,
) -> Literal[GrantScope.BUSINESS] | ScopeNarrowing:
    """Resolve ``user``'s scope on ``resource_key`` into a *usable* narrowing (Issues #156, #171).

    Returns :data:`~src.commons.enums.GrantScope.BUSINESS` — the "apply no filter" sentinel, the
    whole-business console view — or a :class:`ScopeNarrowing` carrying the identity sets that
    resource's :class:`ScopeShape` narrows by. Callers branch once::

        scope = resolve_scope(db, user, "widgets")
        instance_ids = None if scope is GrantScope.BUSINESS else set(scope.instance_ids)

    The two sub-business rungs differ by exactly one union: ``assigned`` is ``own`` plus the
    instances :func:`assignment_instance_ids` names. Keeping ``own`` a strict subset is what makes
    :meth:`~src.commons.enums.GrantScope.satisfies` a valid ladder comparison rather than three
    unrelated modes — an ``assigned`` caller can do anything an ``own`` caller can.

    The identity sets are resolved eagerly and only below ``business`` — a business-scoped caller,
    the overwhelmingly common case, costs nothing extra. The return type names ``BUSINESS``
    specifically (never a bare ``GrantScope``): a sub-business tier always arrives as the
    :class:`ScopeNarrowing` that carries its narrowing, so a caller that has checked for the
    sentinel is holding a filter, and a type checker knows it.
    """
    tier = resolve_scope_tier(
        db, user, resource_key, scope_type=scope_type, scope_id=scope_id, trace=trace
    )
    if tier is GrantScope.BUSINESS:
        _record(
            trace,
            TraceStage.INSTANCE_NARROWING,
            TraceOutcome.SKIPPED,
            "%s tier: no narrowing, the whole business",
            GrantScope.BUSINESS.value,
            resource=resource_key,
            value=GrantScope.BUSINESS.value,
        )
        return GrantScope.BUSINESS
    instance_ids = own_instance_ids(db, user)
    if tier is GrantScope.ASSIGNED:
        instance_ids |= assignment_instance_ids(db, user)
    _record(
        trace,
        TraceStage.INSTANCE_NARROWING,
        TraceOutcome.MATCHED if instance_ids else TraceOutcome.DENIED,
        "%s tier on a %s resource: %d instance(s)",
        tier.value,
        scope_shape_for(resource_key).value,
        len(instance_ids),
        resource=resource_key,
        value=tier.value,
    )
    return ScopeNarrowing(
        tier=tier,
        user_id=str(user.id),
        shape=scope_shape_for(resource_key),
        instance_ids=instance_ids,
    )


# --------------------------------------------------------------------------------------
# Router-facing helpers — one call per list/detail endpoint
# --------------------------------------------------------------------------------------
#
# Every console migrated onto the scope tier needs the same three lines: resolve the caller, ask
# for their tier on this resource, and turn the answer into the filter value the module's own list
# service already takes. These wrappers are those three lines, so a module never writes them (or
# gets them subtly wrong) itself. Each returns ``None`` for "apply no narrowing" and a set —
# possibly empty, meaning *no rows* — otherwise.
#
# The ``*_in_scope`` functions take an already-loaded ``User``; the ``scoped_*`` ones take JWT
# claims and resolve the user first. Same resolution, two entry points.


def _user_from_claims(db: Session, current_user: dict) -> User | None:
    """Resolve the signed-in ``User`` row from JWT claims, or ``None`` when it may not act.

    :func:`src.core.security.find_active_user`: the same resolution the RBAC gate uses, so the two
    can never disagree about who the caller is.
    """
    from src.core.security import find_active_user

    return find_active_user(db, current_user)


def _closed_narrowing(resource_key: str) -> ScopeNarrowing:
    """Return the reaches-nothing narrowing an unresolvable caller gets — every axis empty."""
    return ScopeNarrowing(
        tier=GrantScope.OWN,
        user_id="",
        shape=scope_shape_for(resource_key),
        instance_ids=frozenset(),
    )


def scope_for_claims(
    db: Session, current_user: dict, resource_key: str
) -> Literal[GrantScope.BUSINESS] | ScopeNarrowing:
    """Resolve the signed-in caller from JWT claims, then :func:`resolve_scope`.

    Fails **closed** for an unresolvable caller (an empty :class:`ScopeNarrowing`, i.e. no rows)
    rather than raising: every endpoint that calls this has already passed an RBAC dependency which
    resolves and validates the same user, so narrowing a query is not the place to duplicate the
    authentication gate.
    """
    user = _user_from_claims(db, current_user)
    if user is None:
        return _closed_narrowing(resource_key)
    return resolve_scope(db, user, resource_key)


def instance_ids_in_scope(
    db: Session, user: User, resource_key: str
) -> set[str] | None:
    """Return the instance ids ``user`` may see on ``resource_key``, or ``None`` for unrestricted.

    One branch per rung:

    * **business** → :func:`business_instance_ids` — unrestricted, unless the caller holds scoped
      assignments, in which case those instances.
    * **assigned** → the caller's own instances **plus** the instances they were assigned.
      Fail-closed: assigned to nothing, owning nothing, reaches nothing.
    * **own** → the caller's own instances.
    """
    scope = resolve_scope(db, user, resource_key)
    if scope is GrantScope.BUSINESS:
        return business_instance_ids(db, user)
    return set(scope.instance_ids)


def scoped_instance_ids(
    db: Session, current_user: dict, resource_key: str
) -> set[str] | None:
    """Claims-taking form of :func:`instance_ids_in_scope`; empty (deny) for an unknown caller."""
    user = _user_from_claims(db, current_user)
    if user is None:
        return set()
    return instance_ids_in_scope(db, user, resource_key)


def ids_within_scope(ids: Iterable[str], gate: Callable[[str], object]) -> list[str]:
    """Keep the ids ``gate`` admits, dropping every id it refuses (Issue #178).

    ``gate`` is the module's own per-row scoped loader — the *same* function the single-row route
    calls — so a bulk endpoint can never drift from the narrowing its detail endpoint enforces.
    A refusal is signalled the way those loaders signal it, by raising
    :class:`~fastapi.HTTPException`; any other exception propagates untouched. De-duplicates in
    request order.
    """
    kept: list[str] = []
    for row_id in dict.fromkeys(ids):
        try:
            gate(row_id)
        except HTTPException:
            continue
        kept.append(row_id)
    return kept


def refuse_out_of_scope_ids(
    requested: Iterable[str],
    applied: Iterable[tuple[str, bool, str | None]],
    *,
    not_found_detail: str,
) -> list[tuple[str, bool, str | None]]:
    """Re-expand a scope-filtered bulk result back onto the ids the caller actually asked for.

    A bulk endpoint narrows its id list to the caller's scope before touching the database, which
    would otherwise make an out-of-scope id vanish from the response entirely — telling the caller
    nothing, or worse, reporting a smaller total than they submitted. This folds the applied rows
    back into request order and fills every id the filter dropped with the module's own *not found*
    detail, so an id outside the caller's scope is indistinguishable from one that never existed
    (Issue #178). De-duplicates in request order, exactly as the services do.
    """
    by_id = {row_id: (deleted, detail) for row_id, deleted, detail in applied}
    return [
        (row_id, *by_id.get(row_id, (False, not_found_detail)))
        for row_id in dict.fromkeys(requested)
    ]


def instance_in_scope(instance_ids: set[str] | None, instance_id: str | None) -> bool:
    """Return whether one row's instance id survives a :func:`scoped_instance_ids` filter.

    The detail-endpoint counterpart of passing the set to a list query: ``None`` (unrestricted)
    admits everything, a set admits only its members, and a row with no instance at all is admitted
    only for an unrestricted caller — fail closed.
    """
    if instance_ids is None:
        return True
    return instance_id is not None and instance_id in instance_ids
