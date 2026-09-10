"""Request/response payloads for the RBAC admin API (Issue #6).

Covers role definitions (``rbac_role``), the per-resource permission matrix
(``role_permission``), and user-role assignment. Ported and simplified from the
``maps`` project (``rbac_admin.py`` role/permission schemas) to match the Cape Tour
resource tree in ``src.commons.enums``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RbacRoleOut(BaseModel):
    """One role row for the admin list / detail panel."""

    name: str
    description: str | None = None
    is_system: bool = False
    created_at: datetime | None = None
    modified_at: datetime | None = None
    user_count: int = Field(
        default=0, description="Users currently assigned this role."
    )
    permission_count: int = Field(
        default=0, description="Resources with an explicit grant on this role."
    )


class RbacRoleListOut(BaseModel):
    """Paginated list of roles."""

    items: list[RbacRoleOut]
    total: int = Field(description="Total rows matching filters (before offset/limit).")


class RbacRoleCreateIn(BaseModel):
    """Create a new (non-system) role."""

    name: str = Field(
        min_length=1,
        max_length=50,
        description="Role name slug: lowercase letters, digits, underscore; "
        "must start with a letter.",
    )
    description: str | None = Field(default=None, max_length=500)


class RbacRolePatchIn(BaseModel):
    """Update role metadata (currently: description only)."""

    description: str | None = Field(default=None, max_length=500)


class RolePermissionRowOut(BaseModel):
    """One resource's granted verb, effect and scope tier for a role, plus its effective values."""

    resource: str = Field(description="Resource enum value.")
    max_verb: str | None = Field(
        default=None,
        description="Verb explicitly granted on this resource (null when none).",
    )
    effect: str = Field(
        default="allow",
        description="Effect of this resource's own row: 'allow' (adds access) or 'deny' "
        "(removes this verb and every higher verb). Defaults to 'allow'; only meaningful "
        "when ``max_verb`` is set.",
    )
    scope: str | None = Field(
        default=None,
        description="Scope tier stored on this resource's own row — 'own', 'assigned' or "
        "'business' (null when the role has no row here). The third field of the grant, "
        "beside ``max_verb`` and ``effect``.",
    )
    effective_max_verb: str | None = Field(
        default=None,
        description="Resolved verb after parent->child inheritance and deny-beats-allow.",
    )
    effective_scope: str = Field(
        default="own",
        description="Resolved scope tier after parent->child inheritance and role inheritance — "
        "what ``src.core.scope.resolve_scope_tier`` would answer for a holder of this role. "
        "Complements ``scope`` exactly as ``effective_max_verb`` complements ``max_verb``.",
    )
    effective_inherited: bool = Field(
        default=False,
        description="True when no row exists on this resource but access is inherited "
        "(from a resource-tree ancestor or an inherited role).",
    )
    effective_via_role: bool = Field(
        default=False,
        description="True when an inherited role (role_hierarchy closure) changes the effective "
        "verb on this resource — distinct from inheritance via the resource tree (Issue #135).",
    )
    parent_resource: str | None = Field(
        default=None,
        description="Immediate parent resource in the permission tree, if any.",
    )
    created_at: datetime | None = Field(
        default=None,
        description="When this grant was first recorded (timezone-aware).",
    )
    last_used_at: datetime | None = Field(
        default=None,
        description="When a request last exercised this grant (Issue #176) — null means no "
        "recorded use, which on a young collection window is not the same as unused. Rolled up "
        "over the resource subtree, since a parent grant is what authorizes its children.",
    )
    hit_count: int = Field(
        default=0,
        description="Roughly how many allowed requests this grant has carried since collection "
        "began. Coalesced in-process between flushes, so an order-of-magnitude signal for "
        "prioritising a pruning worklist — never a count to reconcile against anything.",
    )


class RolePermissionsGetOut(BaseModel):
    """Full permission matrix (one row per resource) for a role."""

    role: str
    permissions: list[RolePermissionRowOut]
    usage_enabled: bool = Field(
        default=False,
        description="Whether grant-usage collection is on for this deployment (Issue #176). When "
        "off, every row's usage fields are meaningless and the console says so rather than "
        "rendering 'never used' for the whole matrix.",
    )
    usage_since: datetime | None = Field(
        default=None,
        description="When usage collection began here. Shown next to the unused-grant filter, "
        "because a grant can read 'never used' simply because the window is young — and an "
        "operator has no way to tell that from a genuinely dead grant otherwise.",
    )
    usage_unused_days: int = Field(
        default=90,
        description="The 'granted, never used in N days' worklist threshold in force.",
    )


class PermissionCellIn(BaseModel):
    """A single matrix cell's grant: a verb, its ALLOW/DENY effect, and its scope tier.

    The three fields of one ``role_permission`` row — verb (Issue #6), effect (Issue #134) and,
    since Issue #173, ``scope`` (Issue #156's tier). Until then the grid carried the first two and
    silently discarded the third, so a payload asking for ``scope: own`` returned 200 and stored the
    default.
    """

    max_verb: str | None = Field(
        default=None,
        description="Verb (read/create/update/delete) or null/none to revoke the cell.",
    )
    effect: str = Field(
        default="allow",
        description="'allow' (default) adds access up to ``max_verb``; 'deny' removes that "
        "verb and every higher verb, beating any ALLOW for the same resource+verb.",
    )
    scope: str | None = Field(
        default=None,
        description="Scope tier: 'own' (the caller's own rows), 'assigned' (those plus the "
        "properties assigned to them) or 'business' (the whole business). **Omit to preserve** "
        "the tier already stored on this cell; a new cell defaults to the narrowest tier.",
    )


class RolePermissionsPutIn(BaseModel):
    """Replace permissions for a role: resource key -> cell, or null to revoke.

    Each value is either a bare verb string (``"update"``, effect defaults to ALLOW and the stored
    tier is preserved — the pre-#134 wire shape, still accepted), a ``{max_verb, effect, scope}``
    object, or ``null``/``"none"`` to revoke the cell.
    """

    permissions: dict[str, PermissionCellIn | str | None] = Field(
        default_factory=dict,
        description="Map of resource enum value to a verb string, a {max_verb, effect, scope} "
        "object, or null to revoke.",
    )


class PolicyStatement(BaseModel):
    """One decomposed policy statement — a single ``role_permission`` row, as JSON (Issue #161).

    Deliberately the same shape an IAM policy document's ``Statement`` array carries
    (``Effect``/``Resource``/``Action``/``Condition``), because the table already *is* that shape:
    one row per (resource, verb-or-action) with an effect and, since Issue #156, a ``scope``
    condition. Exactly one of ``verb`` (a cumulative CRUD grant) and ``action`` (a named-action
    grant) is set, mirroring the row's own exclusivity constraint.

    ``scope`` is omitted on export when it equals the role's seed default
    (:func:`~src.core.rbac.default_grant_scope`) and on import means "leave it at that default", so
    an ordinary document stays free of noise and only a deliberate override is spelled out.

    ``scope_instances`` is the **read-only** resolution of an ``assigned`` tier (Issue #171,
    ``docs/architecture/rbac-iam-parity.md`` §4.6). The tier itself lives in the statement, IAM-style,
    but the instance list it names lives on the principal
    (``user_roles(scope_type='property')``) — so without this field an exported document would say
    "assigned" without saying assigned to *what*, and a reviewer reading a copied policy could not
    tell how far it reaches. ``GET`` fills it in; ``PUT`` ignores it entirely, so there is still
    exactly one source of truth and a round-trip is still a no-op.
    """

    effect: str = Field(
        default="allow",
        description="'allow' (default) adds access; 'deny' removes it and beats any ALLOW.",
    )
    resource: str = Field(
        description="Resource key from the live catalog, e.g. 'leases'."
    )
    verb: str | None = Field(
        default=None,
        description="Cumulative CRUD verb (read/create/update/delete). Mutually exclusive "
        "with 'action'.",
    )
    action: str | None = Field(
        default=None,
        description="Named action key (e.g. 'sign'). Mutually exclusive with 'verb'.",
    )
    scope: str | None = Field(
        default=None,
        description="Scope tier condition, narrowest first: 'own' (the caller's own rows), "
        "'assigned' (those plus the properties assigned to them) or 'business' (the whole "
        "business). Omit to accept the role's default.",
    )
    scope_instances: list[str] | None = Field(
        default=None,
        description="Read-only: the property ids an 'assigned' tier currently resolves to for "
        "holders of this role. Rendered on export for legibility; ignored on import.",
    )
    applies_to_descendants: bool = Field(
        default=False,
        description="Named-action grants only: cascade this action to descendant resources.",
    )


class RolePolicyDocument(BaseModel):
    """A role's complete grant set as one JSON document (Issue #161, M28).

    A *projection* of the ``role_permission`` rows, never a second source of truth: ``GET`` renders
    the rows, ``PUT`` diffs a submitted document against them by natural key
    (``resource`` + ``verb``/``action``) and applies exactly the create/update/delete set needed.
    Round-tripping an unmodified export is therefore a no-op.
    """

    role: str = Field(description="The role this document describes.")
    statements: list[PolicyStatement] = Field(
        default_factory=list,
        description="One statement per grant. An omitted grant is revoked on PUT.",
    )


class RolePolicyPutResult(BaseModel):
    """What a policy ``PUT`` actually changed, alongside the resulting document."""

    created: int = Field(default=0, description="Grants added.")
    updated: int = Field(default=0, description="Grants changed in place.")
    deleted: int = Field(
        default=0, description="Grants revoked (absent from the document)."
    )
    unchanged: int = Field(
        default=0, description="Grants the document left exactly as they were."
    )
    document: RolePolicyDocument = Field(
        description="The role's policy after the change."
    )


class RoleInheritanceGetOut(BaseModel):
    """The roles a given role inherits from directly (its ``role_hierarchy`` parents)."""

    role: str
    inherits: list[str] = Field(
        default_factory=list,
        description="Role names this role inherits from directly (one hop).",
    )


class RoleInheritanceAddIn(BaseModel):
    """Add an inheritance edge: this role should inherit ``inherits_role``."""

    inherits_role: str = Field(
        min_length=1,
        max_length=50,
        description="The role whose grants this role should inherit.",
    )


class PermissionAuditRowOut(BaseModel):
    """One RBAC-admin grant/revoke audit record (Issue #138)."""

    id: str
    actor: str = Field(
        description="Who made the change (email/identifier; 'system' if not a user)."
    )
    actor_id: str | None = None
    action: str = Field(description="'grant' or 'revoke'.")
    target_type: str = Field(
        description="'role_permission', 'role_hierarchy' or 'user_role'."
    )
    target_id: str = Field(description="Identifier of the affected grant.")
    before: dict | None = Field(
        default=None, description="Grant state before the change."
    )
    after: dict | None = Field(
        default=None, description="Grant state after the change."
    )
    created_at: datetime | None = None


class PermissionAuditListOut(BaseModel):
    """Paginated permission-audit trail."""

    items: list[PermissionAuditRowOut]
    total: int = Field(description="Total rows matching filters (before offset/limit).")


class UserRolesGetOut(BaseModel):
    """The current unscoped role assigned to a user (compat single-role view)."""

    user_id: str
    role: str


class UserRolesPutIn(BaseModel):
    """Assign a defined role to a user (replaces the unscoped assignment)."""

    role: str = Field(min_length=1, max_length=50, description="Target role name.")


class UserRoleAssignmentOut(BaseModel):
    """One role assignment for a user: role, optional scope, optional expiry (Issue #136)."""

    id: str
    role: str
    scope_type: str | None = Field(
        default=None,
        description="Instance type the role is scoped to (null = unscoped).",
    )
    scope_id: str | None = Field(
        default=None, description="Instance id the role is scoped to (null = unscoped)."
    )
    granted_by: str | None = Field(
        default=None, description="User id of the actor who granted this assignment."
    )
    granted_at: datetime | None = None
    expires_at: datetime | None = Field(
        default=None, description="When the assignment expires (null = never)."
    )
    active: bool = Field(
        default=True,
        description="False when expired (past ``expires_at``); an inactive assignment grants "
        "nothing at resolution time.",
    )
    status: str = Field(
        default="active",
        description="'active', 'scoped' (active but only within its scope) or 'expired' "
        "(Issue #175). Distinct from ``active``, which cannot express the scoped case.",
    )
    status_label: str = Field(
        default="active",
        description="The same fact in operator language — 'granted, expired 3 days ago', "
        "'active only for property <id>'. Composed server-side so no page re-derives it.",
    )


class UserRoleAssignmentsOut(BaseModel):
    """All of a user's role assignments (active and expired), for the admin Users tab."""

    user_id: str
    assignments: list[UserRoleAssignmentOut] = Field(default_factory=list)


class UserRoleAssignmentAddIn(BaseModel):
    """Add a role assignment to a user, optionally scoped and/or time-boxed (Issue #136)."""

    role: str = Field(min_length=1, max_length=50, description="Target role name.")
    scope_type: str | None = Field(
        default=None,
        max_length=32,
        description="Instance type to scope the role to (e.g. 'property', 'lease'); "
        "omit for an unscoped assignment.",
    )
    scope_id: str | None = Field(
        default=None,
        max_length=64,
        description="Instance id to scope the role to; required when ``scope_type`` is set.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="When the assignment should expire (timezone-aware); omit for no expiry.",
    )


# --------------------------------------------------------------------------------------
# Dynamic catalog CRUD (Issue #141, M25) — resources / actions / permissions
# --------------------------------------------------------------------------------------

# A resource/action key: lowercase letters, digits, dot and underscore; must start with a letter.
_CATALOG_KEY_PATTERN = r"^[a-z][a-z0-9_.]*$"


class ResourceOut(BaseModel):
    """One catalog resource row for the admin catalog UI and the grid."""

    id: str
    key: str
    name: str
    description: str | None = None
    parent_id: str | None = Field(
        default=None, description="Parent resource id (null for a root)."
    )
    parent_key: str | None = Field(
        default=None, description="Parent resource key (null for a root)."
    )
    is_system: bool = Field(
        default=False,
        description="True for a seeded built-in (protected from deletion).",
    )
    created_at: datetime | None = None


class ResourceCreateIn(BaseModel):
    """Create a catalog resource, optionally under a parent."""

    key: str = Field(
        min_length=1,
        max_length=64,
        pattern=_CATALOG_KEY_PATTERN,
        description="Stable dotted key (e.g. 'reports.custom'); unique.",
    )
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    parent_key: str | None = Field(
        default=None,
        max_length=64,
        description="Key of the parent resource; omit for a root resource.",
    )


class ResourcePatchIn(BaseModel):
    """Update a resource's metadata and/or reparent it (its ``key`` is immutable)."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    parent_key: str | None = Field(
        default=None,
        max_length=64,
        description="New parent key; pass an empty string to detach to a root.",
    )


class ResourceListOut(BaseModel):
    """The full resource catalog (tree order: parents before children)."""

    items: list[ResourceOut]
    total: int


class ActionOut(BaseModel):
    """One catalog action (verb) row."""

    id: str
    key: str
    name: str
    description: str | None = None
    is_system: bool = False
    created_at: datetime | None = None


class ActionCreateIn(BaseModel):
    """Create a catalog action."""

    key: str = Field(
        min_length=1,
        max_length=32,
        pattern=_CATALOG_KEY_PATTERN,
        description="Stable action key (e.g. 'sign'); unique.",
    )
    name: str = Field(min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=500)


class ActionPatchIn(BaseModel):
    """Update an action's metadata (its ``key`` is immutable)."""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=500)


class ActionListOut(BaseModel):
    """The full action catalog."""

    items: list[ActionOut]
    total: int


class PermissionOut(BaseModel):
    """One ``(resource, action)`` permission row, with the joined keys for display."""

    id: str
    resource_id: str
    action_id: str
    resource_key: str
    action_key: str
    key: str = Field(description="Composed human key ``resource:action``.")
    description: str | None = None
    created_at: datetime | None = None


class PermissionCreateIn(BaseModel):
    """Create a ``(resource, action)`` permission by key."""

    resource_key: str = Field(min_length=1, max_length=64)
    action_key: str = Field(min_length=1, max_length=32)
    description: str | None = Field(default=None, max_length=500)


class PermissionListOut(BaseModel):
    """The full permission catalog."""

    items: list[PermissionOut]
    total: int


class CatalogOut(BaseModel):
    """The resource tree + action catalog the permissions grid renders from (Issue #141)."""

    resources: list[ResourceOut]
    actions: list[ActionOut]


class NavGateOut(BaseModel):
    """One surface's current gate: its default plus any live admin override (Issue #146).

    ``surface_key`` is a :class:`~src.core.nav_registry.NavDestination`'s own ``key`` (a rail icon
    or grouped-rail tab, e.g. ``"messages"``) or a manifest-declared resource's own ``full_key`` (a
    console sub-tab, e.g. ``"communications.messages.inbox"``) — the same string either callsite
    already uses as its stable identity, not a new key space. ``default_*`` is what renders with no
    override row at all (the Python-declared source of truth);
    ``resource_key``/``verb``/``action``/``scope`` is the row's live value when one exists, identical
    to the default until an admin re-points it.

    ``scope`` (Issue #165, M28) is the gate's second axis: the
    :class:`~src.commons.enums.GrantScope` tier the caller's grant on ``resource_key`` must reach
    for the surface to open. ``business`` marks a whole-business back-office surface, ``own`` one
    that is itself a first-person view.
    """

    surface_key: str
    label: str = Field(
        description="The destination/resource's own display name, for the UI list."
    )
    default_resource_key: str
    default_verb: str | None
    default_action: str | None
    default_scope: str
    resource_key: str
    verb: str | None
    action: str | None
    scope: str
    is_admin_override: bool
    is_overridden: bool = Field(
        description="True when the live gate differs from the Python-declared default."
    )
    updated_at: datetime | None = None


class NavGateListOut(BaseModel):
    """Every known surface's current gate — the union of every ``NavDestination`` and every
    manifest-declared, ``NavMeta``-carrying resource, overlaid with any live override."""

    items: list[NavGateOut]
    total: int


class NavGatePatchIn(BaseModel):
    """Re-point ``surface_key``'s gate to a different resource + verb, or a different named action.

    Exactly one of ``verb``/``action`` must be set (mirrors the ``nav_gate_overrides`` table's own
    CHECK constraint) — a verb re-gate for the common case, a named action for the rarer "this tab
    now needs the finer, irreversible-transition permission, not just CREATE" case (Issue #140's
    ``sign``/``approve`` shape).

    ``scope`` re-points the gate's tier (Issue #165). Omitted, the surface keeps whatever tier it
    currently carries — its live row's, or its Python-declared default when it has no row yet — so a
    console edit that only changes the resource or verb never silently widens or narrows the tier.
    """

    resource_key: str = Field(min_length=1, max_length=120)
    verb: str | None = Field(default=None, max_length=16)
    action: str | None = Field(default=None, max_length=32)
    scope: str | None = Field(default=None, max_length=16)


# --------------------------------------------------------------------------------------
# Decision simulator (Issue #174, M29)
# --------------------------------------------------------------------------------------
#
# ``docs/architecture/rbac-decision-transparency.md`` §4. The wire shape of
# ``POST /admin/rbac/simulate``: who, against what, optionally about which row and in which scope —
# and the verdict, the grant that produced it and the full decision trace.
#
# ``POST`` rather than ``GET`` is deliberate and is part of the contract: the payload is a document,
# and nothing about a simulated principal belongs in a URL, a browser history entry or an access
# log. The endpoint writes nothing.


class SimulatePrincipalIn(BaseModel):
    """Who to simulate — exactly one of ``role``, ``user_id`` or ``email``.

    A **role** answers the portable question ("what does the tenant role reach?"); a **user**
    answers the concrete one, resolving the union of their active, in-scope, unexpired assignments
    exactly as a real request does (Issue #136).
    """

    role: str | None = Field(default=None, max_length=50)
    user_id: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=320)


class SimulateTargetIn(BaseModel):
    """What to attempt — ``{resource, verb}``, or ``{surface}`` / ``{path}``.

    The two forms answer different questions and both are required for the simulator to be worth
    having: ``{resource, verb}`` is "may this role do X", ``{surface}``/``{path}`` is "can this role
    **open this page**", which resolves through the nav gate (override → declared resource/verb →
    the Issue #165 tier). The second is the question M28 #164's regression turned on, and a
    resource+verb-only simulator would not have caught it.
    """

    resource: str | None = Field(default=None, max_length=120)
    verb: str | None = Field(
        default=None,
        max_length=16,
        description="read/create/update/delete; defaults to read when a resource is given.",
    )
    surface: str | None = Field(
        default=None,
        max_length=120,
        description="A nav destination key ('leases') or a console sub-tab surface key "
        "('maintenance.requests').",
    )
    path: str | None = Field(
        default=None,
        max_length=200,
        description="A URL path ('/admin/leases'), resolved to the destination that declares it.",
    )


class SimulateInstanceIn(BaseModel):
    """One concrete row the question is about, e.g. ``{"type": "property", "id": "…"}``."""

    type: str = Field(max_length=32)
    id: str = Field(max_length=64)


class SimulateContextIn(BaseModel):
    """The scope a *scoped* role assignment needs before it counts (Issue #136).

    Mirrors :func:`~src.core.rbac.ensure_permission_key`'s own ``scope_type``/``scope_id``
    arguments, so a simulation that omits them reports exactly what a route that omits them decides.
    """

    scope_type: str | None = Field(default=None, max_length=32)
    scope_id: str | None = Field(default=None, max_length=64)


class SimulateIn(BaseModel):
    """One simulation request."""

    principal: SimulatePrincipalIn
    target: SimulateTargetIn
    instance: SimulateInstanceIn | None = None
    context: SimulateContextIn | None = None


class DecidingStatementOut(BaseModel):
    """The grant that settled the decision, as an operator would name it."""

    role: str = Field(description="The role the grant is written on.")
    resource: str = Field(description="The resource in the tree whose grant decided.")
    effect: str = Field(description="'allow' or 'deny'.")
    max_verb: str | None = None
    action: str | None = None
    scope: str | None = Field(
        default=None, description="The grant's stored scope tier."
    )
    inherited_via: str | None = Field(
        default=None,
        description="Inheritance path through role_hierarchy, e.g. 'agent→manager'; null when the "
        "principal holds the grant directly.",
    )
    cascaded_from_ancestor: bool = Field(
        default=False,
        description="True when the grant sits on an ancestor resource and cascaded down the tree, "
        "rather than on the target resource itself.",
    )


class ResolvedInstancesOut(BaseModel):
    """What a sub-``business`` tier narrows to for this principal."""

    axis: str = Field(
        description="Which identity axis the resource's shape narrows by "
        "(property_ids/tenant_ids/vendor_ids/user_id)."
    )
    count: int
    sample: list[str] = Field(
        default_factory=list,
        description="Up to five resolved ids — a sample, never the full list.",
    )


class TraceStepOut(BaseModel):
    """One step a resolver took, as ``rbac-decision-transparency.md`` §3 specifies."""

    stage_label: str = Field(
        default="",
        description="The stage in operator language ('How wide it reaches'), composed server-side "
        "so the raw trace disclosure needs no vocabulary of its own in the browser (Issue #175).",
    )
    stage: str = Field(
        description="role_closure | candidate_grants | tree_walk | verb_resolution | scope_tier "
        "| instance_narrowing | surface_gate"
    )
    detail: str = Field(description="Human-readable, e.g. 'leases: ALLOW update'.")
    outcome: str = Field(description="matched | skipped | capped | denied | final")
    resource: str | None = Field(
        default=None,
        description="The resource key this step is about, when it is about one.",
    )
    value: str | None = Field(
        default=None, description="The verb, tier or id this step settled on."
    )


class SimulationExplanationOut(BaseModel):
    """The decision in operator language, composed server-side (Issue #175, M29).

    The explainer pages render these strings verbatim. They exist so no page resolves a tier, an
    inheritance path or a cascade in JavaScript — which would be a second implementation of the
    rules one layer out from the one Issue #174 exists to prevent.
    """

    summary: str = Field(description="The one line the explainer leads with.")
    effective_tier_label: str = Field(
        description="'their own rows' / 'assigned properties' / 'whole business'."
    )
    effective_tier_meaning: str = Field(
        description="What that tier concretely reaches for this principal."
    )
    deciding_sentence: str | None = Field(
        default=None,
        description="The deciding grant said out loud; null when no grant decided.",
    )


class SimulateOut(BaseModel):
    """One simulated decision: the verdict, what produced it, and every step taken."""

    decision: str = Field(
        description="'allow' or 'deny' — what the request path would do."
    )
    target_kind: str = Field(description="'resource_verb' or 'surface'.")
    target_label: str = Field(description="The target as asked for, echoed back.")
    principal_label: str = Field(
        description="The principal as resolved ('role:agent', an email)."
    )
    principal_roles: list[str] = Field(
        default_factory=list,
        description="The active, in-scope roles the principal resolved to.",
    )
    resource: str = Field(
        description="The resource the decision resolved against — for a surface, whatever its "
        "current gate points at."
    )
    effective_verb: str | None = Field(
        default=None,
        description="The verb the principal effectively holds on that resource.",
    )
    effective_tier: str = Field(
        description="The scope tier they effectively hold there: own | assigned | business."
    )
    deciding_statement: DecidingStatementOut | None = None
    resolved_instances: ResolvedInstancesOut | None = None
    explanation: SimulationExplanationOut | None = Field(
        default=None,
        description="The same decision in operator language (Issue #175). The explainer UI renders "
        "these strings and resolves nothing itself.",
    )
    trace: list[TraceStepOut] = Field(default_factory=list)


class UserEffectiveAccessRowOut(BaseModel):
    """One resource a user reaches, and which of their active roles gets them there."""

    resource: str
    verb: str
    tier: str
    tier_label: str
    tier_meaning: str
    contributing_roles: list[str] = Field(
        default_factory=list,
        description="The active roles whose own closure resolves this same verb — the answer to "
        "'which of their roles is doing this', which the union alone cannot give.",
    )


class UserEffectiveAccessOut(BaseModel):
    """What one user actually reaches, as the union of their active assignments (Issue #175).

    A user's access is the union of their active roles' closures, filtered by scoped and time-boxed
    assignments (Issue #136). Nothing in the console rendered that union before this issue, which is
    why "why can't this person see their lease?" required a developer.
    """

    user_id: str
    email: str
    nav_role: str = Field(
        description="The single role the shell renders from (``user.role``) — which is not always "
        "the whole story, and the page says so."
    )
    active_roles: list[str] = Field(
        default_factory=list,
        description="Roles resolved from unscoped, unexpired assignments — what a request with no "
        "scope of its own resolves.",
    )
    assignments: list[UserRoleAssignmentOut] = Field(
        default_factory=list,
        description="Every assignment, active or not. An expired or scoped one is shown as "
        "inactive rather than omitted: a row that has vanished looks like one never granted.",
    )
    access: list[UserEffectiveAccessRowOut] = Field(default_factory=list)
