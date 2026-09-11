"""Operator language for RBAC decisions — one vocabulary, server-side (Issue #175, M29).

The engine speaks in enum values: ``own``, ``assigned``, ``business``, ``role_closure``,
``verb_resolution``. An operator asking "why can this role open this page?" does not, and should not
have to learn to. This module is the single translation layer between the two, and it lives on the
server for one reason that matters more than tidiness:

**No permission resolution happens in JavaScript.** The explainer pages (role detail, user detail,
the admin-only "why" on 403) render exactly what an endpoint returns. If the browser built these
sentences it would need the tier ladder, the inheritance rules and the cascade semantics in a second
place — which is the drift Issue #174's "one implementation, never two" exists to prevent, moved one
layer out. The rule is the same rule.

Everything here is a **pure function of already-decided values**. Nothing in this module decides
anything, and nothing it returns is ever fed back into a decision.
"""

from datetime import datetime

from src.commons.enums import GrantScope, PermissionEffect

#: How a scope tier reads to an operator (Issue #173, M28 — this table moved here from
#: ``src.web.routes`` so the API and the templates cannot drift into two vocabularies).
_TIER_LABELS: dict[str, str] = {
    GrantScope.OWN.value: "their own rows",
    GrantScope.ASSIGNED.value: "assigned instances",
    GrantScope.BUSINESS.value: "whole business",
}

#: How a trace stage reads to an operator. The raw stage keeps travelling on the wire — this is what
#: the "details" disclosure prints beside it for a reader who is not holding the source open.
_STAGE_LABELS: dict[str, str] = {
    "role_closure": "Roles in play",
    "candidate_grants": "Grants those roles supply",
    "tree_walk": "Walking the resource tree",
    "verb_resolution": "What they may do",
    "scope_tier": "How wide it reaches",
    "instance_narrowing": "Which rows that is",
    "surface_gate": "The page's own gate",
}


def scope_tier_label(tier: GrantScope | str) -> str:
    """Return the operator-facing name of a scope tier ("assigned instances", not ``assigned``)."""
    value = tier.value if isinstance(tier, GrantScope) else str(tier)
    return _TIER_LABELS.get(value, value)


def scope_tier_meaning(
    tier: GrantScope | str, *, assigned_instance_count: int | None = None
) -> str:
    """Return what ``tier`` concretely reaches, in operator language.

    The answer an admin actually wants: not "assigned", but *assigned to what*. An ``assigned``
    grant with nothing assigned reaches nothing, and saying so is the cheapest place to catch the
    half-configured agent role Issue #171's tier makes possible.

    ``assigned_instance_count`` is optional because the caller does not always have one — a role
    page counts its holders' assignments, a simulation counts the principal's resolved instances,
    and a bare label has neither. Omitted, the ``assigned`` rung is described without a number
    rather than with a wrong one.
    """
    value = tier.value if isinstance(tier, GrantScope) else str(tier)
    if value == GrantScope.BUSINESS.value:
        return "every row the resource has"
    if value == GrantScope.ASSIGNED.value:
        if assigned_instance_count is None:
            return "their own rows, plus the instances they are assigned"
        if not assigned_instance_count:
            return "nothing assigned yet — reaches nothing"
        noun = "instance" if assigned_instance_count == 1 else "instances"
        return f"{assigned_instance_count} {noun}, and everything under them"
    return "only rows the caller is the subject of"


def trace_stage_label(stage: str) -> str:
    """Return the operator-facing heading for a trace stage, or the raw stage when unknown."""
    return _STAGE_LABELS.get(stage, stage.replace("_", " "))


def inheritance_phrase(role: str, inherited_via: str | None) -> str:
    """Describe where a grant came from: held directly, or inherited through the role closure.

    ``inherited_via`` is the simulator's ``nurse_doctor→receptionist`` path. Rendered as "inherited
    from *receptionist* (nurse_doctor → receptionist)" rather than as ``role_closure``, which is a
    fact about the
    implementation and not about the operator's problem.
    """
    if not inherited_via:
        return f"held directly by {role}"
    spaced = inherited_via.replace("→", " → ")
    return f"inherited from {role} ({spaced})"


def deciding_sentence(
    *,
    role: str,
    resource: str,
    effect: str,
    max_verb: str | None,
    scope: str | None,
    inherited_via: str | None,
    cascaded_from_ancestor: bool,
    target_resource: str,
) -> str:
    """One sentence naming the grant that decided, the way an operator would say it out loud.

    "clinic_manager may update sites for the sites assigned to them — held directly, cascading
    down to sites.display." Everything in it is already-resolved data from the simulator's deciding
    statement; this only arranges it.
    """
    verb = (max_verb or "").upper() or "nothing"
    action = "is denied" if effect == PermissionEffect.DENY.value else "may"
    tier = f" over {scope_tier_label(scope)}" if scope else ""
    where = (
        f", cascading down to {target_resource}"
        if cascaded_from_ancestor and target_resource != resource
        else ""
    )
    return (
        f"{role} {action} {verb} on {resource}{tier} — "
        f"{inheritance_phrase(role, inherited_via)}{where}."
    )


def no_grant_sentence(target_label: str) -> str:
    """The sentence for a refusal with no grant behind it at all — the most common "why not"."""
    return (
        f"Nothing grants access to {target_label}: no role in this principal's closure holds an "
        f"ALLOW anywhere in that resource's chain."
    )


def tier_refusal_sentence(held: str, required: str, surface: str) -> str:
    """The sentence for the refusal M28 #164 shipped and #165 closed — a verb held, a tier too narrow.

    This is the one an operator most needs spelled out, because the verb *is* held: the page refuses
    a breadth, not a capability, and every instinct says "but they have read on leases".
    """
    return (
        f"{surface} is a {scope_tier_label(required)} page. This grant reaches "
        f"{scope_tier_label(held)}, so the page stays shut even though the verb is held."
    )


def assignment_status(
    *,
    active: bool,
    expires_at: datetime | None,
    scope_type: str | None,
    scope_id: str | None,
    now: datetime,
) -> tuple[str, str]:
    """Return ``(status, label)`` for one role assignment, in operator language (Issue #175).

    A support ticket in this area is usually answered by one of these three lines, which is why an
    expired or out-of-scope assignment must be *shown as inactive* rather than omitted — an
    assignment that has silently vanished from the page looks like one that was never granted:

    * ``active`` — "active" / "active within <scope>"
    * ``expired`` — "granted, expired 3 days ago"
    * ``scoped`` — never reached here; a scoped assignment is active but conditional, and says so.
    """
    if not active:
        expiry = expires_at
        if expiry is not None and expiry.tzinfo is None and now.tzinfo is not None:
            expiry = expiry.replace(tzinfo=now.tzinfo)
        if expiry is None:
            return "expired", "granted, no longer active"
        days = max((now - expiry).days, 0)
        when = (
            "today" if days == 0 else ("1 day ago" if days == 1 else f"{days} days ago")
        )
        return "expired", f"granted, expired {when}"
    if scope_type:
        target = scope_id or "an unnamed instance"
        return "scoped", f"active only for {scope_type} {target}"
    return "active", "active"
