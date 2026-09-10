"""Operator language and the 403 "why" gate (Issue #175, M29).

Two properties, both of which the explainer's whole value rests on and neither of which can be
asserted through rendered HTML — which ``.cursor/rules/testing-strategy.mdc`` forbids testing
anyway:

1. **The 403 affordance is gated on the ``rbac:READ`` grant and on nothing else.** A caller without
   it must see today's page: telling an unauthorized caller precisely which grant they lack is a
   disclosure, not a courtesy. :func:`~src.web.context.can_explain_denial` is a pure function of an
   already-resolved :class:`~src.core.nav_visibility.NavVisibility` for exactly this reason — the
   property is asserted directly rather than through markup.
2. **The vocabulary is operator language, composed server-side.** ``own``/``assigned``/``business``
   and ``role_closure`` never reach a page; "their own rows", "assigned properties", "whole
   business" and "inherited from *manager*" do. Composed here so no page re-derives it — a second
   implementation of the rules in JavaScript is the thing Issue #174 exists to prevent, one layer
   out.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from datetime import datetime, timedelta

import pytest

from src.commons.enums import GrantScope, PermissionVerb
from src.core.nav_visibility import NavVisibility
from src.core.rbac_language import (
    assignment_status,
    inheritance_phrase,
    trace_stage_label,
)
from src.core.s3_logging import APP_TIMEZONE
from src.web.context import RBAC_RESOURCE, can_explain_denial


def _nav(**grants: object) -> NavVisibility:
    """Build a :class:`NavVisibility` holding exactly the grants named, at the tiers named."""
    verbs = {res: PermissionVerb(v) for res, (v, _t) in grants.items()}  # type: ignore[misc]
    tiers = {res: GrantScope(t) for res, (_v, t) in grants.items()}  # type: ignore[misc]
    return NavVisibility(authenticated=True, grants=verbs, grant_scopes=tiers)


def test_a_caller_holding_business_tier_rbac_read_may_be_shown_why() -> None:
    """The one audience the affordance exists for."""
    assert can_explain_denial(
        _nav(**{RBAC_RESOURCE: (PermissionVerb.READ.value, GrantScope.BUSINESS.value)})
    )


def test_a_caller_with_no_rbac_grant_at_all_may_not() -> None:
    """The default. Their 403 is the page that shipped before this issue."""
    assert not can_explain_denial(
        _nav(leases=(PermissionVerb.DELETE.value, GrantScope.BUSINESS.value))
    )


def test_an_own_tier_rbac_grant_is_not_enough() -> None:
    """Gated on the tier the endpoint behind the link enforces, so the link never 403s when followed.

    A narrower gate here would render an affordance that refuses on click — the click-then-403
    Issue #168 exists to remove — and would also imply an ``own``-tier read of the permission model
    is a thing, which it is not.
    """
    assert not can_explain_denial(
        _nav(**{RBAC_RESOURCE: (PermissionVerb.READ.value, GrantScope.OWN.value)})
    )


def test_a_signed_out_shell_may_not() -> None:
    """No session, no affordance — and a signed-out visitor is redirected before reaching a 403."""
    assert not can_explain_denial(NavVisibility(authenticated=False))


def test_the_gate_reads_a_grant_and_never_a_role_name() -> None:
    """The property Issues #164/#172 spent a milestone establishing, restated for this affordance.

    Two callers, identical grants, and this object carries no role name at all — so there is
    nothing for a name check to read even if someone tried to write one.
    """
    nav = _nav(
        **{RBAC_RESOURCE: (PermissionVerb.READ.value, GrantScope.BUSINESS.value)}
    )
    assert can_explain_denial(nav)
    assert not hasattr(nav, "role")


def test_inheritance_is_named_in_words_not_in_the_stage_key() -> None:
    """``role_closure`` is a fact about the implementation, not about the operator's problem."""
    assert inheritance_phrase("manager", None) == "held directly by manager"
    assert inheritance_phrase("manager", "agent→manager") == (
        "inherited from manager (agent → manager)"
    )


@pytest.mark.parametrize(
    "stage",
    (
        "role_closure",
        "candidate_grants",
        "tree_walk",
        "verb_resolution",
        "scope_tier",
        "instance_narrowing",
        "surface_gate",
    ),
)
def test_every_trace_stage_has_a_heading(stage: str) -> None:
    """The raw-trace disclosure is for a person, so every stage in it reads as one."""
    label = trace_stage_label(stage)
    assert label
    assert label != stage
    assert "_" not in label


def test_an_expired_assignment_says_how_long_ago() -> None:
    """ "granted, expired 3 days ago" answers the ticket; "inactive" restates the question."""
    now = datetime.now(APP_TIMEZONE)
    status, label = assignment_status(
        active=False,
        expires_at=now - timedelta(days=3),
        scope_type=None,
        scope_id=None,
        now=now,
    )
    assert status == "expired"
    assert label == "granted, expired 3 days ago"


def test_a_scoped_assignment_is_active_but_conditional_and_says_which() -> None:
    """Active and conditional is a third state ``active: bool`` cannot express."""
    now = datetime.now(APP_TIMEZONE)
    status, label = assignment_status(
        active=True,
        expires_at=None,
        scope_type="property",
        scope_id="prop-1",
        now=now,
    )
    assert status == "scoped"
    assert label == "active only for property prop-1"


def test_a_plain_assignment_is_simply_active() -> None:
    """No decoration on the ordinary case."""
    now = datetime.now(APP_TIMEZONE)
    assert assignment_status(
        active=True, expires_at=None, scope_type=None, scope_id=None, now=now
    ) == ("active", "active")


def test_a_naive_expiry_does_not_raise_against_an_aware_now() -> None:
    """SQLite hands back naive datetimes; the label must survive that, not crash the page."""
    now = datetime.now(APP_TIMEZONE)
    status, label = assignment_status(
        active=False,
        expires_at=(now - timedelta(days=1)).replace(tzinfo=None),
        scope_type=None,
        scope_id=None,
        now=now,
    )
    assert status == "expired"
    assert label == "granted, expired 1 day ago"
