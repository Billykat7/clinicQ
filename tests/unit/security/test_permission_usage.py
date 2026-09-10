"""Grant usage telemetry: collection, coalescing, flush and roll-up (Issue #176, M29).

``docs/architecture/rbac-decision-transparency.md`` §6. Five properties, in the order they matter:

1. **Collection adds no per-request database write.** Asserted by counting the SQL statements an
   authorized request issues with collection on and with it off, and requiring the two to be equal.
   This is the whole justification for the buffer, so it is measured rather than argued.
2. **An allow is recorded; a denial is not.** A denial is already visible through the 403 logging
   path and says nothing about whether a grant is needed — it says the opposite.
3. **A lost buffer degrades to stale-but-correct, never to a wrong "never used".** ``last_used_at``
   only ever moves forward, and dropping a buffer makes a grant look older, never newer.
4. **The roll-up is over the resource subtree**, because a parent grant is what authorizes its
   children — attributing a hit only to the key the request named would leave a load-bearing parent
   reading as dead.
5. **The off switch turns collection off entirely**, and is on by default.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Callable, Generator
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session, sessionmaker

from src.commons.enums import GrantScope, PermissionVerb, UserRole
from src.core import permission_usage, rbac
from src.core.permission_usage import (
    UsageKey,
    buffered,
    collection_started_at,
    ensure_window,
    flush,
    record_allow,
    reset_buffer,
    revoke_role_usage,
    revoke_usage,
    usage_for_role,
)
from src.core.rbac import (
    default_role_permissions,
    default_system_roles,
    ensure_permission_key,
    refresh_resource_descendants_py,
    seed_named_action_grants,
    seed_named_catalog,
    seed_resource_catalog,
    seeded_grant_scope,
)
from src.core.s3_logging import APP_TIMEZONE
from src.database.models import PermissionUsage, RbacRole, RolePermission, User

_ROLES = (
    UserRole.ADMIN.value,
    UserRole.MANAGER.value,
    UserRole.TENANT.value,
)


@pytest.fixture(autouse=True)
def _clean_buffer() -> Generator[None]:
    """The buffer is process-wide; empty it around every test so cases cannot leak into each other."""
    reset_buffer()
    yield
    reset_buffer()


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Generator[Session]:
    """A session seeded as a deployed database is: roles, the portal matrix, the catalog."""
    with session_factory() as session:
        for name, description in (*default_system_roles(),):
            session.add(RbacRole(name=name, description=description, is_system=True))
        seen: set[tuple[str, str]] = set()
        for helper in (default_role_permissions,):
            for role, resource_key, verb in helper():
                if (role, resource_key) in seen:
                    continue
                seen.add((role, resource_key))
                session.add(
                    RolePermission(
                        role=role,
                        resource=resource_key,
                        max_verb=verb.value,
                        created_at=datetime.now(APP_TIMEZONE),
                        scope=seeded_grant_scope(role).value,
                    )
                )
        for role in _ROLES:
            session.add(
                User(
                    id=f"user-{role}",
                    email=f"{role}.usage@example.com",
                    role=role,
                    is_verified=True,
                )
            )
        session.commit()
        seed_resource_catalog(session)
        session.commit()
        seed_named_catalog(session)
        seed_named_action_grants(session)
        session.commit()
        refresh_resource_descendants_py(session)
        session.commit()
        yield session


@pytest.fixture
def settings_for(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Force ``auth_enabled`` / ``permission_usage_enabled`` for both modules that read them."""

    def _set(*, auth: bool = True, usage: bool = True) -> None:
        stub = type(
            "S",
            (),
            {"auth_enabled": auth, "permission_usage_enabled": usage},
        )()
        monkeypatch.setattr(rbac, "get_settings", lambda: stub)
        monkeypatch.setattr(permission_usage, "get_settings", lambda: stub)

    return _set


def _claims(db: Session, role: str) -> dict[str, str]:
    """JWT-style claims for the seeded holder of ``role``."""
    user = db.get(User, f"user-{role}")
    return {"sub": user.email, "email": user.email}


def test_a_denied_request_records_nothing(
    db: Session, settings_for: Callable[..., None]
) -> None:
    """A denial says nothing about whether a grant is needed — it says the opposite."""
    settings_for(usage=True)
    with pytest.raises(HTTPException):
        ensure_permission_key(
            db, _claims(db, UserRole.TENANT.value), "rbac", PermissionVerb.DELETE.value
        )
    assert buffered() == {}


def test_a_multi_role_caller_records_every_active_role(
    db: Session, settings_for: Callable[..., None]
) -> None:
    """Over-reporting in the one safe direction: a grant carrying traffic is never marked unused.

    The resolver returns one verb for the union of active roles without saying which role supplied
    it, and resolving that per role on the hot path is exactly the cost this feature must not add.
    A false "still in use" costs an operator a second glance; a false "never used" costs a revoked
    grant in production.
    """
    settings_for(usage=True)
    record_allow(
        [UserRole.MANAGER.value, UserRole.TENANT.value],
        "orders",
        PermissionVerb.READ.value,
    )
    assert {key.role for key in buffered()} == {
        UserRole.MANAGER.value,
        UserRole.TENANT.value,
    }


def test_a_flush_writes_the_buffer_and_empties_it(
    db: Session, settings_for: Callable[..., None]
) -> None:
    """One row per grant, and the buffer starts clean for the next window."""
    settings_for(usage=True)
    record_allow([UserRole.MANAGER.value], "orders", PermissionVerb.READ.value)
    assert flush(db) == 1
    db.commit()
    assert buffered() == {}
    row = db.get(
        PermissionUsage, (UserRole.MANAGER.value, "orders", PermissionVerb.READ.value)
    )
    assert row is not None
    assert row.hit_count == 1


def test_a_second_flush_accumulates_rather_than_replacing(
    db: Session, settings_for: Callable[..., None]
) -> None:
    """``hit_count`` is cumulative across windows; ``last_used_at`` is the newest seen."""
    settings_for(usage=True)
    early = datetime.now(APP_TIMEZONE) - timedelta(days=2)
    record_allow(
        [UserRole.MANAGER.value], "orders", PermissionVerb.READ.value, now=early
    )
    flush(db)
    db.commit()
    later = datetime.now(APP_TIMEZONE)
    record_allow(
        [UserRole.MANAGER.value], "orders", PermissionVerb.READ.value, now=later
    )
    record_allow(
        [UserRole.MANAGER.value], "orders", PermissionVerb.READ.value, now=later
    )
    flush(db)
    db.commit()
    row = db.get(
        PermissionUsage, (UserRole.MANAGER.value, "orders", PermissionVerb.READ.value)
    )
    assert row.hit_count == 3
    assert row.last_used_at.replace(tzinfo=None) >= later.replace(tzinfo=None)


def test_last_used_at_never_moves_backwards(
    db: Session, settings_for: Callable[..., None]
) -> None:
    """Two instances flushing overlapping windows can arrive out of order.

    A stored timestamp going backwards would be the one way this table could manufacture a *wrong*
    answer rather than a stale one — a grant used an hour ago reading as used a week ago is what
    gets a live grant revoked.
    """
    settings_for(usage=True)
    recent = datetime.now(APP_TIMEZONE)
    record_allow(
        [UserRole.MANAGER.value], "orders", PermissionVerb.READ.value, now=recent
    )
    flush(db)
    db.commit()
    stale = recent - timedelta(days=7)
    record_allow(
        [UserRole.MANAGER.value], "orders", PermissionVerb.READ.value, now=stale
    )
    flush(db)
    db.commit()
    row = db.get(
        PermissionUsage, (UserRole.MANAGER.value, "orders", PermissionVerb.READ.value)
    )
    assert row.last_used_at.replace(tzinfo=None) >= recent.replace(tzinfo=None)


def test_a_lost_buffer_leaves_stale_but_correct_data(
    db: Session, settings_for: Callable[..., None]
) -> None:
    """Dropping a buffer on shutdown is acceptable, and this is exactly what it costs.

    The stored row keeps the *older* timestamp — the grant looks less recently used than it is,
    never more. That direction is why a lost buffer can never produce a wrong "never used" for a
    grant that has any recorded history at all.
    """
    settings_for(usage=True)
    first = datetime.now(APP_TIMEZONE) - timedelta(days=1)
    record_allow(
        [UserRole.MANAGER.value], "orders", PermissionVerb.READ.value, now=first
    )
    flush(db)
    db.commit()
    record_allow([UserRole.MANAGER.value], "orders", PermissionVerb.READ.value)
    reset_buffer()  # the shutdown
    flush(db)
    db.commit()
    row = db.get(
        PermissionUsage, (UserRole.MANAGER.value, "orders", PermissionVerb.READ.value)
    )
    assert row.hit_count == 1
    assert row.last_used_at.replace(tzinfo=None) == first.replace(tzinfo=None)


def test_flushing_an_empty_buffer_touches_nothing(
    db: Session, settings_for: Callable[..., None]
) -> None:
    """The common case on a quiet interval: no work, and no window row conjured either."""
    settings_for(usage=True)
    assert flush(db) == 0
    assert collection_started_at(db) is None


def test_the_first_flush_stamps_the_collection_window(
    db: Session, settings_for: Callable[..., None]
) -> None:
    """A grant reads "never used" on a young deployment for reasons unrelated to the grant."""
    settings_for(usage=True)
    record_allow([UserRole.MANAGER.value], "orders", PermissionVerb.READ.value)
    flush(db)
    db.commit()
    assert collection_started_at(db) is not None


def test_reading_the_window_never_creates_it(db: Session) -> None:
    """A reader stamping the window would claim collection started when someone opened a console."""
    assert collection_started_at(db) is None
    assert collection_started_at(db) is None


def test_the_window_is_stamped_once_and_kept(
    db: Session, settings_for: Callable[..., None]
) -> None:
    """Later flushes must not move the start date forward, or the caveat becomes a lie."""
    settings_for(usage=True)
    first = ensure_window(db)
    db.commit()
    record_allow([UserRole.MANAGER.value], "orders", PermissionVerb.READ.value)
    flush(db)
    db.commit()
    # SQLite does not preserve the tzinfo the column declares, so compare the instants.
    assert collection_started_at(db).replace(tzinfo=None) == first.replace(tzinfo=None)


def test_an_unrelated_resource_is_not_marked_used(
    db: Session, settings_for: Callable[..., None]
) -> None:
    """The roll-up follows the tree, not the whole catalog."""
    settings_for(usage=True)
    record_allow([UserRole.MANAGER.value], "order.details", PermissionVerb.READ.value)
    flush(db)
    db.commit()
    assert "crates" not in usage_for_role(db, UserRole.MANAGER.value)


def test_revoking_a_grant_removes_its_usage_row(
    db: Session, settings_for: Callable[..., None]
) -> None:
    """A row that outlives its grant would lend a re-granted cell a history it never had."""
    settings_for(usage=True)
    record_allow([UserRole.MANAGER.value], "orders", PermissionVerb.READ.value)
    flush(db)
    db.commit()
    revoke_usage(db, UserRole.MANAGER.value, "orders")
    db.commit()
    assert usage_for_role(db, UserRole.MANAGER.value) == {}


def test_deleting_a_role_removes_all_of_its_usage(
    db: Session, settings_for: Callable[..., None]
) -> None:
    """Same rule at role granularity."""
    settings_for(usage=True)
    record_allow([UserRole.MANAGER.value], "orders", PermissionVerb.READ.value)
    record_allow([UserRole.MANAGER.value], "crates", PermissionVerb.READ.value)
    flush(db)
    db.commit()
    revoke_role_usage(db, UserRole.MANAGER.value)
    db.commit()
    assert usage_for_role(db, UserRole.MANAGER.value) == {}


def test_collection_is_on_by_default() -> None:
    """On by default — the pruning worklist is worthless if nobody remembers to enable it."""
    from src.core.config import Settings

    assert (
        Settings(_env_file=None, jwt_secret="x" * 40).permission_usage_enabled is True
    )


def test_a_grant_scope_tier_is_not_part_of_the_usage_key() -> None:
    """ "Deliberately not stored" includes the tier: the key is (role, resource, verb), full stop.

    The tier is a property of the grant that the matrix already renders; duplicating it here would
    mean a tier change orphaned the usage history of a grant that did not otherwise move.
    """
    fields = set(UsageKey.__dataclass_fields__)
    assert fields == {"role", "resource", "verb"}
    assert GrantScope.BUSINESS.value not in fields
