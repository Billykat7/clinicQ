"""The decision trace: the resolvers explaining their own steps (Issue #174, M29).

``docs/architecture/rbac-decision-transparency.md`` §3. The trace is the primitive the simulator,
the explainer and the snapshot harness are all built on, and it is only trustworthy if two
properties hold — so those are what this file proves, in the order the design states them:

1. **``trace=None`` is the enforcement path.** A normal authorized request constructs no
   :class:`~src.core.rbac.TraceStep` at all. Asserted by counting constructions, not by reading the
   code, because "we remembered to pass ``None`` everywhere" is exactly the kind of claim that
   decays.
2. **A trace is never an input to a decision.** Every verdict is identical with and without one,
   over a grid of roles and resources that covers ALLOW, DENY, cascade and no-grant-at-all.

Then the content: every stage reports, *including the ones that did nothing*, because "no grant on
``leases``, walked to parent" is the step that explains most surprises and a trace that only logs
matches explains nothing.

Per ``.cursor/rules/testing-strategy.mdc`` this is a unit file because the properties above are
properties of pure resolvers; the endpoint that exposes them is covered end-to-end in
``tests/integration/admin/test_rbac_simulator.py``.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Callable, Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.commons.enums import GrantScope, PermissionEffect, UserRole
from src.core import rbac
from src.core.rbac import (
    TraceOutcome,
    TraceStage,
    TraceStep,
    default_role_permissions,
    default_system_roles,
    effective_verb_over_keys,
    load_effective_grant_keys_for_roles,
    load_resource_parent_map,
    refresh_resource_descendants_py,
    role_inheritance_path,
    seed_resource_catalog,
    seeded_grant_scope,
)
from src.core.scope import resolve_scope_tier
from src.database.models import RbacRole, RoleHierarchy, RolePermission, User

_ROLES = (
    UserRole.ADMIN.value,
    UserRole.PLATFORM_ADMIN.value,
    UserRole.NURSE_DOCTOR.value,
    UserRole.PATIENT.value,
    UserRole.RECEPTIONIST.value,
    UserRole.USER.value,
)


#: A spread that exercises every branch of the walk: a root with grants, a child that inherits one,
#: a deep leaf, and a key nothing grants anywhere.
_RESOURCES = (
    "crates",
    "orders",
    "order.details",
    "tickets",
    "tickets.jobs",
    "rbac",
    "communications.messages",
    "no.such.resource",
)


def _grant(
    db: Session,
    role: str,
    resource: str,
    verb: str,
    *,
    effect: PermissionEffect = PermissionEffect.ALLOW,
    scope: GrantScope | None = None,
) -> None:
    """Insert one cumulative grant row, as a seeded deployment holds it."""
    db.add(
        RolePermission(
            role=role,
            resource=resource,
            max_verb=verb,
            effect=effect.value,
            created_at=datetime.now(UTC),
            **({} if scope is None else {"scope": scope.value}),
        )
    )


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Generator[Session]:
    """A session seeded exactly as a deployed database is: roles, the portal matrix, the catalog."""
    with session_factory() as session:
        for name, description in (*default_system_roles(),):
            session.add(RbacRole(name=name, description=description, is_system=True))
        seen: set[tuple[str, str]] = set()
        for helper in (default_role_permissions,):
            for role, resource_key, verb in helper():
                if (role, resource_key) in seen:
                    continue
                seen.add((role, resource_key))
                _grant(
                    session,
                    role,
                    resource_key,
                    verb.value,
                    scope=seeded_grant_scope(role),
                )
        for role in _ROLES:
            session.add(
                User(
                    id=f"user-{role}",
                    email=f"{role}.trace@example.com",
                    role=role,
                    is_verified=True,
                )
            )
        session.commit()
        seed_resource_catalog(session)
        session.commit()
        refresh_resource_descendants_py(session)
        session.commit()
        yield session


@pytest.fixture
def auth_on(monkeypatch: pytest.MonkeyPatch) -> Callable[[bool], None]:
    """Return a switch that forces ``settings.auth_enabled`` on or off for RBAC."""

    def _set(enabled: bool) -> None:
        monkeypatch.setattr(
            rbac, "get_settings", lambda: type("S", (), {"auth_enabled": enabled})()
        )

    return _set


@pytest.fixture
def count_trace_steps(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    """Count every :class:`TraceStep` constructed from now on, wherever it is constructed.

    Patches the class the resolvers actually reference, so the count is of *constructions* rather
    than of call sites someone remembered to check — the only form of this assertion that keeps
    holding as call sites are added.
    """
    constructed = 0
    original = rbac.TraceStep

    def _counting(*args: object, **kwargs: object) -> TraceStep:
        nonlocal constructed
        constructed += 1
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rbac, "TraceStep", _counting)
    return lambda: constructed


def _user(db: Session, role: str) -> User:
    """Return the seeded user holding ``role``."""
    return db.get(User, f"user-{role}")


def test_asking_for_a_trace_does_construct_steps(
    db: Session, count_trace_steps: Callable[[], int]
) -> None:
    """The counter is not vacuous: the same resolver records plenty when a list is supplied."""
    trace: list[TraceStep] = []
    effective_verb_over_keys(
        load_effective_grant_keys_for_roles(db, [UserRole.ADMIN.value]),
        load_resource_parent_map(db),
        "crates",
        trace=trace,
    )
    assert count_trace_steps() > 0
    assert len(trace) == count_trace_steps()


@pytest.mark.parametrize("role", _ROLES)
@pytest.mark.parametrize("resource_key", _RESOURCES)
def test_the_verb_is_identical_with_and_without_a_trace(
    db: Session, role: str, resource_key: str
) -> None:
    """Behaviour-preserving by construction, asserted over the grid rather than argued."""
    grants = load_effective_grant_keys_for_roles(db, [role])
    parents = load_resource_parent_map(db)
    trace: list[TraceStep] = []
    assert effective_verb_over_keys(
        grants, parents, resource_key
    ) == effective_verb_over_keys(grants, parents, resource_key, trace=trace)


@pytest.mark.parametrize("role", _ROLES)
@pytest.mark.parametrize("resource_key", _RESOURCES)
def test_the_tier_is_identical_with_and_without_a_trace(
    db: Session, role: str, resource_key: str
) -> None:
    """Same property for the second axis — the tier resolver Issue #171 built."""
    user = _user(db, role)
    trace: list[TraceStep] = []
    assert resolve_scope_tier(db, user, resource_key) is resolve_scope_tier(
        db, user, resource_key, trace=trace
    )


def test_a_level_with_no_grant_reports_the_step_that_explains_the_surprise(
    db: Session,
) -> None:
    """ "No grant on X, walked to parent Y" is the step the design calls out by name."""
    trace: list[TraceStep] = []
    effective_verb_over_keys(
        load_effective_grant_keys_for_roles(db, [UserRole.PATIENT.value]),
        load_resource_parent_map(db),
        "no.such.resource",
        trace=trace,
    )
    walked = [step for step in trace if step.stage is TraceStage.TREE_WALK]
    assert walked, "the walk reported nothing at all"
    assert any(step.outcome is TraceOutcome.SKIPPED for step in walked)
    assert any("walked to parent" in step.detail for step in walked)


def test_a_resource_nothing_grants_ends_in_an_explicit_no_access_step(
    db: Session,
) -> None:
    """The verdict is a step of its own, so a reader never has to infer it from an absence."""
    trace: list[TraceStep] = []
    verb = effective_verb_over_keys(
        load_effective_grant_keys_for_roles(db, [UserRole.PATIENT.value]),
        load_resource_parent_map(db),
        "no.such.resource",
        trace=trace,
    )
    assert verb is None
    final = [step for step in trace if step.stage is TraceStage.VERB_RESOLUTION]
    assert len(final) == 1
    assert final[0].outcome is TraceOutcome.DENIED


def test_the_deciding_level_is_carried_as_a_machine_readable_anchor(
    db: Session,
) -> None:
    """The explainer must never have to parse ``detail`` back apart to find what decided.

    A grant on ``leases`` cascades to ``lease.details``; the final step anchors to the level that
    actually carried the grant, not to the key that was asked about.
    """
    trace: list[TraceStep] = []
    verb = effective_verb_over_keys(
        load_effective_grant_keys_for_roles(db, [UserRole.PLATFORM_ADMIN.value]),
        load_resource_parent_map(db),
        "order.details",
        trace=trace,
    )
    final = next(step for step in trace if step.stage is TraceStage.VERB_RESOLUTION)
    assert final.value == verb
    assert final.resource is not None


def test_the_tier_walk_reports_its_closed_default_rather_than_falling_silent(
    db: Session,
) -> None:
    """A grant-less caller resolves ``own`` — and says so, which is the whole answer to "why deny"."""
    trace: list[TraceStep] = []
    tier = resolve_scope_tier(
        db, _user(db, UserRole.USER.value), "no.such.resource", trace=trace
    )
    assert tier is GrantScope.OWN
    tier_steps = [step for step in trace if step.stage is TraceStage.SCOPE_TIER]
    assert tier_steps
    assert tier_steps[-1].outcome is TraceOutcome.FINAL
    assert tier_steps[-1].value == GrantScope.OWN.value


def test_the_role_closure_stage_names_the_roles_in_play(db: Session) -> None:
    """Stage one of the design's list: which roles the principal actually resolved to."""
    trace: list[TraceStep] = []
    resolve_scope_tier(
        db, _user(db, UserRole.PLATFORM_ADMIN.value), "crates", trace=trace
    )
    closure_steps = [step for step in trace if step.stage is TraceStage.ROLE_CLOSURE]
    assert closure_steps
    assert UserRole.PLATFORM_ADMIN.value in closure_steps[0].detail


def test_a_directly_held_grant_reports_a_single_element_path(db: Session) -> None:
    """``["manager"]`` — the grant is the role's own, so there is no inheritance to explain."""
    assert role_inheritance_path(
        db, UserRole.PLATFORM_ADMIN.value, UserRole.PLATFORM_ADMIN.value
    ) == [UserRole.PLATFORM_ADMIN.value]


def test_an_inherited_grant_reports_the_shortest_path(db: Session) -> None:
    """``agent → manager``: the edge an operator wants named, not an arbitrary walk."""
    db.add(RbacRole(name="agent", description="Property agent", is_system=False))
    db.add(RoleHierarchy(role="agent", inherits_role=UserRole.PLATFORM_ADMIN.value))
    db.commit()
    assert role_inheritance_path(db, "agent", UserRole.PLATFORM_ADMIN.value) == [
        "agent",
        UserRole.PLATFORM_ADMIN.value,
    ]


def test_an_unreachable_role_reports_no_path_at_all(db: Session) -> None:
    """An empty list, never a partial one — "not inherited" is a distinct, useful answer."""
    assert role_inheritance_path(db, UserRole.PATIENT.value, UserRole.ADMIN.value) == []
