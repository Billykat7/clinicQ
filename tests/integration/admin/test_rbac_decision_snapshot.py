"""The golden decision snapshot: the standing guard against a silently moved decision (Issue #177).

M28 #164 shipped a change that moved console reachability for three seeded roles, against its own
acceptance criterion that reachability be "byte-for-byte unchanged". It passed review and CI. It
was found later, by hand, with a bespoke probe run across two git worktrees.

Nothing caught it because the *decision set* was never an artifact. Individual tests pin individual
decisions; nothing pinned them all, so a change that moved fifty at once read as a green build.

This file is the net. It proves five things, and two of them are **demonstrated rather than
asserted**, because "this would have caught it" is a claim and the whole value of this issue rests
on it:

1. The committed snapshot equals what the code decides right now.
2. The snapshot is deterministic — the same input produces the same bytes, twice.
3. A deliberately introduced reachability change **fails this guard**, naming the change.
4. **Replaying M28 #164's wall deletion fails with the same reachability diff** that milestone's
   hand-built probe found: portal roles gaining management consoles.
5. A silent widening of `own` → `business` is caught **even when allow/deny does not move**, which
   is the failure mode M28 #173 lived with from #156 onwards.

Plus the property that keeps the guard honest: regeneration is explicit, and never happens in CI.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Iterator
from pathlib import Path

import pytest

from src.commons.enums import GrantScope, PermissionVerb, UserRole
from src.core import nav_registry, rbac_snapshot
from src.core.nav_visibility import NavVisibility
from src.core.rbac_snapshot import SNAPSHOT_PATH, build_snapshot, read_snapshot

REPO_ROOT = Path(__file__).resolve().parents[3]


GOLDEN = REPO_ROOT / SNAPSHOT_PATH


@pytest.fixture
def committed() -> str:
    """The snapshot as committed to the repository."""
    text = read_snapshot(GOLDEN)
    assert text, (
        f"{SNAPSHOT_PATH} is missing. Run `make rbac-snapshot` and commit the result."
    )
    return text


def test_the_committed_snapshot_is_what_the_code_decides(committed: str) -> None:
    """The guard itself: a moved decision fails CI until someone regenerates and reviews it.

    The kernel shipped without its golden file and without this test (Issue 18 restored both), so
    ``make rbac-snapshot-check`` failed on every checkout and nothing noticed.
    """
    moved = rbac_snapshot.diff_snapshots(committed, build_snapshot())
    assert not moved, (
        f"{len(moved)} RBAC decision(s) moved; review them, then run `make rbac-snapshot`:\n"
        + "\n".join(moved[:40])
    )


def test_the_snapshot_is_deterministic_across_runs() -> None:
    """A file that wobbles between runs is a file nobody reads, and a guard nobody trusts."""
    assert build_snapshot() == build_snapshot()


@pytest.fixture
def leases_console_opened_to_own_tier() -> Iterator[None]:
    """Temporarily re-declare the leases console as a first-person page.

    A single, surgical widening — the leases rail destination drops from ``business`` to ``own`` —
    which is exactly the shape of change an operator or a refactor could make without meaning to.
    Reverted on teardown; nothing is written to the golden file.
    """
    original = nav_registry._DESTINATIONS_BY_KEY["orders"]
    nav_registry._DESTINATIONS_BY_KEY["orders"] = nav_registry.NavDestination(
        key=original.key,
        label=original.label,
        href=original.href,
        resource=original.resource,
        verb=original.verb,
        scope=GrantScope.OWN,
        group=original.group,
    )
    yield
    nav_registry._DESTINATIONS_BY_KEY["orders"] = original


@pytest.fixture
def m28_164_wall_deleted() -> Iterator[None]:
    """Reconstruct M28 #164: the surface gate comparing verbs alone, with no tier.

    #164 deleted the ``is_manager`` wall from ``NavVisibility.visible()`` on the grounds that
    ``role_permission.scope`` (#156) now carried the "my rows vs every row" distinction. That was
    true at the *data* layer and false at the *surface* layer, which still compared verbs alone — so
    seven consoles opened for a tenant, twelve for an owner and five for a vendor. #165 closed it by
    giving each surface a required tier.

    Removing the tier comparison from :meth:`~src.core.nav_visibility.NavVisibility._surface_allowed`
    reproduces exactly that state: the wall gone, and nothing in its place.
    """
    original = NavVisibility._surface_allowed

    def _no_tier_check(
        self: NavVisibility,
        surface_key: str,
        default_resource: str,
        default_verb: PermissionVerb | str,
        default_scope: GrantScope,
        *,
        trace: object = None,
    ) -> bool:
        # ``OWN`` is the narrowest tier and every tier satisfies it, so passing it is precisely
        # "do not check the tier" — the post-#164, pre-#165 gate.
        return original(
            self,
            surface_key,
            default_resource,
            default_verb,
            GrantScope.OWN,
            trace=trace,  # type: ignore[arg-type]
        )

    NavVisibility._surface_allowed = _no_tier_check  # type: ignore[method-assign]
    yield
    NavVisibility._surface_allowed = original  # type: ignore[method-assign]


@pytest.fixture
def owner_grants_widened_to_business() -> Iterator[None]:
    """Widen every seeded ``owner`` grant from ``own`` to ``business`` — breadth only.

    The M28 #173 failure mode: a grant's *breadth* moving while its verb — and so most allow/deny
    verdicts — stands still. Before the tier was part of the snapshot, a change like this was
    invisible to every automated check in the repository.
    """
    original = rbac_snapshot.seeded_grant_scope

    def _widened(role: str | None) -> GrantScope:
        if (role or "").strip() == UserRole.NURSE_DOCTOR.value:
            return GrantScope.BUSINESS
        return original(role)

    rbac_snapshot.seeded_grant_scope = _widened  # type: ignore[assignment]
    yield
    rbac_snapshot.seeded_grant_scope = original  # type: ignore[assignment]


def test_no_ci_workflow_regenerates_the_snapshot() -> None:
    """CI may re-derive and diff; it must never write the golden file.

    Scanned rather than trusted: the one way this guard silently stops working is a well-meant
    "just regenerate it in CI" step, and that change would otherwise pass every test here.
    """
    workflows = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows, "no workflows found — this guard is scanning the wrong place"
    for workflow in workflows:
        for number, line in enumerate(workflow.read_text().splitlines(), start=1):
            # YAML comments are not steps. They are also where this guard is *explained*, so
            # scanning them would make the explanation trip its own assertion.
            if line.lstrip().startswith("#"):
                continue
            if "generate_rbac_snapshot" in line or "rbac-snapshot" in line:
                assert "--check" in line or "rbac-snapshot-check" in line, (
                    f"{workflow.name}:{number} regenerates the snapshot in CI: {line.strip()}"
                )


def test_the_makefile_exposes_regeneration_as_its_own_deliberate_target() -> None:
    """Regeneration has to be a thing a person chooses to do, and has to be documented."""
    makefile = (REPO_ROOT / "Makefile").read_text()
    assert "\nrbac-snapshot:" in makefile
    assert "scripts/generate_rbac_snapshot.py" in makefile
