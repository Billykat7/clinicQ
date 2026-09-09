"""Decision simulator: answer an RBAC question without making the request (Issue #174, M29).

``docs/architecture/rbac-decision-transparency.md`` §4. The engine has always been able to answer
*is this allowed* — that is what :func:`~src.core.rbac.ensure_permission_key` does on every request
— and never able to answer *why*, *for whom* or *what would happen if*. This module is the second
answer, built entirely out of the first.

**It contains no authorization logic.** Every verdict below is computed by the functions the request
path itself calls:

======================================  ======================================================
Question                                Resolver this module calls
======================================  ======================================================
which roles is this principal?          :func:`~src.core.rbac.active_roles_for_user`
which grants do those roles supply?     :func:`~src.core.rbac.load_effective_grant_keys_for_roles`
what verb does that resolve to?         :func:`~src.core.rbac.effective_verb_over_keys`
how wide does the grant reach?          :func:`~src.core.scope.scope_tiers_for_roles`
which rows does that narrow to?         :func:`~src.core.scope.resolve_scope`
can this role open this page?           :func:`~src.core.nav_visibility.NavVisibility.visible`
======================================  ======================================================

That is the point, not an implementation detail. A simulator carrying its own copy of the
resolution rules is worse than no simulator at all: it answers confidently and wrongly the moment
the two drift, and nothing would fail. The grid parity test in
``tests/integration/admin/test_rbac_simulator.py`` pins it — every seeded role by every catalog
resource by every verb, and every nav surface — against what a live request decides.

What this module *does* own is the **explanation**: turning the :class:`~src.core.rbac.TraceStep`
list those resolvers now emit into a deciding statement an operator can read. It reads the trace;
it never feeds one back into a decision.
"""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import GrantScope, PermissionEffect, PermissionVerb, ScopeShape
from src.core.nav_registry import NavDestination, destination_for_path
from src.core.nav_visibility import NavVisibility, nav_visibility_for_role
from src.core.rbac import (
    TraceOutcome,
    TraceStage,
    TraceStep,
    _record,
    active_roles_for_user,
    effective_verb_over_keys,
    load_effective_grant_keys_for_roles,
    load_resource_parent_map,
    permission_verb_rank,
    role_closure,
    role_inheritance_path,
)
from src.core.rbac_language import (
    deciding_sentence,
    no_grant_sentence,
    scope_tier_label,
    scope_tier_meaning,
    tier_refusal_sentence,
)
from src.core.scope import (
    ASSIGNMENT_SCOPE_TYPE,
    ScopeNarrowing,
    instance_in_scope,
    resolve_scope,
    scope_shape_for,
    scope_tiers_for_roles,
)
from src.database.models import RolePermission, User

#: How many resolved instance ids a narrowed result reports. A sample, never the list: the point is
#: "this tier narrows to three properties, here are three of them", and a role assigned to four
#: hundred should not return four hundred ids to a console panel.
INSTANCE_SAMPLE_SIZE = 5

#: Joins the roles on an inheritance path — ``agent→manager``, as the design doc writes it.
_INHERITANCE_ARROW = "→"


class SimulationDecision(StrEnum):
    """The simulator's verdict: what the request path would do with this principal and target."""

    ALLOW = "allow"
    DENY = "deny"


class TargetKind(StrEnum):
    """Which of the simulator's two target forms a request asked for.

    Both exist because the two questions people ask are genuinely different, and only the second
    would have caught M28 #164: a resource+verb simulator says the tenant role holds
    ``leases:READ`` (true, for their own lease) and says nothing at all about whether
    ``/admin/leases`` opens for them.
    """

    #: ``{"resource": "leases", "verb": "update"}`` — may this principal do X?
    RESOURCE_VERB = "resource_verb"
    #: ``{"surface": "leases"}`` or ``{"path": "/admin/leases"}`` — can they open this page?
    SURFACE = "surface"


class SimulationError(ValueError):
    """A target or principal the simulator cannot resolve (an unknown path, no principal at all).

    Raised rather than answered ``deny``: "this role cannot open that page" and "no destination
    declares that path" are different facts, and collapsing them would let a typo read as a
    security finding.
    """


@dataclass(frozen=True, slots=True)
class SimulationPrincipal:
    """Who the simulation is about — exactly one of the three identifying forms.

    A **role** is the portable question ("what does the tenant role reach?"), a **user** the
    concrete one ("why can't this person open their lease?"), which under Issue #136 may resolve to
    several roles at once, some scoped and some time-boxed.
    """

    role: str | None = None
    user_id: str | None = None
    email: str | None = None


@dataclass(frozen=True, slots=True)
class SimulationTarget:
    """What is being attempted — a ``(resource, verb)`` pair, a surface key, or a URL path."""

    resource: str | None = None
    verb: str | None = None
    surface: str | None = None
    path: str | None = None


@dataclass(frozen=True, slots=True)
class SimulationInstance:
    """One concrete row the decision is about (``{"type": "property", "id": …}``)."""

    type: str
    id: str


@dataclass(frozen=True, slots=True)
class SimulationContext:
    """The scope a *scoped* role assignment needs before it counts (Issue #136).

    A property-scoped assignment grants nothing until the check supplies a matching
    ``scope_type``/``scope_id`` — exactly as :func:`~src.core.rbac.ensure_permission_key`'s own
    arguments do — so a simulation that omits it correctly reports the narrower answer.
    """

    scope_type: str | None = None
    scope_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedPrincipal:
    """The roles a :class:`SimulationPrincipal` resolves to, and the user row behind them."""

    #: The union of active, in-scope roles — what the API layer resolves and enforces against.
    roles: tuple[str, ...]
    #: The single role the *shell* renders from (``User.role``), which is what the nav layer reads.
    #: Kept distinct rather than collapsed: the surface form must resolve exactly as the shell does.
    nav_role: str
    #: The resolved user, when the principal named one.
    user: User | None
    #: How the principal was named, for the response echo.
    label: str


@dataclass(frozen=True, slots=True)
class DecidingStatement:
    """The grant that settled the decision, named the way an operator would name it.

    Derived from the trace's anchors (:attr:`~src.core.rbac.TraceStep.resource` and
    :attr:`~src.core.rbac.TraceStep.value`) and the authored ``role_permission`` rows behind them —
    never by re-deriving *which* grant won, which would be the second implementation of the
    resolution rule this whole issue exists to prevent.
    """

    role: str
    resource: str
    effect: str
    max_verb: str | None = None
    action: str | None = None
    scope: str | None = None
    #: ``"agent→manager"`` when the grant reached the principal through the role closure.
    inherited_via: str | None = None
    #: True when the grant sits on an ancestor of the target resource, not the target itself.
    cascaded_from_ancestor: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedInstances:
    """What a sub-``business`` tier narrows to for this principal, when it narrows at all."""

    #: Which identity axis the resource's :class:`~src.commons.enums.ScopeShape` narrows by.
    axis: str
    count: int
    sample: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """One simulated decision, plus everything that produced it."""

    decision: SimulationDecision
    target_kind: TargetKind
    target_label: str
    principal_label: str
    principal_roles: tuple[str, ...]
    #: The resource the decision resolved against — for a surface, whatever its gate points at.
    resource: str
    effective_verb: str | None
    effective_tier: GrantScope
    deciding_statement: DecidingStatement | None
    resolved_instances: ResolvedInstances | None
    trace: tuple[TraceStep, ...]


# --------------------------------------------------------------------------------------
# Principal resolution
# --------------------------------------------------------------------------------------


def resolve_principal(
    db: Session,
    principal: SimulationPrincipal,
    context: SimulationContext | None = None,
) -> ResolvedPrincipal:
    """Resolve a principal to the role set the request path would resolve for it.

    A **user** principal goes through :func:`~src.core.rbac.active_roles_for_user` with the
    supplied scope, so an expired or out-of-scope assignment contributes nothing here for exactly
    the reason it contributes nothing to a real request. A **role** principal is taken at face
    value: the question is about the role, and a role that does not exist yet simply holds no
    grants (which is a legitimate answer, not an error).

    Raises :class:`SimulationError` when no form was supplied, or when a named user does not exist.
    """
    ctx = context or SimulationContext()
    if principal.user_id or principal.email:
        stmt = select(User).where(User.is_deleted.is_(False))
        if principal.user_id:
            stmt = stmt.where(User.id == principal.user_id)
        else:
            stmt = stmt.where(User.email == (principal.email or "").strip().lower())
        user = db.execute(stmt).scalar_one_or_none()
        if user is None:
            raise SimulationError("No such user.")
        roles = active_roles_for_user(
            db, user, scope_type=ctx.scope_type, scope_id=ctx.scope_id
        )
        return ResolvedPrincipal(
            roles=tuple(sorted(roles)),
            nav_role=(user.role or "").strip(),
            user=user,
            label=str(user.email),
        )
    role = (principal.role or "").strip()
    if not role:
        raise SimulationError("A principal must name a role, a user_id or an email.")
    return ResolvedPrincipal(
        roles=(role,), nav_role=role, user=None, label=f"role:{role}"
    )


# --------------------------------------------------------------------------------------
# Explanation — reading the trace back into a statement
# --------------------------------------------------------------------------------------


def _authored_rows(
    db: Session, roles: tuple[str, ...]
) -> list[tuple[str, str, str, str, str]]:
    """Return ``(role, resource, max_verb, effect, scope)`` for every cumulative row in the closure.

    The **authored** rows — what an admin edits in the console — rather than the flattened
    ``effective_role_permissions`` cache the resolver may read, because the cache attributes a row
    to the role that inherits it, and the question this answers is *which role was it written on*.
    Attribution only; the verdict has already been decided by the resolvers.
    """
    closure: set[str] = set()
    for role in roles:
        closure |= role_closure(db, role)
    if not closure:
        return []
    rows = db.execute(
        select(
            RolePermission.role,
            RolePermission.resource,
            RolePermission.max_verb,
            RolePermission.effect,
            RolePermission.scope,
        ).where(RolePermission.role.in_(closure), RolePermission.action.is_(None))
    ).all()
    return [
        (
            role,
            resource,
            max_verb,
            effect or PermissionEffect.ALLOW.value,
            scope or GrantScope.OWN.value,
        )
        for role, resource, max_verb, effect, scope in rows
    ]


def _inheritance_label(
    db: Session, principal_roles: tuple[str, ...], granting_role: str
) -> str | None:
    """Return ``"agent→manager"`` when the grant arrived through the closure, else ``None``.

    The shortest path across the principal's roles, so a user holding several roles is explained by
    the one that most directly accounts for the grant rather than by an arbitrary pick.
    """
    best: list[str] = []
    for role in principal_roles:
        path = role_inheritance_path(db, role, granting_role)
        if len(path) > 1 and (not best or len(path) < len(best)):
            best = path
    return _INHERITANCE_ARROW.join(best) if best else None


def _deciding_statement(
    db: Session,
    roles: tuple[str, ...],
    resource_key: str,
    trace: list[TraceStep],
) -> DecidingStatement | None:
    """Name the grant the verb resolution settled on, from the trace's own anchors.

    Reads the last ``verb_resolution`` step: its ``resource`` is the level of the resource tree
    that decided and its ``value`` the verb that came out. ``None`` there means no ALLOW existed
    anywhere in the chain — there is no statement to name, which is itself the answer to most
    "why can't they?" questions.

    Among the authored rows at that level, ties are broken toward the role the principal holds
    directly, then toward the shortest inheritance path — the explanation an operator would give.
    """
    final = next(
        (step for step in reversed(trace) if step.stage is TraceStage.VERB_RESOLUTION),
        None,
    )
    if final is None or final.resource is None or final.value is None:
        return None
    wants_deny = final.outcome in (TraceOutcome.DENIED, TraceOutcome.CAPPED)
    effect = PermissionEffect.DENY.value if wants_deny else PermissionEffect.ALLOW.value
    candidates = [
        row
        for row in _authored_rows(db, roles)
        if row[1] == final.resource and row[3] == effect and row[2] is not None
    ]
    if not candidates:
        return None

    # The **widest** ALLOW is what the resolver pooled; the **strictest** DENY is what capped it —
    # so the verb rank sorts in opposite directions for the two effects. Ties on rank always go to a
    # role the principal holds directly, which is the explanation an operator expects before one
    # reached through the closure, so that half of the key never flips.
    sign = 1 if wants_deny else -1
    candidates.sort(
        key=lambda row: (
            sign * permission_verb_rank(row[2]),
            0 if row[0] in roles else 1,
        )
    )
    granting_role, grant_resource, max_verb, grant_effect, scope = candidates[0]
    return DecidingStatement(
        role=granting_role,
        resource=grant_resource,
        effect=grant_effect,
        max_verb=max_verb,
        scope=scope,
        inherited_via=_inheritance_label(db, roles, granting_role),
        cascaded_from_ancestor=grant_resource != resource_key,
    )


# --------------------------------------------------------------------------------------
# Instance narrowing
# --------------------------------------------------------------------------------------

#: Which :class:`ScopeNarrowing` field each resource shape narrows by — the same mapping
#: ``src.core.scope``'s router helpers apply, named here only so the *report* can say which axis it
#: sampled. No decision is taken from it.
_AXIS_BY_SHAPE: dict[ScopeShape, str] = {
    ScopeShape.INSTANCE_DERIVED: "instance_ids",
    ScopeShape.THREAD_PARTICIPANT: "user_id",
}


def _narrowed_instances(
    narrowing: ScopeNarrowing, resource_key: str
) -> ResolvedInstances:
    """Summarise what a sub-``business`` narrowing resolves to, on the axis its shape names.

    A shape with no entry in :data:`_AXIS_BY_SHAPE` falls back to ``instance_ids``, the axis every
    :class:`~src.core.scope.ScopeNarrowing` carries — so a domain shape added without touching this
    report still reports something true, just coarser than it could.
    """
    shape = scope_shape_for(resource_key)
    axis = _AXIS_BY_SHAPE.get(shape, "instance_ids")
    if axis == "user_id":
        ids = [narrowing.user_id] if narrowing.user_id else []
    else:
        ids = sorted(getattr(narrowing, axis, narrowing.instance_ids))
    return ResolvedInstances(
        axis=axis, count=len(ids), sample=tuple(ids[:INSTANCE_SAMPLE_SIZE])
    )


# --------------------------------------------------------------------------------------
# The two target forms
# --------------------------------------------------------------------------------------


def _simulate_resource_verb(
    db: Session,
    principal: ResolvedPrincipal,
    resource_key: str,
    required_verb: str,
    instance: SimulationInstance | None,
    context: SimulationContext,
    trace: list[TraceStep],
) -> SimulationResult:
    """Answer "may this principal do ``verb`` on ``resource``?" through the enforcement resolvers.

    Mirrors :func:`~src.core.rbac.ensure_permission_key` with its default ``required_scope`` (the
    narrowest tier, which every tier satisfies): the verdict is the verb comparison, and the tier is
    *reported* beside it rather than folded into it, because a caller wants to know that a grant is
    held at ``own`` even when the route they asked about does not require more.
    """
    grants = load_effective_grant_keys_for_roles(db, principal.roles)
    parent_map = load_resource_parent_map(db)
    verb = effective_verb_over_keys(grants, parent_map, resource_key, trace=trace)
    tier = scope_tiers_for_roles(db, principal.roles, [resource_key], trace=trace)[
        resource_key
    ]
    allowed = verb is not None and permission_verb_rank(verb) >= permission_verb_rank(
        required_verb
    )
    instances: ResolvedInstances | None = None
    if principal.user is not None:
        scope = resolve_scope(
            db,
            principal.user,
            resource_key,
            scope_type=context.scope_type,
            scope_id=context.scope_id,
        )
        if isinstance(scope, ScopeNarrowing):
            instances = _narrowed_instances(scope, resource_key)
            if allowed and instance is not None:
                allowed = _instance_admitted(scope, instance, resource_key, trace)
    # Read the explanation off the resolver's own steps **before** the verdict summary is appended:
    # that summary anchors to the resource that was *asked about*, while the deciding grant sits at
    # whichever level of the tree actually carried it.
    statement = _deciding_statement(db, principal.roles, resource_key, trace)
    _record(
        trace,
        TraceStage.VERB_RESOLUTION,
        TraceOutcome.FINAL if allowed else TraceOutcome.DENIED,
        "verdict: %s %s on %s (holds %s at tier %s)",
        "may" if allowed else "may not",
        required_verb,
        resource_key,
        verb or "no verb",
        tier.value,
        resource=resource_key,
        value=verb,
    )
    return SimulationResult(
        decision=SimulationDecision.ALLOW if allowed else SimulationDecision.DENY,
        target_kind=TargetKind.RESOURCE_VERB,
        target_label=f"{resource_key}:{required_verb}",
        principal_label=principal.label,
        principal_roles=principal.roles,
        resource=resource_key,
        effective_verb=verb,
        effective_tier=tier,
        deciding_statement=statement,
        resolved_instances=instances,
        trace=tuple(trace),
    )


def _instance_admitted(
    scope: ScopeNarrowing,
    instance: SimulationInstance,
    resource_key: str,
    trace: list[TraceStep],
) -> bool:
    """Return whether one named row survives ``scope``'s narrowing, via the shared helper.

    Only :data:`~src.core.scope.ASSIGNMENT_SCOPE_TYPE` is resolvable — it is the type
    :class:`~src.database.models.UserRoleAssignment` scopes by and the one every
    ``INSTANCE_DERIVED`` resource filters on. An instance of any other type is reported as
    unresolved and left out of the verdict rather than silently admitted or silently refused; a
    project that adds a second scope type resolves it here.
    """
    if instance.type != ASSIGNMENT_SCOPE_TYPE:
        _record(
            trace,
            TraceStage.INSTANCE_NARROWING,
            TraceOutcome.SKIPPED,
            "instance type %s is not narrowed by this resolver; verdict unchanged",
            instance.type,
            resource=resource_key,
        )
        return True
    admitted = instance_in_scope(set(scope.instance_ids), instance.id)
    _record(
        trace,
        TraceStage.INSTANCE_NARROWING,
        TraceOutcome.MATCHED if admitted else TraceOutcome.DENIED,
        "instance %s is %sin the %s tier's %d resolved instance(s)",
        instance.id,
        "" if admitted else "not ",
        scope.tier.value,
        len(scope.instance_ids),
        resource=resource_key,
        value=instance.id,
    )
    return admitted


def _gate_resource(nav: NavVisibility, surface_key: str, default_resource: str) -> str:
    """Return the resource a surface's *current* gate points at (override row, else the default)."""
    override = nav.gate_overrides.get(surface_key)
    return default_resource if override is None else override.resource


def _simulate_surface(
    db: Session,
    principal: ResolvedPrincipal,
    surface_key: str,
    dest: NavDestination | None,
    label: str,
    trace: list[TraceStep],
) -> SimulationResult:
    """Answer "can this principal open this page?" through :class:`NavVisibility` itself.

    This is the M28 #164 question, and it is answered by building the very object the Jinja shell
    renders from and asking it — :meth:`~src.core.nav_visibility.NavVisibility.visible` for a
    registry destination, :meth:`~src.core.nav_visibility.NavVisibility.can_surface` for a console
    sub-tab that has no destination of its own. Resolving it any other way would reintroduce the
    divergence that let seven consoles open for the tenant role while every test stayed green.
    """
    nav = nav_visibility_for_role(db, principal.nav_role)
    _record(
        trace,
        TraceStage.ROLE_CLOSURE,
        TraceOutcome.MATCHED,
        "shell renders from role %s (nav resolves one role, not the assignment union)",
        principal.nav_role or "none",
        resource=surface_key,
    )
    if dest is not None:
        allowed = nav.visible(dest.key, trace=trace)
        resource = _gate_resource(nav, dest.key, dest.resource)
    else:
        allowed = nav.can_surface(surface_key, trace=trace)
        resource = _gate_resource(nav, surface_key, surface_key)
    # The verb/tier the shell itself resolved for the gate's resource — read off the same matrix
    # ``visible()`` just consulted, not recomputed.
    held_verb = nav.grants.get(resource)
    tier = nav.scope_for(resource)
    # The deciding statement is an *explanation* of the gate's resource, resolved through the same
    # verb resolver on a trace of its own so the surface trace above stays a readable narrative.
    statement_trace: list[TraceStep] = []
    effective_verb_over_keys(
        load_effective_grant_keys_for_roles(db, [principal.nav_role]),
        load_resource_parent_map(db),
        resource,
        trace=statement_trace,
    )
    return SimulationResult(
        decision=SimulationDecision.ALLOW if allowed else SimulationDecision.DENY,
        target_kind=TargetKind.SURFACE,
        target_label=label,
        principal_label=principal.label,
        principal_roles=principal.roles,
        resource=resource,
        effective_verb=held_verb.value if held_verb is not None else None,
        effective_tier=tier,
        deciding_statement=_deciding_statement(
            db, (principal.nav_role,), resource, statement_trace
        ),
        resolved_instances=None,
        trace=tuple(trace),
    )


def simulate(
    db: Session,
    principal: SimulationPrincipal,
    target: SimulationTarget,
    *,
    instance: SimulationInstance | None = None,
    context: SimulationContext | None = None,
) -> SimulationResult:
    """Simulate one decision end to end and return the verdict, the reason and the full trace.

    Dispatches on which target form was supplied — ``{resource, verb}`` or ``{surface}``/``{path}``
    — and delegates to the resolvers listed in this module's docstring. Writes nothing: a
    simulation is a read, which is why it is not recorded in the permission audit log (that log is
    for *changes*).

    Raises :class:`SimulationError` for a principal or target that cannot be resolved at all.
    """
    ctx = context or SimulationContext()
    resolved = resolve_principal(db, principal, ctx)
    trace: list[TraceStep] = []
    _record(
        trace,
        TraceStage.ROLE_CLOSURE,
        TraceOutcome.MATCHED if resolved.roles else TraceOutcome.SKIPPED,
        "principal %s resolves to role(s) %s; closure %s",
        resolved.label,
        ", ".join(resolved.roles) or "none",
        ", ".join(sorted(set().union(*(role_closure(db, r) for r in resolved.roles))))
        if resolved.roles
        else "none",
    )
    if target.resource:
        verb = (target.verb or PermissionVerb.READ.value).strip().lower()
        if verb not in {member.value for member in PermissionVerb}:
            raise SimulationError(f"Unknown verb: {verb!r}.")
        return _simulate_resource_verb(
            db, resolved, target.resource, verb, instance, ctx, trace
        )
    if target.path:
        dest = destination_for_path(target.path)
        if dest is None:
            raise SimulationError(
                f"No navigation destination declares the path {target.path!r}."
            )
        return _simulate_surface(db, resolved, dest.key, dest, target.path, trace)
    if target.surface:
        return _simulate_surface(
            db,
            resolved,
            target.surface,
            _destination_or_none(target.surface),
            target.surface,
            trace,
        )
    raise SimulationError("A target must name a resource, a surface or a path.")


def _destination_or_none(key: str) -> NavDestination | None:
    """Return the registry destination named ``key``, or ``None`` when it is a bare surface key.

    A surface key is either a nav destination (``leases``, ``rbac``) or a console sub-tab that has
    none of its own (``maintenance.requests``, ``communications.announcements.inbox``). The two are
    gated by different methods, so which one it is has to be decided before the gate is asked.
    """
    from src.core.nav_registry import destination

    try:
        return destination(key)
    except KeyError:
        return None


# --------------------------------------------------------------------------------------
# Explanation in operator language (Issue #175, M29)
# --------------------------------------------------------------------------------------
#
# The explainer UI renders what the endpoint returns and resolves nothing itself, so the sentences
# an operator reads are composed here, from a decision that has already been made. Nothing below
# touches a verdict; it arranges values the resolvers produced.


@dataclass(frozen=True, slots=True)
class SimulationExplanation:
    """A simulated decision, said out loud (Issue #175).

    ``summary`` is the one line the explainer leads with; the rest is what the row expands to. The
    raw :attr:`SimulationResult.trace` stays available behind a "details" disclosure for the reader
    who wants it — this is for the reader who does not.
    """

    summary: str
    effective_tier_label: str
    effective_tier_meaning: str
    deciding_sentence: str | None


def _required_tier(result: SimulationResult) -> str | None:
    """Return the tier a surface gate demanded, from the trace's own surface-gate anchor."""
    for step in reversed(result.trace):
        if step.stage is TraceStage.SURFACE_GATE and step.value:
            return step.value
    return None


def _tier_capped(result: SimulationResult) -> bool:
    """Return whether a tier comparison — not the verb — is what refused this decision."""
    return any(
        step.stage is TraceStage.SCOPE_TIER and step.outcome is TraceOutcome.CAPPED
        for step in result.trace
    )


def explain(result: SimulationResult) -> SimulationExplanation:
    """Render ``result`` into operator language (Issue #175, M29).

    Three refusals read very differently and must not be collapsed into "denied":

    * **a tier too narrow** — the verb *is* held and the page still refuses, which is M28 #164's
      whole shape and the sentence an operator most needs spelled out;
    * **no grant at all** — the answer to most "why can't they?" tickets;
    * **a grant that decided against them** — a DENY row, named with the role it sits on.
    """
    instances = result.resolved_instances
    assigned_count = (
        instances.count
        if instances is not None and instances.axis == "instance_ids"
        else None
    )
    tier_label = scope_tier_label(result.effective_tier)
    tier_meaning = scope_tier_meaning(
        result.effective_tier, assigned_instance_count=assigned_count
    )
    statement = result.deciding_statement
    sentence = (
        None
        if statement is None
        else deciding_sentence(
            role=statement.role,
            resource=statement.resource,
            effect=statement.effect,
            max_verb=statement.max_verb,
            scope=statement.scope,
            inherited_via=statement.inherited_via,
            cascaded_from_ancestor=statement.cascaded_from_ancestor,
            target_resource=result.resource,
        )
    )
    if result.decision is SimulationDecision.DENY:
        required = _required_tier(result)
        if _tier_capped(result) and required is not None:
            summary = tier_refusal_sentence(
                held=result.effective_tier.value,
                required=required,
                surface=result.target_label,
            )
        elif statement is None:
            summary = no_grant_sentence(result.target_label)
        else:
            summary = sentence or no_grant_sentence(result.target_label)
    else:
        summary = sentence or (
            f"{result.principal_label} may reach {result.target_label} "
            f"({tier_label}: {tier_meaning})."
        )
    return SimulationExplanation(
        summary=summary,
        effective_tier_label=tier_label,
        effective_tier_meaning=tier_meaning,
        deciding_sentence=sentence,
    )
