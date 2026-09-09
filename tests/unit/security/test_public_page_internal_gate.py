"""The public landing page's internal sections are gated on a grant, not on being signed in.

``/`` serves two audiences from one URL. The product half — the hero, the model, the mock-ups,
the feature scope, the positioning table — is public. The other half is the *project*: the
tracked-issue count, the milestone roadmap, the six delivery lanes and the anti-blocking rule.
That half names unshipped work and internal sequencing, so it renders only for a caller holding
the operator grant :func:`~src.web.context.can_view_internals` resolves.

Three properties are worth pinning, and all three are pure functions of an already-resolved
:class:`~src.core.nav_visibility.NavVisibility` — no HTTP, no database, and no assertion about
rendered markup, which ``.cursor/rules/testing-strategy.mdc`` forbids:

1. a signed-out visitor never sees the internals;
2. being *signed in* is not enough — a caller with no grant on the gated resource is refused,
   which is the regression that would quietly turn "internal" into "any account";
3. the tier is enforced, so an ``own``-scoped grant on the same resource does not open a surface
   the registry declares at ``business``.
"""

from __future__ import annotations

from src.commons.enums import GrantScope, PermissionVerb
from src.core.nav_visibility import NavVisibility
from src.web.context import INTERNAL_NAV_KEY, can_view_internals


def _nav(verb: PermissionVerb, scope: GrantScope) -> NavVisibility:
    """A signed-in caller holding exactly ``verb`` at ``scope`` on the gated resource."""
    return NavVisibility(
        authenticated=True,
        grants={INTERNAL_NAV_KEY: verb},
        grant_scopes={INTERNAL_NAV_KEY: scope},
    )


def test_a_signed_out_visitor_is_never_shown_the_internals() -> None:
    """The front door's default. An anonymous caller holds no grants, so the gate is closed.

    This is also the database-outage path: ``nav_visibility_for_request_safe`` degrades to exactly
    this shell, so an outage serves the public page rather than failing open.
    """
    assert can_view_internals(NavVisibility(authenticated=False)) is False


def test_being_signed_in_is_not_enough() -> None:
    """A signed-in caller with no grant on the gated resource is refused.

    The property that keeps "internal" from silently meaning "anyone with an account" — the shape
    the gate would collapse into if it were ever rewritten as an ``is_authenticated`` check.
    """
    assert can_view_internals(NavVisibility(authenticated=True)) is False


def test_the_operator_grant_opens_the_internals() -> None:
    """READ at the business tier on the gated resource — the one grant that admits a caller."""
    assert can_view_internals(_nav(PermissionVerb.READ, GrantScope.BUSINESS)) is True


def test_an_ownership_scoped_grant_does_not_reach_the_internals() -> None:
    """The tier is half the gate (Issue #165): the destination is declared at ``business``.

    A portal-style caller can hold a narrow grant on the same resource without that cascading a
    whole-business surface open.
    """
    assert can_view_internals(_nav(PermissionVerb.READ, GrantScope.OWN)) is False


def test_the_development_shell_opens_the_internals_for_a_session() -> None:
    """With auth disabled every destination renders — but still only for a signed-in shell."""
    assert (
        can_view_internals(NavVisibility(authenticated=True, all_access=True)) is True
    )
    assert (
        can_view_internals(NavVisibility(authenticated=False, all_access=True)) is False
    )
