"""Admin RBAC API: users (Issue #4), roles, permissions matrix, and role assignment (Issue #6).

Ported from the ``maps`` project (``rbac_admin.py``). Endpoints live under
``/admin/rbac``:

* ``/users`` — manage the identity fields of ``User`` rows plus the email operations
  tied to them (activation email on create, verification resend on update). Deletes are
  soft (``is_deleted=true``); soft-deleted users are excluded from listings/detail and
  cannot sign in.
* ``/roles`` — define custom roles and their per-resource permission matrix; system
  roles (``user``, ``admin``) are protected from deletion.
* ``/users/{id}/roles`` — assign or change a user's role; the change takes effect on
  their next ``GET /auth/me`` (which recomputes effective permissions).

Access control: every endpoint is gated by verb-based RBAC (Issue #5). User endpoints
use the ``users`` resource; role/permission/assignment endpoints use the ``rbac``
resource — READ for reads, CREATE/UPDATE for writes, DELETE for role deletion.
Enforcement is unauthenticated -> 401, under-privileged -> 403 via
``src.api.rbac_deps``; the seeded ``admin`` role holds DELETE (which covers all verbs).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from src.api.rbac_deps import require
from src.commons.enums import (
    GrantScope,
    PermissionAuditAction,
    PermissionAuditTargetType,
    PermissionEffect,
    PermissionVerb,
    UserRole,
)
from src.core.config import Settings, get_settings
from src.core.email_send import (
    EmailDeliveryError,
    send_activation_email,
)
from src.core.nav_registry import NAV_DESTINATIONS
from src.core.permission_usage import (
    collection_started_at,
    revoke_role_usage,
    revoke_usage,
    usage_for_role,
)
from src.core.rbac import (
    TraceStep,
    active_roles_for_user,
    catalog_display_name,
    default_grant_scope,
    effective_verb_over_keys,
    granted_covers_required,
    load_effective_grant_keys_for_role,
    load_effective_grant_keys_for_roles,
    load_resource_parent_map,
    load_role_grant_keys,
    permission_key,
    rebuild_resource_closure,
    role_inherits_cycle,
)
from src.core.rbac_language import (
    assignment_status,
    scope_tier_label,
    scope_tier_meaning,
    trace_stage_label,
)
from src.core.rbac_manifest import iter_resources
from src.core.rbac_manifest_registry import (
    ALL_MANIFESTS,
    manifest_parent_map,
    manifest_resource_keys,
)
from src.core.rbac_simulator import (
    SimulationContext,
    SimulationError,
    SimulationInstance,
    SimulationPrincipal,
    SimulationResult,
    SimulationTarget,
    explain,
    simulate,
)
from src.core.s3_logging import APP_TIMEZONE
from src.core.scope import ASSIGNMENT_SCOPE_TYPE, scope_tiers_for_roles
from src.core.security import create_activation_token, get_current_user
from src.core.verification import enforce_verification_resend_cooldown
from src.database.models import (
    Action,
    NavGateOverride,
    Permission,
    PermissionAuditLog,
    RbacRole,
    Resource,
    RoleHierarchy,
    RolePermission,
    User,
    UserRoleAssignment,
)
from src.database.session import get_db
from src.schemas.bulk import (
    BulkDeleteIn,
    BulkDeleteOut,
    BulkDeleteResultRow,
    bulk_delete_summary,
)
from src.schemas.rbac import (
    ActionCreateIn,
    ActionListOut,
    ActionOut,
    ActionPatchIn,
    CatalogOut,
    DecidingStatementOut,
    NavGateListOut,
    NavGateOut,
    NavGatePatchIn,
    PermissionAuditListOut,
    PermissionAuditRowOut,
    PermissionCellIn,
    PermissionCreateIn,
    PermissionListOut,
    PermissionOut,
    PolicyStatement,
    RbacRoleCreateIn,
    RbacRoleListOut,
    RbacRoleOut,
    RbacRolePatchIn,
    ResolvedInstancesOut,
    ResourceCreateIn,
    ResourceListOut,
    ResourceOut,
    ResourcePatchIn,
    RoleInheritanceAddIn,
    RoleInheritanceGetOut,
    RolePermissionRowOut,
    RolePermissionsGetOut,
    RolePermissionsPutIn,
    RolePolicyDocument,
    RolePolicyPutResult,
    SimulateIn,
    SimulateOut,
    SimulationExplanationOut,
    TraceStepOut,
    UserEffectiveAccessOut,
    UserEffectiveAccessRowOut,
    UserRoleAssignmentAddIn,
    UserRoleAssignmentOut,
    UserRoleAssignmentsOut,
    UserRolesGetOut,
    UserRolesPutIn,
)

router = APIRouter(prefix="/admin/rbac", tags=["admin"])

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
# The authenticated principal (claims dict). Used to stamp ``granted_by`` on role assignments; the
# RBAC verb gate is enforced separately by the ``Rbac*Dep`` dependencies.
CurrentUser = Annotated[dict, Depends(get_current_user)]

# Issue #152: resolved through the generic ``require`` factory (src.api.rbac_deps) against the
# ``rbac`` manifest (rbac_manifest.py, co-located in this package) instead of the named
# ``require_rbac_*`` functions — mirrors the Applications pilot (Issue #149). A fresh-DB bootstrap
# is unaffected: ``rbac`` and the admin role's grant on it are seeded by the enum-derived catalog
# migration, which runs before ``rbac_manifest_sync`` ever needs to (verified for Issue #152).
#: The RBAC console's own resource key. Holding DELETE on it is what "is an administrator of the
#: permission system" means since Issue #160 (M28) — the property the last-administrator guards
#: below protect, in place of the literal role name they used to compare against.
RBAC_RESOURCE = "rbac"

# Issue #166 (M28): ``business`` tier — this backs a whole-business back-office console,
# and its nav destination declares the same tier, a parity
# ``tests/unit/security/test_nav_enforcement_parity.py`` now asserts. No seeded role loses
# access: every role that holds this grant holds it at ``business`` already.
RbacReadDep = Annotated[
    None, Depends(require("rbac", "read", scope=GrantScope.BUSINESS))
]
RbacCreateDep = Annotated[
    None, Depends(require("rbac", "create", scope=GrantScope.BUSINESS))
]
RbacUpdateDep = Annotated[
    None, Depends(require("rbac", "update", scope=GrantScope.BUSINESS))
]
RbacDeleteDep = Annotated[
    None, Depends(require("rbac", "delete", scope=GrantScope.BUSINESS))
]

# The user directory is a **separate** resource from the console it lives in: Issue #155 confirmed
# ``users`` must be enforced in its own right, not inherited from the ``rbac`` gate. Issue #154
# (M27) moved the last named ``require_users_*`` functions out of ``src.api.rbac_deps`` and onto
# the generic factory against the ``users`` manifest node (declared under the ``reports``
# admin-shell hub in this package's ``rbac_manifest.py``) — the same migration every other module
# already went through.
# Business-tier for the same reason as ``rbac`` above (Issue #168): the user directory is the
# whole-business one, and the console's template gates now declare the tier alongside the verb — a
# parity ``tests/unit/security/test_rbac_ui_action_gating.py`` asserts pair by pair. Only ``admin``
# holds these grants, at ``business``, so nothing changes for a real deployment.
UsersReadDep = Annotated[
    None, Depends(require("users", "read", scope=GrantScope.BUSINESS))
]
UsersCreateDep = Annotated[
    None, Depends(require("users", "create", scope=GrantScope.BUSINESS))
]
UsersUpdateDep = Annotated[
    None, Depends(require("users", "update", scope=GrantScope.BUSINESS))
]
UsersDeleteDep = Annotated[
    None, Depends(require("users", "delete", scope=GrantScope.BUSINESS))
]


# --------------------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------------------


class AdminUserRowOut(BaseModel):
    """One user row in the admin search results."""

    id: str
    email: str
    role: str
    is_active: bool = True
    is_verified: bool = False
    created_at: datetime | None = None


class AdminUserListOut(BaseModel):
    """Paginated user search results."""

    items: list[AdminUserRowOut]
    total: int = Field(description="Total rows matching filters (before offset/limit).")


class AdminUserDetailOut(BaseModel):
    """Full user detail for the admin view panel."""

    id: str
    email: str
    role: str
    is_active: bool = True
    is_verified: bool = False
    first_name: str | None = None
    last_name: str | None = None
    avatar_url: str | None = None
    last_login: datetime | None = None
    created_at: datetime | None = None


class AdminUserCreateIn(BaseModel):
    """Create a new user (email + optional name + initial role)."""

    email: EmailStr = Field(description="Email address for the new account.")
    role: str = Field(default=UserRole.USER.value, min_length=1, max_length=50)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    send_activation_email: bool = Field(
        default=True,
        description="If true, send the account activation email after creation.",
    )


class AdminUserPatchIn(BaseModel):
    """Partial update for an admin-managed user (omit fields you do not change)."""

    email: EmailStr | None = Field(default=None)
    role: str | None = Field(default=None, min_length=1, max_length=50)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    resend_verification: bool = Field(
        default=False,
        description="If true, resend the activation email (unverified accounts only).",
    )


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


# Bulk-delete request/response models are shared across every console (Issue #116). Kept as a
# module-local alias so existing references (``_bulk_delete_summary``) stay unchanged.
_bulk_delete_summary = bulk_delete_summary


def _rbac_administrator_roles(db: Session) -> set[str]:
    """Return every role whose **effective** grants include ``rbac`` DELETE (Issue #160, M28).

    Resolved through the ordinary engine — inheritance closure, resource tree, deny-beats-allow —
    so a role that reaches ``rbac:DELETE`` by inheriting another role counts, and one explicitly
    denied it does not. ``rbac:DELETE`` is the bar because it is exactly what ``/admin/rbac``'s own
    console requires (``RbacDeleteDep``): "can this user fully administer the permission system,
    under whatever name their role happens to have".
    """
    parents = load_resource_parent_map(db)
    role_names = {
        name
        for name in db.execute(select(RbacRole.name)).scalars().all()
        if name is not None
    }
    role_names |= {
        role
        for role in db.execute(select(User.role).distinct()).scalars().all()
        if role
    }
    return {
        role
        for role in role_names
        if granted_covers_required(
            effective_verb_over_keys(
                load_effective_grant_keys_for_roles(db, [role]), parents, RBAC_RESOURCE
            ),
            PermissionVerb.DELETE,
        )
    }


def _is_rbac_administrator(db: Session, user: User) -> bool:
    """Whether ``user`` currently holds ``rbac`` DELETE through any of their active roles."""
    return bool(active_roles_for_user(db, user) & _rbac_administrator_roles(db))


def _rbac_administrator_count(db: Session) -> int:
    """Count active, non-deleted users who effectively hold ``rbac`` DELETE (Issue #160, M28).

    The replacement for the old ``_admin_user_count``, which counted ``User.role == "admin"``
    literally. That protected nothing in a deployment whose sysadmin-tier role is called anything
    else: the last holder of a custom full-access role could be demoted or deleted with no warning,
    locking the operator out of ``/admin/rbac`` permanently. The *intent* of the guard — never let
    an operator lock themselves out — is unchanged; only the mechanism moves from a role's name to
    the grant that actually confers the power.

    Batched deliberately: the administrator roles are resolved once, then users and their active
    unscoped assignments are read in two queries and matched in Python, mirroring
    :func:`~src.core.rbac.active_roles_for_user`'s resolution (an unscoped assignment applies; a
    user with no assignment rows at all falls back to the ``User.role`` mirror). The guard runs on
    every user mutation, including inside bulk deletes, so it must not be a per-user query fan-out.
    """
    administrator_roles = _rbac_administrator_roles(db)
    if not administrator_roles:
        return 0
    now = datetime.now(APP_TIMEZONE)
    assignments_by_user: dict[str, set[str]] = {}
    has_any_assignment: set[str] = set()
    for user_id, role, expires_at, scope_type in db.execute(
        select(
            UserRoleAssignment.user_id,
            UserRoleAssignment.role,
            UserRoleAssignment.expires_at,
            UserRoleAssignment.scope_type,
        )
    ).all():
        has_any_assignment.add(user_id)
        if scope_type is not None or (expires_at is not None and expires_at <= now):
            continue
        assignments_by_user.setdefault(user_id, set()).add(role)

    count = 0
    for user_id, mirror_role in db.execute(
        select(User.id, User.role).where(
            User.is_deleted.is_(False), User.is_active.is_(True)
        )
    ).all():
        roles = assignments_by_user.get(user_id, set())
        if not roles and user_id not in has_any_assignment:
            roles = {mirror_role or UserRole.USER.value}
        if roles & administrator_roles:
            count += 1
    return count


def _detail_out(user: User) -> AdminUserDetailOut:
    """Build the detail response from a User row."""
    return AdminUserDetailOut(
        id=str(user.id),
        email=user.email,
        role=user.role or UserRole.USER.value,
        is_active=bool(user.is_active),
        is_verified=bool(user.is_verified),
        first_name=user.first_name,
        last_name=user.last_name,
        avatar_url=user.avatar_url,
        last_login=user.last_login,
        created_at=user.created_at,
    )


def _load_active_user(db: Session, user_id: str) -> User:
    """Return a non-deleted user by id, or raise 404."""
    user = db.execute(
        select(User).where(User.id == user_id, User.is_deleted.is_(False))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    return user


async def _send_activation(
    request: Request, db: Session, user: User, settings: Settings
) -> None:
    """Send the activation email to ``user`` and record the send time (or raise 503).

    The SMTP send is offloaded to a worker thread so it never stalls the event loop; the
    surrounding session/ORM work stays on the loop, before and after the ``await``.
    """
    email = user.email.lower().strip()
    base_url = str(request.base_url).rstrip("/")
    token = create_activation_token(user_id=str(user.id), email=email)
    activation_link = f"{base_url}/api/v1/auth/activate?token={quote(token, safe='')}"
    try:
        await asyncio.to_thread(
            send_activation_email,
            to_email=email,
            activation_link=activation_link,
            expire_hours=settings.activation_link_expire_hours,
        )
    except EmailDeliveryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not send activation email. Try again later.",
        ) from exc
    user.last_verification_email_sent_at = datetime.now(UTC)


# --------------------------------------------------------------------------------------
# Sorting (Issue #115) — a safe per-resource allow-list, never raw column injection
# --------------------------------------------------------------------------------------


def _sort_clause(
    sort: str | None,
    order: str | None,
    allowed: dict[str, Any],
    default_key: str,
) -> Any:
    """Resolve a safe ``ORDER BY`` from a ``sort``/``order`` pair against an allow-list.

    An unknown or missing ``sort`` key falls back to ``default_key`` (a value in ``allowed``),
    so a stale or hostile query can never reach a column that was not explicitly whitelisted —
    the mapping is the only path from a client string to a SQL column. ``order`` is descending
    only when explicitly ``desc``; anything else is ascending.
    """
    column = allowed.get((sort or "").strip().lower(), allowed[default_key])
    descending = (order or "").strip().lower() == "desc"
    return column.desc() if descending else column.asc()


# Sortable columns for the users list — resource string → mapped ORDER BY column.
_USER_SORT_COLUMNS: dict[str, Any] = {
    "email": User.email,
    "role": User.role,
    "status": User.is_verified,
}


# --------------------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------------------


@router.get("/users", response_model=AdminUserListOut)
async def list_users(
    _rbac: UsersReadDep,
    db: DbSession,
    q: Annotated[str | None, Query(description="Email substring filter.")] = None,
    role: Annotated[
        str | None, Query(description="Exact assigned role name filter.")
    ] = None,
    sort: Annotated[
        str | None,
        Query(description="Sort column: email, role or status (unknown = default)."),
    ] = None,
    order: Annotated[
        str | None, Query(description="Sort direction: asc (default) or desc.")
    ] = None,
    offset: Annotated[int, Query(ge=0, description="Row offset for pagination.")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Max rows per page.")] = 20,
) -> AdminUserListOut:
    """List and search users by email/role (soft-deleted users are excluded)."""
    filters: list[Any] = [User.is_deleted.is_(False)]
    if q and q.strip():
        filters.append(User.email.ilike(f"%{q.strip()}%"))
    if role and role.strip():
        filters.append(User.role == role.strip())
    where_clause = and_(*filters)

    total = int(
        db.execute(
            select(func.count()).select_from(User).where(where_clause)
        ).scalar_one()
    )
    # Email is unique, so it is the stable tiebreaker that keeps paging deterministic when the
    # primary sort column (e.g. role or status) has ties.
    order_by = _sort_clause(sort, order, _USER_SORT_COLUMNS, "email")
    rows = (
        db.execute(
            select(User)
            .where(where_clause)
            .order_by(order_by, User.email)
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    items = [
        AdminUserRowOut(
            id=str(r.id),
            email=r.email,
            role=r.role or UserRole.USER.value,
            is_active=bool(r.is_active),
            is_verified=bool(r.is_verified),
            created_at=r.created_at,
        )
        for r in rows
    ]
    return AdminUserListOut(items=items, total=total)


@router.post(
    "/users", response_model=AdminUserDetailOut, status_code=status.HTTP_201_CREATED
)
async def create_user(
    request: Request,
    _rbac: UsersCreateDep,
    db: DbSession,
    settings: SettingsDep,
    current_user: CurrentUser,
    body: AdminUserCreateIn,
) -> AdminUserDetailOut:
    """Create a new user, optionally sending an activation email."""
    email = body.email.lower().strip()
    role = (body.role or "").strip() or UserRole.USER.value

    existing = db.execute(
        select(User).where(User.email == email, User.is_deleted.is_(False))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already exists.",
        )

    user = User(
        id=str(uuid4()),
        email=email,
        role=role,
        first_name=(body.first_name or None),
        last_name=(body.last_name or None),
        is_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Seed the unscoped assignment so resolution reads ``user_roles`` from day one (Issue #136).
    _sync_unscoped_assignment(db, user, role, _acting_user_id(db, current_user))
    db.commit()
    db.refresh(user)

    if body.send_activation_email:
        await _send_activation(request, db, user, settings)
        db.commit()
        db.refresh(user)

    return _detail_out(user)


@router.get("/users/{user_id}", response_model=AdminUserDetailOut)
async def get_user(
    _rbac: UsersReadDep,
    db: DbSession,
    user_id: str,
) -> AdminUserDetailOut:
    """Return one non-deleted user for the admin detail panel."""
    return _detail_out(_load_active_user(db, user_id))


@router.patch("/users/{user_id}", response_model=AdminUserDetailOut)
async def patch_user(
    request: Request,
    _rbac: UsersUpdateDep,
    db: DbSession,
    settings: SettingsDep,
    current_user: CurrentUser,
    user_id: str,
    body: AdminUserPatchIn,
) -> AdminUserDetailOut:
    """Update a user's email/name/role and optionally resend the verification email."""
    raw = body.model_dump(exclude_unset=True)
    resend = bool(raw.pop("resend_verification", False))
    if not raw and not resend:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update.",
        )

    user = _load_active_user(db, user_id)

    if "email" in raw:
        new_email = (raw["email"] or "").strip().lower()
        if new_email != (user.email or "").strip().lower():
            conflict = db.execute(
                select(User).where(
                    User.email == new_email,
                    User.is_deleted.is_(False),
                    User.id != user_id,
                )
            ).scalar_one_or_none()
            if conflict is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="User already exists.",
                )
            user.email = new_email

    if "role" in raw:
        new_role = (raw["role"] or "").strip() or UserRole.USER.value
        prev = user.role or UserRole.USER.value
        if new_role != prev:
            # Issue #160 (M28): "is this user an administrator" is now the grant they hold
            # (``rbac:DELETE``), not the string their role is named, and the new role is only a
            # demotion if it does not confer the same power.
            administrator_roles = _rbac_administrator_roles(db)
            demoting_last_admin = (
                _is_rbac_administrator(db, user)
                and new_role not in administrator_roles
                and _rbac_administrator_count(db) <= 1
            )
            if demoting_last_admin:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot remove the last admin user.",
                )
            # Keep the unscoped assignment in sync with the mirror (Issue #136).
            _sync_unscoped_assignment(
                db, user, new_role, _acting_user_id(db, current_user)
            )

    if "first_name" in raw:
        fn = raw["first_name"]
        user.first_name = (fn.strip() if isinstance(fn, str) else None) or None
    if "last_name" in raw:
        ln = raw["last_name"]
        user.last_name = (ln.strip() if isinstance(ln, str) else None) or None

    if resend:
        if user.is_verified:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is already verified.",
            )
        enforce_verification_resend_cooldown(user, settings)
        await _send_activation(request, db, user, settings)

    db.commit()
    db.refresh(user)
    return _detail_out(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    _rbac: UsersDeleteDep,
    db: DbSession,
    user_id: str,
) -> None:
    """Soft-delete a user (``is_deleted=true``, ``is_active=false``)."""
    user = _load_active_user(db, user_id)
    if _is_rbac_administrator(db, user) and _rbac_administrator_count(db) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the last admin user.",
        )
    user.is_deleted = True
    user.is_active = False
    db.commit()
    return


@router.post("/users/bulk-delete", response_model=BulkDeleteOut)
async def bulk_delete_users(
    _rbac: UsersDeleteDep,
    db: DbSession,
    body: BulkDeleteIn,
) -> BulkDeleteOut:
    """Soft-delete several users in one call, honouring per-row rules (Issue #116).

    The ``UsersDeleteDep`` guard re-checks the caller's DELETE grant on the ``users`` resource
    for this request; each id is then applied under the same soft-delete semantics as the
    single delete (``is_deleted=true``), so a soft-deleted user drops out of listings and can
    no longer sign in. The last-admin invariant is preserved **across the batch** (a flush after
    each delete keeps the admin count current), and every id gets a result row so the UI can
    report partial success rather than silently dropping failures.
    """
    results: list[BulkDeleteResultRow] = []
    for user_id in dict.fromkeys(body.ids):  # de-dupe, preserve request order
        user = db.execute(
            select(User).where(User.id == user_id, User.is_deleted.is_(False))
        ).scalar_one_or_none()
        if user is None:
            results.append(
                BulkDeleteResultRow(id=user_id, deleted=False, detail="User not found.")
            )
            continue
        if _is_rbac_administrator(db, user) and _rbac_administrator_count(db) <= 1:
            results.append(
                BulkDeleteResultRow(
                    id=user_id,
                    deleted=False,
                    detail="Cannot delete the last admin user.",
                )
            )
            continue
        user.is_deleted = True
        user.is_active = False
        db.flush()  # so the next _rbac_administrator_count sees this deletion within the batch
        results.append(BulkDeleteResultRow(id=user_id, deleted=True))
    db.commit()
    return _bulk_delete_summary(results)


# --------------------------------------------------------------------------------------
# Roles + permissions (Issue #6) — gated on the ``rbac`` resource
# --------------------------------------------------------------------------------------

_ROLE_NAME_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,49}$")


def _role_counts(db: Session, role_name: str) -> tuple[int, int]:
    """Return ``(user_count, permission_count)`` for a role."""
    user_count = int(
        db.execute(
            select(func.count()).select_from(User).where(User.role == role_name)
        ).scalar_one()
    )
    permission_count = int(
        db.execute(
            select(func.count())
            .select_from(RolePermission)
            .where(RolePermission.role == role_name)
        ).scalar_one()
    )
    return user_count, permission_count


def _role_assignment_count(db: Session, role_name: str) -> int:
    """Count how many users still hold ``role_name`` — via the mirror or a ``user_roles`` row.

    A role assigned only through a scoped/expiring ``user_roles`` row (never mirrored onto
    ``User.role``) must still block deletion (Issue #136).
    """
    mirror = int(
        db.execute(
            select(func.count()).select_from(User).where(User.role == role_name)
        ).scalar_one()
    )
    assignments = int(
        db.execute(
            select(func.count())
            .select_from(UserRoleAssignment)
            .where(UserRoleAssignment.role == role_name)
        ).scalar_one()
    )
    return mirror + assignments


def _role_row_out(row: RbacRole, user_count: int, permission_count: int) -> RbacRoleOut:
    """Build the role response from a row plus pre-computed counts (no extra queries)."""
    return RbacRoleOut(
        name=row.name,
        description=row.description,
        is_system=row.is_system,
        created_at=row.created_at,
        modified_at=row.modified_at,
        user_count=user_count,
        permission_count=permission_count,
    )


def _role_out(db: Session, row: RbacRole) -> RbacRoleOut:
    """Build the role response with user and permission counts (single-row path)."""
    user_count, permission_count = _role_counts(db, row.name)
    return _role_row_out(row, user_count, permission_count)


def _load_role(db: Session, role_name: str) -> RbacRole:
    """Return the ``rbac_role`` row by name, or raise 404."""
    row = db.execute(
        select(RbacRole).where(RbacRole.name == role_name)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found.",
        )
    return row


def _verb_from_string(raw: str | None) -> PermissionVerb | None:
    """Parse a verb string, treating ``None``/empty/``"none"`` as no access.

    Raises 422 for an unrecognized verb value.
    """
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value or value == "none":
        return None
    try:
        return PermissionVerb(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid verb: {raw!r}",
        ) from exc


def _effect_from_string(raw: str | None) -> PermissionEffect:
    """Parse an effect string, defaulting to ALLOW. Raises 422 for an unknown value."""
    value = (raw or PermissionEffect.ALLOW.value).strip().lower()
    try:
        return PermissionEffect(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid effect: {raw!r}",
        ) from exc


def _scope_from_string(raw: str | None) -> GrantScope | None:
    """Parse a scope tier, treating ``None``/empty as "not specified". 422 for an unknown value.

    ``None`` is the *preserve* signal, not a default: a cell payload that omits ``scope`` leaves
    whatever tier the row already carries (Issue #173). An unrecognised value is refused by name
    rather than silently discarded, like every other catalog value here.
    """
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value:
        return None
    try:
        return GrantScope(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid scope: {raw!r}",
        ) from exc


def _cell_values(
    value: PermissionCellIn | str | None,
) -> tuple[PermissionVerb | None, PermissionEffect, GrantScope | None]:
    """Normalise a matrix-cell payload to ``(verb, effect, scope-or-None)``.

    A bare string keeps the pre-#134 meaning (that verb, ALLOW, tier untouched). An object carries
    an explicit effect and, since Issue #173, an optional tier. ``None``/empty/``"none"`` verb
    revokes the cell (effect and tier are then irrelevant).

    The tier is ``None`` when the payload does not mention it, which the caller reads as *preserve*
    — never as *reset to the default*. That distinction is the bug this issue fixes from the other
    side: before it, the grid could not send a tier at all, so authoring one through the policy
    document and then touching the cell was a way to lose it.
    """
    if isinstance(value, PermissionCellIn):
        return (
            _verb_from_string(value.max_verb),
            _effect_from_string(value.effect),
            _scope_from_string(value.scope),
        )
    return _verb_from_string(value), PermissionEffect.ALLOW, None


def _catalog_resource_keys(db: Session) -> set[str]:
    """Return the set of valid resource keys from the DB catalog, manifests as the fallback.

    The DB ``resources`` catalog is the source of truth once seeded (Issue #139); before it is
    seeded (dev / a test that seeds only roles) the keys the registered manifests declare stand in
    (Issue #154 — what the ``PermissionResource`` enum used to provide).
    """
    keys = set(db.execute(select(Resource.key)).scalars().all())
    if keys:
        return keys
    return set(manifest_resource_keys())


def _catalog_resources_ordered(db: Session) -> list[tuple[str, str | None]]:
    """Return ``(resource_key, parent_key)`` in tree order (parents before children).

    Reads the DB catalog and walks roots→children (each level in key order) so the grid renders a
    stable, grouped tree that includes runtime-added resources. Falls back to the registered
    manifests' declaration order and parents (Issue #154) when the catalog is not yet seeded.
    """
    rows = db.execute(select(Resource.key, Resource.parent_id, Resource.id)).all()
    if not rows:
        return list(manifest_parent_map().items())
    key_by_id = {row.id: row.key for row in rows}
    parent_key_by_key = {
        row.key: (key_by_id.get(row.parent_id) if row.parent_id is not None else None)
        for row in rows
    }
    children: dict[str | None, list[str]] = {}
    for key, parent_key in parent_key_by_key.items():
        children.setdefault(parent_key, []).append(key)
    for bucket in children.values():
        bucket.sort()
    ordered: list[tuple[str, str | None]] = []
    stack: list[str] = list(reversed(children.get(None, [])))
    seen: set[str] = set()
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        ordered.append((key, parent_key_by_key.get(key)))
        stack.extend(reversed(children.get(key, [])))
    return ordered


def _role_permissions_matrix_out(db: Session, role_name: str) -> RolePermissionsGetOut:
    """Build a full matrix (one row per catalog resource) with granted + effective verb **and tier**.

    Renders from the **DB catalog** (Issue #141) so a runtime-added resource appears as a grantable,
    correctly grouped row. Resolves each resource with the string-keyed resolver over the role's own
    rows and its inheritance closure, so a cell is marked inherited via the resource tree vs. via an
    inherited role (Issue #135), deny-beats-allow throughout.

    Since Issue #173 each row also carries the grant's **third** field: the stored ``scope`` and the
    ``effective_scope`` a holder of this role resolves. Before that, the one screen whose job is
    answering "what can this role do" omitted half the answer — an admin could not tell whether
    ``properties:READ`` meant three properties or all four hundred.

    **Issue #176 (M29)** adds the fourth thing an operator needs before they will ever *remove* a
    grant: whether anything has used it. Rolled up over the resource subtree, and always alongside
    ``usage_since`` — a grant reads "never used" on a week-old deployment for reasons that have
    nothing to do with the grant, and a worklist that does not say so is worse than no worklist.
    """
    rows = db.execute(
        select(
            RolePermission.resource,
            RolePermission.max_verb,
            RolePermission.effect,
            RolePermission.scope,
            RolePermission.created_at,
        ).where(RolePermission.role == role_name, RolePermission.action.is_(None))
    ).all()
    by_res: dict[str, tuple[str, str, str, datetime | None]] = {
        r: (v, e, sc, ca) for r, v, e, sc, ca in rows
    }
    own_grants = load_role_grant_keys(db, role_name)
    full_grants = load_effective_grant_keys_for_role(db, role_name)
    parent_by_key = load_resource_parent_map(db)
    ordered = _catalog_resources_ordered(db)
    # The tier a holder of this role would actually resolve on each key — one pass over the whole
    # catalog (Issue #165's bulk resolver), so the grid's "effective" column answers both axes of
    # the grant from the same rows the runtime gate reads (Issue #173).
    effective_scopes = scope_tiers_for_roles(
        db, [role_name], [key for key, _parent in ordered]
    )
    # Issue #176 (M29): which of these grants anything has actually exercised. One read, rolled up
    # over the resource subtree — a grant on a parent is what authorizes its children, so a hit on
    # a child marks the parent load-bearing too.
    usage = usage_for_role(db, role_name)
    perms: list[RolePermissionRowOut] = []
    for resource_key, parent_key in ordered:
        stored = by_res.get(resource_key)
        max_verb = stored[0] if stored else None
        effect = stored[1] if stored else PermissionEffect.ALLOW.value
        scope = stored[2] if stored else None
        created_at = stored[3] if stored else None
        effective_own = effective_verb_over_keys(
            own_grants, parent_by_key, resource_key
        )
        effective_full = effective_verb_over_keys(
            full_grants, parent_by_key, resource_key
        )
        grant_usage = usage.get(resource_key)
        perms.append(
            RolePermissionRowOut(
                resource=resource_key,
                max_verb=max_verb,
                effect=effect or PermissionEffect.ALLOW.value,
                scope=scope,
                effective_max_verb=effective_full,
                effective_scope=effective_scopes[resource_key].value,
                effective_inherited=(max_verb is None and effective_full is not None),
                # An inherited role changed the outcome the role's own grants would give.
                effective_via_role=(effective_full != effective_own),
                parent_resource=parent_key,
                created_at=created_at,
                last_used_at=grant_usage.last_used_at if grant_usage else None,
                hit_count=grant_usage.hit_count if grant_usage else 0,
            )
        )
    settings = get_settings()
    return RolePermissionsGetOut(
        role=role_name,
        permissions=perms,
        usage_enabled=settings.permission_usage_enabled,
        usage_since=collection_started_at(db),
        usage_unused_days=settings.permission_usage_unused_days,
    )


@router.get("/roles", response_model=RbacRoleListOut)
async def list_roles(
    _rbac: RbacReadDep,
    db: DbSession,
    q: Annotated[
        str | None,
        Query(description="Substring filter on role name or description."),
    ] = None,
    system_filter: Annotated[
        str | None,
        Query(
            description="Omit or `all` for every role; `system` for built-in only; "
            "`custom` for non-system only."
        ),
    ] = None,
    sort: Annotated[
        str | None,
        Query(description="Sort column: name, users or grants (unknown = default)."),
    ] = None,
    order: Annotated[
        str | None, Query(description="Sort direction: asc (default) or desc.")
    ] = None,
    offset: Annotated[int, Query(ge=0, description="Row offset for pagination.")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Max rows per page.")] = 20,
) -> RbacRoleListOut:
    """List roles with user and permission counts (paginated, sortable)."""
    filters: list[Any] = []
    if q and q.strip():
        term = f"%{q.strip()}%"
        filters.append(or_(RbacRole.name.ilike(term), RbacRole.description.ilike(term)))
    sf = (system_filter or "all").strip().lower()
    if sf == "system":
        filters.append(RbacRole.is_system.is_(True))
    elif sf == "custom":
        filters.append(RbacRole.is_system.is_(False))

    # Correlated counts computed in one pass — both feed the response and let the list sort by
    # them without an N+1 per-row COUNT.
    user_count_col = (
        select(func.count())
        .select_from(User)
        .where(User.role == RbacRole.name)
        .scalar_subquery()
    )
    permission_count_col = (
        select(func.count())
        .select_from(RolePermission)
        .where(RolePermission.role == RbacRole.name)
        .scalar_subquery()
    )
    sort_columns: dict[str, Any] = {
        "name": RbacRole.name,
        "users": user_count_col,
        "grants": permission_count_col,
    }

    count_stmt = select(func.count()).select_from(RbacRole)
    stmt = select(RbacRole, user_count_col, permission_count_col)
    if filters:
        where_clause = and_(*filters)
        count_stmt = count_stmt.where(where_clause)
        stmt = stmt.where(where_clause)

    total = int(db.execute(count_stmt).scalar_one())
    # Name is unique, so it is the stable tiebreaker for ties on the users/grants counts.
    order_by = _sort_clause(sort, order, sort_columns, "name")
    rows = db.execute(
        stmt.order_by(order_by, RbacRole.name).offset(offset).limit(limit)
    ).all()
    return RbacRoleListOut(
        items=[
            _role_row_out(role, int(user_count), int(permission_count))
            for role, user_count, permission_count in rows
        ],
        total=total,
    )


@router.post("/roles", response_model=RbacRoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(
    _rbac: RbacCreateDep,
    db: DbSession,
    body: RbacRoleCreateIn,
) -> RbacRoleOut:
    """Create a new (non-system) role."""
    name = body.name.strip().lower()
    if not _ROLE_NAME_SLUG.match(name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Role name must be 1-50 chars: lowercase letters, digits, "
            "underscore; start with a letter.",
        )
    exists = db.execute(
        select(RbacRole).where(RbacRole.name == name)
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Role already exists.",
        )
    row = RbacRole(name=name, description=body.description, is_system=False)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _role_out(db, row)


@router.get("/roles/{role_name}", response_model=RbacRoleOut)
async def get_role(
    _rbac: RbacReadDep,
    db: DbSession,
    role_name: str,
) -> RbacRoleOut:
    """Return one role by name (for the admin detail panel)."""
    return _role_out(db, _load_role(db, role_name))


@router.patch("/roles/{role_name}", response_model=RbacRoleOut)
async def patch_role(
    _rbac: RbacUpdateDep,
    db: DbSession,
    role_name: str,
    body: RbacRolePatchIn,
) -> RbacRoleOut:
    """Update role metadata (description)."""
    row = _load_role(db, role_name)
    raw = body.model_dump(exclude_unset=True)
    if "description" in raw:
        row.description = body.description
    db.commit()
    db.refresh(row)
    return _role_out(db, row)


@router.delete("/roles/{role_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    _rbac: RbacDeleteDep,
    db: DbSession,
    role_name: str,
) -> None:
    """Delete a role. Blocked for system roles or roles still assigned to users."""
    row = _load_role(db, role_name)
    if row.is_system:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete a system role.",
        )
    if _role_assignment_count(db, role_name) > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete a role that is still assigned to users.",
        )
    db.execute(delete(RolePermission).where(RolePermission.role == role_name))
    # Issue #176: and the role's usage rows, which describe grants that no longer exist.
    revoke_role_usage(db, role_name)
    # Remove every inheritance edge mentioning this role (both directions) so no dangling edge
    # survives — explicit rather than relying on the DB-level FK cascade, which SQLite tests skip.
    db.execute(
        delete(RoleHierarchy).where(
            or_(
                RoleHierarchy.role == role_name,
                RoleHierarchy.inherits_role == role_name,
            )
        )
    )
    db.delete(row)
    db.commit()
    return


@router.post("/roles/bulk-delete", response_model=BulkDeleteOut)
async def bulk_delete_roles(
    _rbac: RbacDeleteDep,
    db: DbSession,
    body: BulkDeleteIn,
) -> BulkDeleteOut:
    """Delete several custom roles in one call, honouring per-row rules (Issue #116).

    The ``RbacDeleteDep`` guard re-checks the caller's DELETE grant on the ``rbac`` resource for
    this request; each role name is then applied under the same rules as the single delete — a
    system role or a role still assigned to any user is skipped with a reason — and its grants are
    removed alongside it. Every id gets a result row so the UI can report partial success.
    """
    results: list[BulkDeleteResultRow] = []
    for role_name in dict.fromkeys(body.ids):  # de-dupe, preserve request order
        row = db.execute(
            select(RbacRole).where(RbacRole.name == role_name)
        ).scalar_one_or_none()
        if row is None:
            results.append(
                BulkDeleteResultRow(
                    id=role_name, deleted=False, detail="Role not found."
                )
            )
            continue
        if row.is_system:
            results.append(
                BulkDeleteResultRow(
                    id=role_name, deleted=False, detail="Cannot delete a system role."
                )
            )
            continue
        assigned = _role_assignment_count(db, role_name)
        if assigned > 0:
            results.append(
                BulkDeleteResultRow(
                    id=role_name,
                    deleted=False,
                    detail="Cannot delete a role still assigned to users.",
                )
            )
            continue
        db.execute(delete(RolePermission).where(RolePermission.role == role_name))
        revoke_role_usage(db, role_name)
        db.execute(
            delete(RoleHierarchy).where(
                or_(
                    RoleHierarchy.role == role_name,
                    RoleHierarchy.inherits_role == role_name,
                )
            )
        )
        db.delete(row)
        db.flush()
        results.append(BulkDeleteResultRow(id=role_name, deleted=True))
    db.commit()
    return _bulk_delete_summary(results)


@router.get("/roles/{role_name}/permissions", response_model=RolePermissionsGetOut)
async def get_role_permissions(
    _rbac: RbacReadDep,
    db: DbSession,
    role_name: str,
) -> RolePermissionsGetOut:
    """Return the permission matrix for a role (one row per resource; missing = null)."""
    _load_role(db, role_name)
    return _role_permissions_matrix_out(db, role_name)


@router.put("/roles/{role_name}/permissions", response_model=RolePermissionsGetOut)
async def put_role_permissions(
    _rbac: RbacUpdateDep,
    db: DbSession,
    current_user: CurrentUser,
    role_name: str,
    body: RolePermissionsPutIn,
) -> RolePermissionsGetOut:
    """Replace the permission matrix for a role (upsert; ``null`` verb revokes).

    Unknown resource keys and invalid verb/effect/scope values are rejected with 422, naming the
    offender — no field of a cell payload is silently discarded (Issue #173). Rows keep their
    original ``created_at`` when only the verb changes. Each cell that actually changes writes one
    ``permission_audit_log`` row (GRANT for a set/change, REVOKE for a removal) in the same
    transaction (Issue #138), and a **tier-only** change counts as a change.

    A cell that does not mention ``scope`` **preserves** the tier already stored — it never resets
    it to the default. That is the interaction with the policy document (Issue #161): the two
    authoring paths write the same rows, so a grid edit after a JSON-authored grant must not quietly
    undo its breadth. A cell being *created* with no tier gets
    :func:`~src.core.rbac.default_grant_scope` — the narrowest one (Issue #172).
    """
    _load_role(db, role_name)
    valid_resources = _catalog_resource_keys(db)
    for key in body.permissions:
        if key not in valid_resources:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown resource: {key!r}",
            )

    # Only cumulative (verb) rows are the matrix grid's concern; named-action grants
    # (``action`` set — Issue #140) are managed elsewhere and must never be touched here.
    existing_rows = (
        db.execute(
            select(RolePermission).where(
                RolePermission.role == role_name,
                RolePermission.action.is_(None),
            )
        )
        .scalars()
        .all()
    )
    by_res: dict[str, RolePermission] = {rp.resource: rp for rp in existing_rows}

    # Iterate the submitted cells (not the enum), so a runtime-added catalog resource is grantable.
    default_scope = default_grant_scope(role_name)
    for resource_key in body.permissions:
        verb, effect, scope = _cell_values(body.permissions.get(resource_key))
        existing = by_res.get(resource_key)
        target_id = f"{role_name}:{resource_key}"
        before = (
            {
                "max_verb": existing.max_verb,
                "effect": existing.effect,
                "scope": existing.scope,
            }
            if existing is not None
            else None
        )
        if verb is None:
            if existing is not None:
                _record_audit(
                    db,
                    current_user,
                    action=PermissionAuditAction.REVOKE,
                    target_type=PermissionAuditTargetType.ROLE_PERMISSION,
                    target_id=target_id,
                    before=before,
                    after=None,
                )
                db.delete(existing)
                # Issue #176: the usage row goes with the grant. Retention needs no sweep (the
                # table is bounded by the grant count), but a row that outlives its grant is worse
                # than untidy — re-granting the same cell later would inherit a "last used" from a
                # grant that no longer exists, which is exactly the misleading evidence this table
                # exists to replace.
                revoke_usage(db, role_name, resource_key)
            continue
        # Omitted tier: preserve the row's own, or start a new row at the narrowest one.
        resolved_scope = (
            scope.value
            if scope is not None
            else (existing.scope if existing is not None else default_scope.value)
        )
        after = {
            "max_verb": verb.value,
            "effect": effect.value,
            "scope": resolved_scope,
        }
        if existing is not None:
            if before == after:
                continue  # no change — not a grant/revoke, so nothing to audit
            existing.max_verb = verb.value
            existing.effect = effect.value
            existing.scope = resolved_scope
        else:
            db.add(
                RolePermission(
                    id=str(uuid4()),
                    role=role_name,
                    resource=resource_key,
                    max_verb=verb.value,
                    effect=effect.value,
                    scope=resolved_scope,
                    created_at=datetime.now(APP_TIMEZONE),
                )
            )
        _record_audit(
            db,
            current_user,
            action=PermissionAuditAction.GRANT,
            target_type=PermissionAuditTargetType.ROLE_PERMISSION,
            target_id=target_id,
            before=before,
            after=after,
        )
    db.commit()
    return _role_permissions_matrix_out(db, role_name)


# --------------------------------------------------------------------------------------
# JSON policy documents (Issue #161, M28) — the same rows, authored as one document
# --------------------------------------------------------------------------------------
#
# ``role_permission`` has always been one decomposed policy statement per row — resource, effect,
# verb-or-action, and (since Issue #156) a ``scope`` condition. These two endpoints expose exactly
# that as JSON so a role can be authored, reviewed, backed up and copied between environments in
# one piece, instead of one matrix cell at a time. **No second source of truth**: ``GET`` is a
# projection of the rows and ``PUT`` diffs a document against them by natural key, applying only
# what differs and auditing each change exactly as a matrix-UI edit already does.


def _statement_key(statement: PolicyStatement) -> tuple[str, str | None]:
    """Natural key of a statement: ``(resource, action)`` — ``None`` action = the cumulative row.

    The same key the ``role_permission`` partial unique indexes enforce (one cumulative row per
    ``(role, resource)``, one named-action row per ``(role, resource, action)``), and the same
    diff-by-natural-key idiom ``rbac_manifest_sync.sync_module_manifest`` already established for
    resources — so a re-imported document updates rows rather than churning them.
    """
    return (statement.resource, statement.action)


def _row_key(row: RolePermission) -> tuple[str, str | None]:
    """Natural key of a stored grant, matching :func:`_statement_key`."""
    return (row.resource, row.action)


def _assigned_instances(db: Session, role_name: str) -> list[str]:
    """Return the property ids an ``assigned``-tier grant on ``role_name`` currently resolves to.

    The read-only half of the Issue #171 instance-breadth decision
    (``docs/architecture/rbac-iam-parity.md`` §4.6, option 2): the tier is in the statement, the
    instances are on the principal, and the export renders the resolution so a policy document is
    legible on its own. Union across every holder of the role — a document describes the *role*,
    not one user — sorted for a stable diff. Expiry is deliberately not filtered here: this is a
    "what does this tier currently name" summary, never an authorization input (that is
    :func:`~src.core.scope.assignment_instance_ids`, which resolves per caller and does).
    """
    rows = db.execute(
        select(UserRoleAssignment.scope_id).where(
            UserRoleAssignment.role == role_name,
            UserRoleAssignment.scope_type == ASSIGNMENT_SCOPE_TYPE,
            UserRoleAssignment.scope_id.is_not(None),
        )
    ).scalars()
    return sorted({str(scope_id) for scope_id in rows})


def _statement_from_row(
    row: RolePermission, *, assigned_instances: list[str]
) -> PolicyStatement:
    """Render one stored grant as a policy statement.

    ``scope`` is **always** emitted (Issue #172). It used to be omitted when it matched the role's
    seed default, for terseness — safe while the omission and the import default agreed, and unsafe
    the moment they stopped: since #172 an omitted ``scope`` on import means ``own``, so a terse
    export of an ``admin`` document applied to a fresh environment would silently narrow every
    grant it describes. A document that states its own breadth is copy-safe by construction, which
    is the use case ``/policy`` exists for.

    ``scope_instances`` is emitted only for an ``assigned`` grant, whose breadth is otherwise
    invisible in the document (Issue #171).
    """
    return PolicyStatement(
        effect=row.effect or PermissionEffect.ALLOW.value,
        resource=row.resource,
        verb=row.max_verb,
        action=row.action,
        scope=row.scope,
        scope_instances=(
            assigned_instances if row.scope == GrantScope.ASSIGNED.value else None
        ),
        applies_to_descendants=bool(row.applies_to_descendants),
    )


def _policy_document(db: Session, role_name: str) -> RolePolicyDocument:
    """Render every grant a role holds **directly** as a policy document.

    Inherited grants (Issue #135) are deliberately not flattened in: the document describes what
    this role *declares*, so re-importing it cannot silently materialise a parent's grants as the
    child's own.
    """
    rows = (
        db.execute(
            select(RolePermission)
            .where(RolePermission.role == role_name)
            .order_by(RolePermission.resource, RolePermission.action)
        )
        .scalars()
        .all()
    )
    # Resolved once for the whole document, and only when some row actually carries the tier.
    assigned_instances = (
        _assigned_instances(db, role_name)
        if any(row.scope == GrantScope.ASSIGNED.value for row in rows)
        else []
    )
    return RolePolicyDocument(
        role=role_name,
        statements=[
            _statement_from_row(row, assigned_instances=assigned_instances)
            for row in rows
        ],
    )


def _validate_statement(
    db: Session, statement: PolicyStatement, *, valid_resources: set[str]
) -> None:
    """Reject a statement the live catalog cannot express, with a message naming the offender."""
    if statement.resource not in valid_resources:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown resource: {statement.resource!r}",
        )
    if (statement.verb is None) == (statement.action is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Statement for {statement.resource!r} must set exactly one of "
                "'verb' or 'action'."
            ),
        )
    if statement.verb is not None and statement.verb not in {
        verb.value for verb in PermissionVerb
    }:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown verb: {statement.verb!r}",
        )
    if statement.action is not None and statement.action not in _catalog_action_keys(
        db
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown action: {statement.action!r}",
        )
    if statement.effect not in {effect.value for effect in PermissionEffect}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown effect: {statement.effect!r}",
        )
    if statement.scope is not None and statement.scope not in {
        scope.value for scope in GrantScope
    }:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown scope: {statement.scope!r}",
        )


def _catalog_action_keys(db: Session) -> set[str]:
    """Valid action keys from the DB catalog, falling back to the manifests + CRUD verbs.

    Mirrors :func:`_catalog_resource_keys`' fallback: before the catalog is seeded (a fresh dev DB,
    a test that seeds only roles) the manifest-declared named actions plus the cumulative verbs
    stand in, so validation is never accidentally permissive *or* impossible.
    """
    keys = set(db.execute(select(Action.key)).scalars().all())
    if keys:
        return keys
    manifest_actions = {
        action
        for manifest in ALL_MANIFESTS
        for node in iter_resources(manifest)
        for action in node.named_actions
    }
    return manifest_actions | {verb.value for verb in PermissionVerb}


def _apply_statement(
    db: Session,
    current_user: dict[str, Any],
    *,
    role_name: str,
    statement: PolicyStatement,
    existing: RolePermission | None,
    default_scope: GrantScope,
) -> str:
    """Create or update one grant from a statement; return ``created``/``updated``/``unchanged``.

    Audited identically to a matrix-UI edit (``PermissionAuditLog``, Issue #138) — same target type,
    same ``before``/``after`` shape — so the trail does not record *how* an operator edited a grant,
    only what changed. A tier-only change (same verb, same effect) is a real change here and writes
    its own audit row naming the before and after tier — widening a grant's breadth is the single
    most consequential edit this console offers (Issue #172).

    An omitted ``scope`` **preserves** the stored tier on an existing row and lands
    ``default_grant_scope`` — the narrowest rung, ``own`` — on a new one. Never the widest: a
    statement that says nothing about breadth is not a request for the whole business.
    """
    scope = statement.scope or (
        existing.scope if existing is not None else default_scope.value
    )
    after = {
        "max_verb": statement.verb,
        "action": statement.action,
        "effect": statement.effect,
        "scope": scope,
        "applies_to_descendants": statement.applies_to_descendants,
    }
    before = (
        {
            "max_verb": existing.max_verb,
            "action": existing.action,
            "effect": existing.effect,
            "scope": existing.scope,
            "applies_to_descendants": existing.applies_to_descendants,
        }
        if existing is not None
        else None
    )
    if before == after:
        return "unchanged"

    if existing is None:
        db.add(
            RolePermission(
                id=str(uuid4()),
                role=role_name,
                resource=statement.resource,
                max_verb=statement.verb,
                action=statement.action,
                effect=statement.effect,
                scope=scope,
                applies_to_descendants=statement.applies_to_descendants,
                created_at=datetime.now(APP_TIMEZONE),
            )
        )
        outcome = "created"
    else:
        existing.max_verb = statement.verb
        existing.effect = statement.effect
        existing.scope = scope
        existing.applies_to_descendants = statement.applies_to_descendants
        outcome = "updated"

    _record_audit(
        db,
        current_user,
        action=PermissionAuditAction.GRANT,
        target_type=PermissionAuditTargetType.ROLE_PERMISSION,
        target_id=f"{role_name}:{statement.resource}"
        + (f":{statement.action}" if statement.action else ""),
        before=before,
        after=after,
    )
    return outcome


@router.get("/roles/{role_name}/policy", response_model=RolePolicyDocument)
async def get_role_policy(
    _rbac: RbacReadDep,
    db: DbSession,
    role_name: str,
) -> RolePolicyDocument:
    """Render a role's grants as a JSON policy document (Issue #161, M28).

    A read-only projection of the role's own ``role_permission`` rows — cumulative verbs and named
    actions alike, with any non-default ``scope`` spelled out. Doubles as the export half of
    "back up a role" / "copy this role to staging": the document this returns is exactly what
    ``PUT`` accepts, and re-importing it unchanged is a no-op.
    """
    _load_role(db, role_name)
    return _policy_document(db, role_name)


@router.put("/roles/{role_name}/policy", response_model=RolePolicyPutResult)
async def put_role_policy(
    _rbac: RbacUpdateDep,
    db: DbSession,
    current_user: CurrentUser,
    role_name: str,
    body: RolePolicyDocument,
) -> RolePolicyPutResult:
    """Apply a full policy document to a role: create, update and revoke exactly what differs.

    The document is **authoritative for the whole role**: a grant it omits is revoked, which is what
    makes an edited export behave the way an operator expects. Every statement is validated against
    the live DB catalog first (unknown resource/action/verb/effect/scope → 422, naming the
    offender), so a typo can never create an orphaned row. Nothing is written unless every statement
    validates; each real change is audited exactly as the matrix UI's own edits are (Issue #138), in
    the same transaction.

    A document whose ``role`` disagrees with the URL is rejected rather than silently retargeted.
    """
    _load_role(db, role_name)
    if body.role and body.role != role_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Document role {body.role!r} does not match {role_name!r}.",
        )
    valid_resources = _catalog_resource_keys(db)
    for statement in body.statements:
        _validate_statement(db, statement, valid_resources=valid_resources)

    seen: set[tuple[str, str | None]] = set()
    for statement in body.statements:
        key = _statement_key(statement)
        if key in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Duplicate statement for {statement.resource!r}"
                    + (f" action {statement.action!r}" if statement.action else "")
                ),
            )
        seen.add(key)

    existing_rows = (
        db.execute(select(RolePermission).where(RolePermission.role == role_name))
        .scalars()
        .all()
    )
    by_key = {_row_key(row): row for row in existing_rows}
    default_scope = default_grant_scope(role_name)

    counts = {"created": 0, "updated": 0, "unchanged": 0, "deleted": 0}
    for statement in body.statements:
        outcome = _apply_statement(
            db,
            current_user,
            role_name=role_name,
            statement=statement,
            existing=by_key.get(_statement_key(statement)),
            default_scope=default_scope,
        )
        counts[outcome] += 1

    for key, row in by_key.items():
        if key in seen:
            continue
        _record_audit(
            db,
            current_user,
            action=PermissionAuditAction.REVOKE,
            target_type=PermissionAuditTargetType.ROLE_PERMISSION,
            target_id=f"{role_name}:{row.resource}"
            + (f":{row.action}" if row.action else ""),
            before={
                "max_verb": row.max_verb,
                "action": row.action,
                "effect": row.effect,
                "scope": row.scope,
                "applies_to_descendants": row.applies_to_descendants,
            },
            after=None,
        )
        db.delete(row)
        # Issue #176: same rule on the policy-document path, so a revoke made by re-importing a
        # document cleans up exactly as a matrix edit does.
        revoke_usage(db, role_name, row.resource)
        counts["deleted"] += 1

    db.commit()
    return RolePolicyPutResult(
        created=counts["created"],
        updated=counts["updated"],
        deleted=counts["deleted"],
        unchanged=counts["unchanged"],
        document=_policy_document(db, role_name),
    )


# --------------------------------------------------------------------------------------
# Role inheritance (Issue #135) — the role_hierarchy DAG, gated on the ``rbac`` resource
# --------------------------------------------------------------------------------------


def _inherited_roles(db: Session, role_name: str) -> list[str]:
    """Return the role names ``role_name`` inherits from directly (one hop), sorted."""
    rows = (
        db.execute(
            select(RoleHierarchy.inherits_role).where(RoleHierarchy.role == role_name)
        )
        .scalars()
        .all()
    )
    return sorted(rows)


@router.get("/roles/{role_name}/inherits", response_model=RoleInheritanceGetOut)
async def get_role_inheritance(
    _rbac: RbacReadDep,
    db: DbSession,
    role_name: str,
) -> RoleInheritanceGetOut:
    """Return the roles ``role_name`` inherits from directly (its ``role_hierarchy`` parents)."""
    _load_role(db, role_name)
    return RoleInheritanceGetOut(
        role=role_name, inherits=_inherited_roles(db, role_name)
    )


@router.post("/roles/{role_name}/inherits", response_model=RoleInheritanceGetOut)
async def add_role_inheritance(
    _rbac: RbacUpdateDep,
    db: DbSession,
    current_user: CurrentUser,
    role_name: str,
    body: RoleInheritanceAddIn,
) -> RoleInheritanceGetOut:
    """Make ``role_name`` inherit ``inherits_role``.

    Rejects an unknown target role (``400``), a self-edge or any edge that would close an
    inheritance cycle (``400``). Adding an edge that already exists is a no-op; a newly added edge
    writes one GRANT audit row (Issue #138). The change takes effect on the next
    effective-permission resolution.
    """
    inherits_role = body.inherits_role.strip().lower()
    _load_role(db, role_name)
    target = db.execute(
        select(RbacRole).where(RbacRole.name == inherits_role)
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown role to inherit.",
        )
    if role_inherits_cycle(db, role_name, inherits_role):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That inheritance would create a cycle.",
        )
    exists = db.execute(
        select(RoleHierarchy).where(
            RoleHierarchy.role == role_name,
            RoleHierarchy.inherits_role == inherits_role,
        )
    ).scalar_one_or_none()
    if exists is None:
        db.add(RoleHierarchy(role=role_name, inherits_role=inherits_role))
        _record_audit(
            db,
            current_user,
            action=PermissionAuditAction.GRANT,
            target_type=PermissionAuditTargetType.ROLE_HIERARCHY,
            target_id=f"{role_name}->{inherits_role}",
            before=None,
            after={"role": role_name, "inherits_role": inherits_role},
        )
        db.commit()
    return RoleInheritanceGetOut(
        role=role_name, inherits=_inherited_roles(db, role_name)
    )


@router.delete(
    "/roles/{role_name}/inherits/{inherits_role}",
    response_model=RoleInheritanceGetOut,
)
async def remove_role_inheritance(
    _rbac: RbacUpdateDep,
    db: DbSession,
    current_user: CurrentUser,
    role_name: str,
    inherits_role: str,
) -> RoleInheritanceGetOut:
    """Remove the ``role_name -> inherits_role`` inheritance edge (idempotent).

    A removal that actually deletes an edge writes one REVOKE audit row (Issue #138).
    """
    _load_role(db, role_name)
    inherits_role = inherits_role.strip().lower()
    existed = db.execute(
        select(RoleHierarchy).where(
            RoleHierarchy.role == role_name,
            RoleHierarchy.inherits_role == inherits_role,
        )
    ).scalar_one_or_none()
    if existed is not None:
        db.execute(
            delete(RoleHierarchy).where(
                RoleHierarchy.role == role_name,
                RoleHierarchy.inherits_role == inherits_role,
            )
        )
        _record_audit(
            db,
            current_user,
            action=PermissionAuditAction.REVOKE,
            target_type=PermissionAuditTargetType.ROLE_HIERARCHY,
            target_id=f"{role_name}->{inherits_role}",
            before={"role": role_name, "inherits_role": inherits_role},
            after=None,
        )
    db.commit()
    return RoleInheritanceGetOut(
        role=role_name, inherits=_inherited_roles(db, role_name)
    )


# --------------------------------------------------------------------------------------
# Role assignment (Issue #6 + #136) — the ``user_roles`` table, gated on the ``rbac`` resource
# --------------------------------------------------------------------------------------


def _acting_user_id(db: Session, current_user: dict[str, Any]) -> str | None:
    """Resolve the acting principal's user id from the token claims (for ``granted_by``)."""
    email = (current_user.get("email") or current_user.get("sub") or "").strip().lower()
    if not email or email == "anonymous":
        return None
    row = db.execute(select(User.id).where(User.email == email)).scalar_one_or_none()
    return str(row) if row is not None else None


def _actor_label(current_user: dict[str, Any]) -> str:
    """Return the durable human identity for the audit trail ('system' when not a user)."""
    email = (current_user.get("email") or current_user.get("sub") or "").strip().lower()
    return email or "system"


def _record_audit(
    db: Session,
    current_user: dict[str, Any],
    *,
    action: PermissionAuditAction,
    target_type: PermissionAuditTargetType,
    target_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append one ``permission_audit_log`` row for an RBAC-admin change (Issue #138).

    Adds the row to the session; the caller commits, so the audit row lands in the **same
    transaction** as the change it describes (a rollback leaves no orphan row).
    """
    db.add(
        PermissionAuditLog(
            id=str(uuid4()),
            actor=_actor_label(current_user),
            actor_id=_acting_user_id(db, current_user),
            action=action.value,
            target_type=target_type.value,
            target_id=target_id,
            before=before,
            after=after,
        )
    )


def _sync_unscoped_assignment(
    db: Session, user: User, role_name: str, granted_by: str | None
) -> None:
    """Make ``role_name`` the user's single unscoped assignment and mirror it onto ``User.role``.

    Deletes any existing unscoped rows and inserts one, so the "set role" path stays a single
    unscoped assignment (Issue #136). ``User.role`` is kept in sync as the compat mirror.
    """
    db.execute(
        delete(UserRoleAssignment).where(
            UserRoleAssignment.user_id == user.id,
            UserRoleAssignment.scope_type.is_(None),
        )
    )
    db.add(
        UserRoleAssignment(
            id=str(uuid4()),
            user_id=str(user.id),
            role=role_name,
            granted_by=granted_by,
            granted_at=datetime.now(APP_TIMEZONE),
        )
    )
    user.role = role_name


def _assignment_out(row: UserRoleAssignment, now: datetime) -> UserRoleAssignmentOut:
    """Build an assignment response, computing whether it is currently active."""
    expires_at = row.expires_at
    # SQLite returns naive datetimes (no tz preserved); assume the business timezone so the
    # comparison against a tz-aware ``now`` never raises. Postgres returns tz-aware values.
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=now.tzinfo)
    active = expires_at is None or expires_at > now
    # Issue #175: ``active`` alone cannot say *why* — and "granted, expired 3 days ago" is the
    # answer to most support tickets in this area, while a scoped assignment is active yet
    # conditional. Both facts are composed once, here, in operator language.
    status, status_label = assignment_status(
        active=active,
        expires_at=expires_at,
        scope_type=row.scope_type,
        scope_id=row.scope_id,
        now=now,
    )
    return UserRoleAssignmentOut(
        id=str(row.id),
        role=row.role,
        scope_type=row.scope_type,
        scope_id=row.scope_id,
        granted_by=row.granted_by,
        granted_at=row.granted_at,
        expires_at=row.expires_at,
        active=active,
        status=status,
        status_label=status_label,
    )


@router.get("/users/{user_id}/roles", response_model=UserRolesGetOut)
async def get_user_roles(
    _rbac: RbacReadDep,
    db: DbSession,
    user_id: str,
) -> UserRolesGetOut:
    """Return the user's currently assigned (unscoped, compat) role."""
    user = _load_active_user(db, user_id)
    return UserRolesGetOut(
        user_id=str(user.id),
        role=user.role or UserRole.USER.value,
    )


@router.put("/users/{user_id}/roles", response_model=UserRolesGetOut)
async def put_user_roles(
    _rbac: RbacUpdateDep,
    db: DbSession,
    current_user: CurrentUser,
    user_id: str,
    body: UserRolesPutIn,
) -> UserRolesGetOut:
    """Set a user's unscoped role (replaces the unscoped assignment; scoped ones are untouched).

    Keeps the simple "set role" flow working: it replaces the user's single unscoped assignment
    and mirrors it onto ``User.role``. Takes effect on their next ``/auth/me``.
    """
    role_name = body.role.strip()
    role_row = db.execute(
        select(RbacRole).where(RbacRole.name == role_name)
    ).scalar_one_or_none()
    if role_row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown role.",
        )
    user = _load_active_user(db, user_id)
    prev = user.role or UserRole.USER.value
    if (
        _is_rbac_administrator(db, user)
        and role_name not in _rbac_administrator_roles(db)
        and _rbac_administrator_count(db) <= 1
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove the last admin user.",
        )
    _sync_unscoped_assignment(db, user, role_name, _acting_user_id(db, current_user))
    _record_audit(
        db,
        current_user,
        action=PermissionAuditAction.GRANT,
        target_type=PermissionAuditTargetType.USER_ROLE,
        target_id=f"{user.id}:unscoped",
        before={"role": prev},
        after={"role": role_name},
    )
    db.commit()
    db.refresh(user)
    return UserRolesGetOut(
        user_id=str(user.id),
        role=user.role or UserRole.USER.value,
    )


@router.get("/users/{user_id}/assignments", response_model=UserRoleAssignmentsOut)
async def list_user_assignments(
    _rbac: RbacReadDep,
    db: DbSession,
    user_id: str,
) -> UserRoleAssignmentsOut:
    """List all of a user's role assignments (active and expired), newest first."""
    user = _load_active_user(db, user_id)
    now = datetime.now(APP_TIMEZONE)
    rows = (
        db.execute(
            select(UserRoleAssignment)
            .where(UserRoleAssignment.user_id == user.id)
            .order_by(UserRoleAssignment.granted_at.desc())
        )
        .scalars()
        .all()
    )
    return UserRoleAssignmentsOut(
        user_id=str(user.id),
        assignments=[_assignment_out(row, now) for row in rows],
    )


@router.get("/users/{user_id}/effective-access", response_model=UserEffectiveAccessOut)
async def get_user_effective_access(
    _rbac: RbacReadDep,
    db: DbSession,
    user_id: str,
) -> UserEffectiveAccessOut:
    """What one user actually reaches — the union of their active assignments (Issue #175, M29).

    The harder and more frequently asked half of the explainer, because a user's access is the
    union of their active roles' closures filtered by scoped and time-boxed assignments
    (Issue #136), and nothing in the console rendered that union before this endpoint. "Why can't
    this person see their lease?" needed a developer.

    Resolved through the same functions a request resolves through —
    :func:`~src.core.rbac.active_roles_for_user` for the union,
    :func:`~src.core.rbac.effective_verb_over_keys` for the verb and
    :func:`~src.core.scope.scope_tiers_for_roles` for the tier — so this panel cannot drift from
    what a request would decide. ``contributing_roles`` re-runs the verb resolver **per role** to
    answer the question the union alone cannot: *which* of their roles gets them there.

    Every assignment is returned, active or not. An expired or scoped one is reported as inactive
    rather than omitted: a row that has silently vanished from the page looks exactly like one that
    was never granted, which is the opposite of the answer a support ticket needs.
    """
    user = _load_active_user(db, user_id)
    now = datetime.now(APP_TIMEZONE)
    assignment_rows = (
        db.execute(
            select(UserRoleAssignment)
            .where(UserRoleAssignment.user_id == user.id)
            .order_by(UserRoleAssignment.granted_at.desc())
        )
        .scalars()
        .all()
    )
    roles = sorted(active_roles_for_user(db, user))
    parent_by_key = load_resource_parent_map(db)
    keys = sorted(parent_by_key)
    union_grants = load_effective_grant_keys_for_roles(db, roles)
    tiers = scope_tiers_for_roles(db, roles, keys)
    # One resolve per role, reused across every key — the per-role attribution the union cannot
    # give, without a query per (role, resource) pair.
    grants_by_role = {
        role: load_effective_grant_keys_for_roles(db, [role]) for role in roles
    }
    assigned_instances = len(
        {
            row.scope_id
            for row in assignment_rows
            if row.scope_type == ASSIGNMENT_SCOPE_TYPE and row.scope_id is not None
        }
    )
    access: list[UserEffectiveAccessRowOut] = []
    for key in keys:
        verb = effective_verb_over_keys(union_grants, parent_by_key, key)
        if verb is None:
            continue
        tier = tiers[key]
        access.append(
            UserEffectiveAccessRowOut(
                resource=key,
                verb=verb,
                tier=tier.value,
                tier_label=scope_tier_label(tier),
                tier_meaning=scope_tier_meaning(
                    tier, assigned_instance_count=assigned_instances
                ),
                contributing_roles=[
                    role
                    for role in roles
                    if effective_verb_over_keys(
                        grants_by_role[role], parent_by_key, key
                    )
                    == verb
                ],
            )
        )
    return UserEffectiveAccessOut(
        user_id=str(user.id),
        email=str(user.email),
        nav_role=(user.role or "").strip(),
        active_roles=roles,
        assignments=[_assignment_out(row, now) for row in assignment_rows],
        access=access,
    )


@router.post(
    "/users/{user_id}/assignments",
    response_model=UserRoleAssignmentsOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_user_assignment(
    _rbac: RbacUpdateDep,
    db: DbSession,
    current_user: CurrentUser,
    user_id: str,
    body: UserRoleAssignmentAddIn,
) -> UserRoleAssignmentsOut:
    """Add a scoped and/or time-boxed role assignment to a user (Issue #136).

    Rejects an unknown role and a half-specified scope (one of ``scope_type``/``scope_id`` without
    the other) with ``400``; a duplicate ``(user, role, scope)`` is a no-op. Adding an unscoped
    assignment also updates the ``User.role`` mirror and is subject to the last-admin safeguard.
    Adding the *first* scoped assignment to a user who has never adopted the assignment system also
    materializes an unscoped baseline row for their current role, so their general RBAC access is
    preserved rather than silently narrowed to nothing outside the new scope (Issue #147).
    """
    role_name = body.role.strip()
    role_row = db.execute(
        select(RbacRole).where(RbacRole.name == role_name)
    ).scalar_one_or_none()
    if role_row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown role."
        )
    scope_type = (body.scope_type or "").strip() or None
    scope_id = (body.scope_id or "").strip() or None
    if (scope_type is None) != (scope_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scope_type and scope_id must be provided together.",
        )
    user = _load_active_user(db, user_id)

    if scope_type is None:
        # An unscoped assignment is the "set role" case — replace the mirror, guard last admin.
        prev = user.role or UserRole.USER.value
        if (
            _is_rbac_administrator(db, user)
            and role_name not in _rbac_administrator_roles(db)
            and _rbac_administrator_count(db) <= 1
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the last admin user.",
            )
        prev = user.role or UserRole.USER.value
        _sync_unscoped_assignment(
            db, user, role_name, _acting_user_id(db, current_user)
        )
        _record_audit(
            db,
            current_user,
            action=PermissionAuditAction.GRANT,
            target_type=PermissionAuditTargetType.USER_ROLE,
            target_id=f"{user.id}:unscoped",
            before={"role": prev},
            after={"role": role_name},
        )
    else:
        # A user who has never adopted the multi-assignment system (Issue #136) has zero
        # ``user_roles`` rows and relies entirely on the ``User.role`` mirror for their general
        # access (``active_roles_for_user``'s fallback). The *moment* they gain any assignment row
        # — even a scoped one — that fallback stops applying (it only fires when zero rows exist at
        # all), so adding *only* a scoped row here would silently strip their general RBAC access
        # everywhere the new scope doesn't apply. Materialize an unscoped baseline row for their
        # current role first, so a scope narrows what they see (Issue #147's whole point) without
        # ever narrowing what they may generally still *do*.
        if not db.execute(
            select(UserRoleAssignment.id)
            .where(UserRoleAssignment.user_id == user.id)
            .limit(1)
        ).scalar_one_or_none():
            db.add(
                UserRoleAssignment(
                    id=str(uuid4()),
                    user_id=str(user.id),
                    role=user.role or UserRole.USER.value,
                    granted_by=_acting_user_id(db, current_user),
                    granted_at=datetime.now(APP_TIMEZONE),
                )
            )
        existing = db.execute(
            select(UserRoleAssignment).where(
                UserRoleAssignment.user_id == user.id,
                UserRoleAssignment.role == role_name,
                UserRoleAssignment.scope_type == scope_type,
                UserRoleAssignment.scope_id == scope_id,
            )
        ).scalar_one_or_none()
        after = {
            "role": role_name,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "expires_at": body.expires_at.isoformat() if body.expires_at else None,
        }
        if existing is None:
            db.add(
                UserRoleAssignment(
                    id=str(uuid4()),
                    user_id=str(user.id),
                    role=role_name,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    granted_by=_acting_user_id(db, current_user),
                    granted_at=datetime.now(APP_TIMEZONE),
                    expires_at=body.expires_at,
                )
            )
            _record_audit(
                db,
                current_user,
                action=PermissionAuditAction.GRANT,
                target_type=PermissionAuditTargetType.USER_ROLE,
                target_id=f"{user.id}:{role_name}:{scope_type}:{scope_id}",
                before=None,
                after=after,
            )
        elif body.expires_at is not None or existing.expires_at is not None:
            existing.expires_at = body.expires_at
            _record_audit(
                db,
                current_user,
                action=PermissionAuditAction.GRANT,
                target_type=PermissionAuditTargetType.USER_ROLE,
                target_id=f"{user.id}:{role_name}:{scope_type}:{scope_id}",
                before=None,
                after=after,
            )
    db.commit()
    return await list_user_assignments(_rbac, db, user_id)


@router.delete(
    "/users/{user_id}/assignments/{assignment_id}",
    response_model=UserRoleAssignmentsOut,
)
async def remove_user_assignment(
    _rbac: RbacUpdateDep,
    db: DbSession,
    current_user: CurrentUser,
    user_id: str,
    assignment_id: str,
) -> UserRoleAssignmentsOut:
    """Remove one role assignment (writes a REVOKE audit row — Issue #138).

    Removing a user's **unscoped** assignment would strip their global role, so it is guarded by
    the last-admin safeguard and clears the ``User.role`` mirror to the base ``user`` role.
    """
    user = _load_active_user(db, user_id)
    row = db.execute(
        select(UserRoleAssignment).where(
            UserRoleAssignment.id == assignment_id,
            UserRoleAssignment.user_id == user.id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found."
        )
    if row.scope_type is None:
        if _is_rbac_administrator(db, user) and _rbac_administrator_count(db) <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the last admin user.",
            )
        user.role = UserRole.USER.value
    _record_audit(
        db,
        current_user,
        action=PermissionAuditAction.REVOKE,
        target_type=PermissionAuditTargetType.USER_ROLE,
        target_id=f"{user.id}:{row.role}:{row.scope_type}:{row.scope_id}",
        before={
            "role": row.role,
            "scope_type": row.scope_type,
            "scope_id": row.scope_id,
        },
        after=None,
    )
    db.delete(row)
    db.commit()
    return await list_user_assignments(_rbac, db, user_id)


# --------------------------------------------------------------------------------------
# Permission audit trail (Issue #138) — read-only, gated on the ``rbac`` resource
# --------------------------------------------------------------------------------------


def _audit_row_out(row: PermissionAuditLog) -> PermissionAuditRowOut:
    """Build the audit response from a ``permission_audit_log`` row."""
    return PermissionAuditRowOut(
        id=str(row.id),
        actor=row.actor,
        actor_id=row.actor_id,
        action=row.action,
        target_type=row.target_type,
        target_id=row.target_id,
        before=row.before,
        after=row.after,
        created_at=row.created_at,
    )


@router.get("/audit", response_model=PermissionAuditListOut)
async def list_permission_audit(
    _rbac: RbacReadDep,
    db: DbSession,
    actor: Annotated[
        str | None, Query(description="Actor substring filter (email/identifier).")
    ] = None,
    target_type: Annotated[
        str | None,
        Query(
            description="Exact target type: role_permission/role_hierarchy/user_role."
        ),
    ] = None,
    target_id: Annotated[
        str | None, Query(description="Target id substring filter.")
    ] = None,
    action: Annotated[
        str | None, Query(description="Exact action filter: grant or revoke.")
    ] = None,
    date_from: Annotated[
        datetime | None, Query(description="Only rows at/after this instant.")
    ] = None,
    date_to: Annotated[
        datetime | None, Query(description="Only rows at/before this instant.")
    ] = None,
    offset: Annotated[int, Query(ge=0, description="Row offset for pagination.")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Max rows per page.")] = 20,
) -> PermissionAuditListOut:
    """Browse the RBAC-admin audit trail, filterable by actor/target/action/date (newest first).

    Read-only and gated on the ``rbac`` resource like the rest of the console, so a portal role
    (which holds no ``rbac`` grant) cannot reach it.
    """
    filters: list[Any] = []
    if actor and actor.strip():
        filters.append(PermissionAuditLog.actor.ilike(f"%{actor.strip()}%"))
    if target_type and target_type.strip():
        filters.append(PermissionAuditLog.target_type == target_type.strip())
    if target_id and target_id.strip():
        filters.append(PermissionAuditLog.target_id.ilike(f"%{target_id.strip()}%"))
    if action and action.strip():
        filters.append(PermissionAuditLog.action == action.strip().lower())
    if date_from is not None:
        filters.append(PermissionAuditLog.created_at >= date_from)
    if date_to is not None:
        filters.append(PermissionAuditLog.created_at <= date_to)
    where_clause = and_(*filters) if filters else None

    count_stmt = select(func.count()).select_from(PermissionAuditLog)
    rows_stmt = select(PermissionAuditLog)
    if where_clause is not None:
        count_stmt = count_stmt.where(where_clause)
        rows_stmt = rows_stmt.where(where_clause)

    total = int(db.execute(count_stmt).scalar_one())
    rows = (
        db.execute(
            rows_stmt.order_by(PermissionAuditLog.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return PermissionAuditListOut(items=[_audit_row_out(r) for r in rows], total=total)


# --------------------------------------------------------------------------------------
# Decision simulator (Issue #174, M29)
#
# ``docs/architecture/rbac-decision-transparency.md`` §4. One endpoint, no writes, no new
# authorization logic: every verdict below is computed by :mod:`src.core.rbac_simulator`, which
# calls the same resolvers a live request calls. See that module's docstring for the mapping from
# question to resolver, and ``tests/integration/admin/test_rbac_simulator.py`` for the grid parity
# test that keeps the two from ever drifting.
#
# Gated on ``rbac:READ`` and nothing else — a caller who may read the permission model may ask it
# questions about itself. A caller without it is refused by the dependency, before any resolution
# runs, so the endpoint leaks nothing about a grant they cannot already read.
# --------------------------------------------------------------------------------------


def _trace_out(trace: Iterable[TraceStep]) -> list[TraceStepOut]:
    """Serialize the resolvers' :class:`~src.core.rbac.TraceStep` list for the wire."""
    return [
        TraceStepOut(
            stage=step.stage.value,
            stage_label=trace_stage_label(step.stage.value),
            detail=step.detail,
            outcome=step.outcome.value,
            resource=step.resource,
            value=step.value,
        )
        for step in trace
    ]


def _explanation_out(result: SimulationResult) -> SimulationExplanationOut:
    """Project the operator-language explanation (Issue #175) onto the response model.

    Composed here, never in the browser: the explainer pages render these strings verbatim, which
    is what keeps the tier ladder, the inheritance rules and the cascade semantics in exactly one
    implementation.
    """
    explanation = explain(result)
    return SimulationExplanationOut(
        summary=explanation.summary,
        effective_tier_label=explanation.effective_tier_label,
        effective_tier_meaning=explanation.effective_tier_meaning,
        deciding_sentence=explanation.deciding_sentence,
    )


def _simulation_out(result: SimulationResult) -> SimulateOut:
    """Project a :class:`~src.core.rbac_simulator.SimulationResult` onto the response model."""
    statement = result.deciding_statement
    instances = result.resolved_instances
    return SimulateOut(
        decision=result.decision.value,
        target_kind=result.target_kind.value,
        target_label=result.target_label,
        principal_label=result.principal_label,
        principal_roles=list(result.principal_roles),
        resource=result.resource,
        effective_verb=result.effective_verb,
        effective_tier=result.effective_tier.value,
        deciding_statement=(
            None
            if statement is None
            else DecidingStatementOut(
                role=statement.role,
                resource=statement.resource,
                effect=statement.effect,
                max_verb=statement.max_verb,
                action=statement.action,
                scope=statement.scope,
                inherited_via=statement.inherited_via,
                cascaded_from_ancestor=statement.cascaded_from_ancestor,
            )
        ),
        resolved_instances=(
            None
            if instances is None
            else ResolvedInstancesOut(
                axis=instances.axis,
                count=instances.count,
                sample=list(instances.sample),
            )
        ),
        explanation=_explanation_out(result),
        trace=_trace_out(result.trace),
    )


@router.post("/simulate", response_model=SimulateOut)
async def simulate_decision(
    _rbac: RbacReadDep,
    db: DbSession,
    payload: SimulateIn,
) -> SimulateOut:
    """Answer one RBAC question without making the request (Issue #174, M29).

    Takes a principal (a role, a user id or an email), a target (``{resource, verb}`` for "may they
    do X", ``{surface}``/``{path}`` for "can they open this page"), and optionally the instance and
    the assignment scope the question is about. Returns the verdict, the effective verb and tier,
    the grant that decided — named with the role it was written on and the inheritance path it
    arrived through — and the full decision trace.

    **Read-only and behaviour-preserving.** Nothing here decides anything: the resolvers in
    :mod:`src.core.rbac_simulator` are the ones enforcement calls, invoked with a ``trace`` list
    they would otherwise be given ``None`` for. A simulation is not written to the permission audit
    log; that log records *changes*, and this changes nothing.

    ``400`` for a principal or target that cannot be resolved (an unknown user, a path no
    destination declares) — deliberately distinct from a ``deny`` verdict, because "that role
    cannot open that page" and "nothing declares that path" are different facts and a typo must not
    read as a security finding.
    """
    try:
        result = simulate(
            db,
            SimulationPrincipal(
                role=payload.principal.role,
                user_id=payload.principal.user_id,
                email=payload.principal.email,
            ),
            SimulationTarget(
                resource=payload.target.resource,
                verb=payload.target.verb,
                surface=payload.target.surface,
                path=payload.target.path,
            ),
            instance=(
                None
                if payload.instance is None
                else SimulationInstance(
                    type=payload.instance.type, id=payload.instance.id
                )
            ),
            context=(
                None
                if payload.context is None
                else SimulationContext(
                    scope_type=payload.context.scope_type,
                    scope_id=payload.context.scope_id,
                )
            ),
        )
    except SimulationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return _simulation_out(result)


# --------------------------------------------------------------------------------------
# Dynamic catalog CRUD (Issue #141, M25) — resources / actions / permissions
#
# The catalog is now editable data (Issue #139). These endpoints let an admin manage it with no code
# change: every mutation is gated on the ``rbac`` resource (DELETE for destructive ops) and audited
# (Issue #138). System-seeded rows are protected, and a resource/action still referenced by a grant
# cannot be deleted. Adding/removing a resource rebuilds ``resource_descendants`` (the Postgres
# trigger does this online; the Python twin covers SQLite) so the closure + grid reflect it.
# --------------------------------------------------------------------------------------


def _rebuild_resource_cache(db: Session) -> None:
    """Rebuild ``resource_descendants`` after a resource-tree change (SQLite; PG uses the trigger)."""
    rebuild_resource_closure(db)


def _resource_out(db: Session, row: Resource) -> ResourceOut:
    """Build a :class:`ResourceOut`, resolving the parent key."""
    parent_key: str | None = None
    if row.parent_id is not None:
        parent_key = db.execute(
            select(Resource.key).where(Resource.id == row.parent_id)
        ).scalar_one_or_none()
    return ResourceOut(
        id=row.id,
        key=row.key,
        name=row.name,
        description=row.description,
        parent_id=row.parent_id,
        parent_key=parent_key,
        is_system=bool(row.is_system),
        created_at=row.created_at,
    )


def _load_resource_or_404(db: Session, resource_id: str) -> Resource:
    """Return a catalog resource by id, or raise 404."""
    row = db.execute(
        select(Resource).where(Resource.id == resource_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found."
        )
    return row


def _load_action_or_404(db: Session, action_id: str) -> Action:
    """Return a catalog action by id, or raise 404."""
    row = db.execute(select(Action).where(Action.id == action_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Action not found."
        )
    return row


def _resource_descendant_keys(db: Session, resource_key: str) -> set[str]:
    """Return every descendant key of ``resource_key`` (self excluded), via the DB parent map."""
    parent_by_key = load_resource_parent_map(db)
    children: dict[str | None, list[str]] = {}
    for key, parent in parent_by_key.items():
        children.setdefault(parent, []).append(key)
    descendants: set[str] = set()
    stack = list(children.get(resource_key, ()))
    while stack:
        key = stack.pop()
        if key in descendants:
            continue
        descendants.add(key)
        stack.extend(children.get(key, ()))
    return descendants


@router.get("/catalog", response_model=CatalogOut)
async def get_catalog(_rbac: RbacReadDep, db: DbSession) -> CatalogOut:
    """Return the resource tree + action catalog the permissions grid renders from (Issue #141)."""
    ordered = _catalog_resources_ordered(db)
    resources_by_key = {
        row.key: row for row in db.execute(select(Resource)).scalars().all()
    }
    resources = [
        _resource_out(db, resources_by_key[key])
        for key, _parent in ordered
        if key in resources_by_key
    ]
    actions = [
        ActionOut(
            id=a.id,
            key=a.key,
            name=a.name,
            description=a.description,
            is_system=bool(a.is_system),
            created_at=a.created_at,
        )
        for a in db.execute(select(Action).order_by(Action.key)).scalars().all()
    ]
    return CatalogOut(resources=resources, actions=actions)


# --- Resources ----------------------------------------------------------------------------


@router.get("/resources", response_model=ResourceListOut)
async def list_resources(_rbac: RbacReadDep, db: DbSession) -> ResourceListOut:
    """List every catalog resource in tree order (parents before children)."""
    by_key = {row.key: row for row in db.execute(select(Resource)).scalars().all()}
    items = [
        _resource_out(db, by_key[key])
        for key, _parent in _catalog_resources_ordered(db)
        if key in by_key
    ]
    return ResourceListOut(items=items, total=len(items))


@router.post(
    "/resources", response_model=ResourceOut, status_code=status.HTTP_201_CREATED
)
async def create_resource(
    _rbac: RbacCreateDep,
    db: DbSession,
    current_user: CurrentUser,
    body: ResourceCreateIn,
) -> ResourceOut:
    """Create a catalog resource (optionally under a parent). Key must be unique."""
    if db.execute(
        select(Resource.id).where(Resource.key == body.key)
    ).scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Resource already exists: {body.key!r}",
        )
    parent_id: str | None = None
    if body.parent_key:
        parent = db.execute(
            select(Resource).where(Resource.key == body.parent_key)
        ).scalar_one_or_none()
        if parent is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown parent resource: {body.parent_key!r}",
            )
        parent_id = parent.id
    row = Resource(
        id=str(uuid4()),
        key=body.key,
        name=body.name,
        description=body.description,
        parent_id=parent_id,
        is_system=False,
    )
    db.add(row)
    db.flush()
    _rebuild_resource_cache(db)
    _record_audit(
        db,
        current_user,
        action=PermissionAuditAction.GRANT,
        target_type=PermissionAuditTargetType.RESOURCE,
        target_id=body.key,
        before=None,
        after={"key": body.key, "name": body.name, "parent_key": body.parent_key},
    )
    db.commit()
    db.refresh(row)
    return _resource_out(db, row)


@router.patch("/resources/{resource_id}", response_model=ResourceOut)
async def update_resource(
    _rbac: RbacUpdateDep,
    db: DbSession,
    current_user: CurrentUser,
    resource_id: str,
    body: ResourcePatchIn,
) -> ResourceOut:
    """Update a resource's name/description and/or reparent it (its key is immutable)."""
    row = _load_resource_or_404(db, resource_id)
    before = {"name": row.name, "description": row.description}
    reparented = False
    if body.name is not None:
        row.name = body.name
    if body.description is not None:
        row.description = body.description
    if body.parent_key is not None:
        if body.parent_key == "":
            row.parent_id = None
            reparented = True
        else:
            parent = db.execute(
                select(Resource).where(Resource.key == body.parent_key)
            ).scalar_one_or_none()
            if parent is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Unknown parent resource: {body.parent_key!r}",
                )
            if parent.key == row.key or parent.key in _resource_descendant_keys(
                db, row.key
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="A resource cannot be its own ancestor.",
                )
            row.parent_id = parent.id
            reparented = True
    db.flush()
    if reparented:
        _rebuild_resource_cache(db)
    _record_audit(
        db,
        current_user,
        action=PermissionAuditAction.GRANT,
        target_type=PermissionAuditTargetType.RESOURCE,
        target_id=row.key,
        before=before,
        after={"name": row.name, "description": row.description},
    )
    db.commit()
    db.refresh(row)
    return _resource_out(db, row)


@router.delete("/resources/{resource_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_resource(
    _rbac: RbacDeleteDep,
    db: DbSession,
    current_user: CurrentUser,
    resource_id: str,
) -> None:
    """Delete a resource. Blocked for system rows, ones with children, or ones a grant references."""
    row = _load_resource_or_404(db, resource_id)
    if row.is_system:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a system resource.",
        )
    if db.execute(
        select(Resource.id).where(Resource.parent_id == row.id)
    ).scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a resource that still has child resources.",
        )
    if db.execute(
        select(RolePermission.id).where(RolePermission.resource == row.key)
    ).scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a resource still referenced by a grant.",
        )
    _record_audit(
        db,
        current_user,
        action=PermissionAuditAction.REVOKE,
        target_type=PermissionAuditTargetType.RESOURCE,
        target_id=row.key,
        before={"key": row.key, "name": row.name},
        after=None,
    )
    db.delete(row)
    db.flush()
    _rebuild_resource_cache(db)
    db.commit()


# --- Actions ------------------------------------------------------------------------------


@router.get("/actions", response_model=ActionListOut)
async def list_actions(_rbac: RbacReadDep, db: DbSession) -> ActionListOut:
    """List every catalog action, ordered by key."""
    rows = db.execute(select(Action).order_by(Action.key)).scalars().all()
    items = [
        ActionOut(
            id=a.id,
            key=a.key,
            name=a.name,
            description=a.description,
            is_system=bool(a.is_system),
            created_at=a.created_at,
        )
        for a in rows
    ]
    return ActionListOut(items=items, total=len(items))


@router.post("/actions", response_model=ActionOut, status_code=status.HTTP_201_CREATED)
async def create_action(
    _rbac: RbacCreateDep,
    db: DbSession,
    current_user: CurrentUser,
    body: ActionCreateIn,
) -> ActionOut:
    """Create a catalog action. Key must be unique."""
    if db.execute(select(Action.id).where(Action.key == body.key)).scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Action already exists: {body.key!r}",
        )
    row = Action(
        id=str(uuid4()),
        key=body.key,
        name=body.name,
        description=body.description,
        is_system=False,
    )
    db.add(row)
    _record_audit(
        db,
        current_user,
        action=PermissionAuditAction.GRANT,
        target_type=PermissionAuditTargetType.ACTION,
        target_id=body.key,
        before=None,
        after={"key": body.key, "name": body.name},
    )
    db.commit()
    db.refresh(row)
    return ActionOut(
        id=row.id,
        key=row.key,
        name=row.name,
        description=row.description,
        is_system=bool(row.is_system),
        created_at=row.created_at,
    )


@router.patch("/actions/{action_id}", response_model=ActionOut)
async def update_action(
    _rbac: RbacUpdateDep,
    db: DbSession,
    current_user: CurrentUser,
    action_id: str,
    body: ActionPatchIn,
) -> ActionOut:
    """Update an action's name/description (its key is immutable)."""
    row = _load_action_or_404(db, action_id)
    before = {"name": row.name, "description": row.description}
    if body.name is not None:
        row.name = body.name
    if body.description is not None:
        row.description = body.description
    _record_audit(
        db,
        current_user,
        action=PermissionAuditAction.GRANT,
        target_type=PermissionAuditTargetType.ACTION,
        target_id=row.key,
        before=before,
        after={"name": row.name, "description": row.description},
    )
    db.commit()
    db.refresh(row)
    return ActionOut(
        id=row.id,
        key=row.key,
        name=row.name,
        description=row.description,
        is_system=bool(row.is_system),
        created_at=row.created_at,
    )


@router.delete("/actions/{action_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_action(
    _rbac: RbacDeleteDep,
    db: DbSession,
    current_user: CurrentUser,
    action_id: str,
) -> None:
    """Delete an action. Blocked for system rows or ones a named grant references."""
    row = _load_action_or_404(db, action_id)
    if row.is_system:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a system action.",
        )
    if db.execute(
        select(RolePermission.id).where(RolePermission.action == row.key)
    ).scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete an action still referenced by a grant.",
        )
    _record_audit(
        db,
        current_user,
        action=PermissionAuditAction.REVOKE,
        target_type=PermissionAuditTargetType.ACTION,
        target_id=row.key,
        before={"key": row.key, "name": row.name},
        after=None,
    )
    db.delete(row)
    db.commit()


# --- Permissions --------------------------------------------------------------------------


def _permission_out(db: Session, row: Permission) -> PermissionOut:
    """Build a :class:`PermissionOut`, joining the resource/action keys."""
    resource_key = db.execute(
        select(Resource.key).where(Resource.id == row.resource_id)
    ).scalar_one()
    action_key = db.execute(
        select(Action.key).where(Action.id == row.action_id)
    ).scalar_one()
    return PermissionOut(
        id=row.id,
        resource_id=row.resource_id,
        action_id=row.action_id,
        resource_key=resource_key,
        action_key=action_key,
        key=permission_key(resource_key, action_key),
        description=row.description,
        created_at=row.created_at,
    )


@router.get("/permissions", response_model=PermissionListOut)
async def list_permissions(_rbac: RbacReadDep, db: DbSession) -> PermissionListOut:
    """List every ``(resource, action)`` permission with its joined keys."""
    rows = db.execute(select(Permission)).scalars().all()
    items = sorted((_permission_out(db, p) for p in rows), key=lambda p: p.key)
    return PermissionListOut(items=items, total=len(items))


@router.post(
    "/permissions", response_model=PermissionOut, status_code=status.HTTP_201_CREATED
)
async def create_permission(
    _rbac: RbacCreateDep,
    db: DbSession,
    current_user: CurrentUser,
    body: PermissionCreateIn,
) -> PermissionOut:
    """Create a ``(resource, action)`` permission by key. Both must exist; the pair is unique."""
    resource = db.execute(
        select(Resource).where(Resource.key == body.resource_key)
    ).scalar_one_or_none()
    if resource is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown resource: {body.resource_key!r}",
        )
    action = db.execute(
        select(Action).where(Action.key == body.action_key)
    ).scalar_one_or_none()
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown action: {body.action_key!r}",
        )
    if db.execute(
        select(Permission.id).where(
            Permission.resource_id == resource.id,
            Permission.action_id == action.id,
        )
    ).scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Permission already exists for this resource and action.",
        )
    row = Permission(
        id=str(uuid4()),
        resource_id=resource.id,
        action_id=action.id,
        description=body.description,
    )
    db.add(row)
    _record_audit(
        db,
        current_user,
        action=PermissionAuditAction.GRANT,
        target_type=PermissionAuditTargetType.PERMISSION,
        target_id=permission_key(body.resource_key, body.action_key),
        before=None,
        after={"resource_key": body.resource_key, "action_key": body.action_key},
    )
    db.commit()
    db.refresh(row)
    return _permission_out(db, row)


@router.delete("/permissions/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_permission(
    _rbac: RbacDeleteDep,
    db: DbSession,
    current_user: CurrentUser,
    permission_id: str,
) -> None:
    """Delete a ``(resource, action)`` permission. Blocked when a grant references it."""
    row = db.execute(
        select(Permission).where(Permission.id == permission_id)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Permission not found."
        )
    if db.execute(
        select(RolePermission.id).where(RolePermission.permission_id == row.id)
    ).scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a permission still referenced by a grant.",
        )
    out = _permission_out(db, row)
    _record_audit(
        db,
        current_user,
        action=PermissionAuditAction.REVOKE,
        target_type=PermissionAuditTargetType.PERMISSION,
        target_id=out.key,
        before={"resource_key": out.resource_key, "action_key": out.action_key},
        after=None,
    )
    db.delete(row)
    db.commit()


# --- Nav gates (Issue #146) -----------------------------------------------------------------
#
# The Option C hybrid (docs/architecture/rbac-universal-surface-framework.md §5): a surface's
# *structure* (label/icon/href/group/order) stays Python-declared in ``nav_registry.py`` or a
# module's ``rbac_manifest.py`` — only its *gate* (which resource + verb/action currently governs
# it) is DB-overridable, reusing the same "DB overrides a code default, audited" shape the
# permission catalog itself already proved (M25). This deliberately narrows Issue #146's own issue
# text (which describes moving label/icon/order into the DB too, and retiring ``NAV_DESTINATIONS``
# — superseded by §6 of that doc, written after the issue was filed).


@dataclass(slots=True)
class _NavGateDefault:
    """One surface's Python-declared default gate, before any DB override is applied.

    ``scope`` is the required :class:`~src.commons.enums.GrantScope` tier (Issue #165, M28) — the
    gate's second axis, declared next to the verb on ``NavDestination``/``NavMeta`` and editable
    from this console exactly like the resource and verb.
    """

    surface_key: str
    label: str
    resource_key: str
    verb: str | None
    action: str | None
    scope: str


def _nav_destination_defaults() -> list[_NavGateDefault]:
    """Every :class:`~src.core.nav_registry.NavDestination` as a surface default.

    ``dest.resource`` is a plain resource key (Issue #154), so no coercion is needed any more.
    """
    return [
        _NavGateDefault(
            surface_key=dest.key,
            label=dest.label,
            resource_key=dest.resource,
            verb=dest.verb.value,
            action=None,
            scope=dest.scope.value,
        )
        for dest in NAV_DESTINATIONS
    ]


def _manifest_nav_defaults() -> list[_NavGateDefault]:
    """Every manifest-declared resource carrying ``NavMeta`` (a console sub-tab), as a default."""
    return [
        _NavGateDefault(
            surface_key=node.full_key,
            label=node.nav.label or catalog_display_name(node.full_key),
            resource_key=node.full_key,
            verb=None if node.nav.action else node.nav.verb,
            action=node.nav.action,
            scope=node.nav.scope,
        )
        for manifest in ALL_MANIFESTS
        for node in iter_resources(manifest)
        if node.nav is not None
    ]


def _nav_gate_out(
    default: _NavGateDefault, override: NavGateOverride | None
) -> NavGateOut:
    """Build a :class:`NavGateOut`, overlaying ``override`` on ``default`` when one exists."""
    resource_key = override.resource_key if override else default.resource_key
    verb = override.verb if override else default.verb
    action = override.action if override else default.action
    scope = override.scope if override else default.scope
    return NavGateOut(
        surface_key=default.surface_key,
        label=default.label,
        default_resource_key=default.resource_key,
        default_verb=default.verb,
        default_action=default.action,
        default_scope=default.scope,
        resource_key=resource_key,
        verb=verb,
        action=action,
        scope=scope,
        is_admin_override=bool(override.is_admin_override) if override else False,
        is_overridden=(resource_key, verb, action, scope)
        != (default.resource_key, default.verb, default.action, default.scope),
        updated_at=override.updated_at if override else None,
    )


def _all_nav_gate_defaults() -> dict[str, _NavGateDefault]:
    """Every known surface's default, ``NavDestination``s first, manifest tabs filling the rest."""
    defaults: dict[str, _NavGateDefault] = {}
    for default in (*_nav_destination_defaults(), *_manifest_nav_defaults()):
        defaults.setdefault(default.surface_key, default)
    return defaults


@router.get("/nav-gates", response_model=NavGateListOut)
async def list_nav_gates(_rbac: RbacReadDep, db: DbSession) -> NavGateListOut:
    """List every known surface's current gate — Python default overlaid with any live override."""
    defaults = _all_nav_gate_defaults()
    overrides = {
        row.surface_key: row
        for row in db.execute(select(NavGateOverride)).scalars().all()
    }
    items = [
        _nav_gate_out(default, overrides.get(surface_key))
        for surface_key, default in defaults.items()
    ]
    # A DB row can outlive its own default (a manifest node retired without a redeploy migrating
    # the row away) — surfaced rather than silently dropped, so a stale override is still visible
    # and resettable from the console.
    for surface_key, row in overrides.items():
        if surface_key in defaults:
            continue
        items.append(
            _nav_gate_out(
                _NavGateDefault(
                    surface_key=surface_key,
                    label=surface_key,
                    resource_key=row.resource_key,
                    verb=row.verb,
                    action=row.action,
                    scope=row.scope,
                ),
                row,
            )
        )
    items.sort(key=lambda item: item.surface_key)
    return NavGateListOut(items=items, total=len(items))


@router.patch("/nav-gates/{surface_key}", response_model=NavGateOut)
async def update_nav_gate(
    _rbac: RbacUpdateDep,
    db: DbSession,
    current_user: CurrentUser,
    surface_key: str,
    body: NavGatePatchIn,
) -> NavGateOut:
    """Re-point ``surface_key``'s gate — the console control behind Option C's "re-gate" recipe."""
    if (body.verb is None) == (body.action is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Exactly one of verb/action must be set.",
        )
    defaults = _all_nav_gate_defaults()
    if surface_key not in defaults:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown surface: {surface_key!r}.",
        )
    if body.resource_key not in _catalog_resource_keys(db):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown resource: {body.resource_key!r}",
        )
    if body.verb is not None:
        try:
            PermissionVerb(body.verb)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid verb: {body.verb!r}",
            ) from exc
    else:
        if not db.execute(
            select(Action.id).where(Action.key == body.action)
        ).scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown action: {body.action!r}",
            )
    if body.scope is not None and body.scope not in {
        scope.value for scope in GrantScope
    }:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown scope: {body.scope!r}",
        )

    row = db.execute(
        select(NavGateOverride).where(NavGateOverride.surface_key == surface_key)
    ).scalar_one_or_none()
    default = defaults[surface_key]
    before = _nav_gate_out(default, row).model_dump(mode="json")
    # Omitting ``scope`` keeps the tier the surface already carries — its live row's, or its
    # Python-declared default when it has no row yet (Issue #165) — so a re-gate that only moves the
    # resource or verb never silently widens or narrows how far the surface reaches.
    scope = body.scope or (row.scope if row is not None else default.scope)
    if row is None:
        row = NavGateOverride(surface_key=surface_key)
        db.add(row)
    row.resource_key = body.resource_key
    row.verb = body.verb
    row.action = body.action
    row.scope = scope
    row.is_admin_override = True
    db.flush()
    after_out = _nav_gate_out(default, row)
    _record_audit(
        db,
        current_user,
        action=PermissionAuditAction.GRANT,
        target_type=PermissionAuditTargetType.NAV_GATE_OVERRIDE,
        target_id=surface_key,
        before=before,
        after=after_out.model_dump(mode="json"),
    )
    db.commit()
    db.refresh(row)
    return _nav_gate_out(default, row)


@router.delete("/nav-gates/{surface_key}", status_code=status.HTTP_204_NO_CONTENT)
async def reset_nav_gate(
    _rbac: RbacDeleteDep,
    db: DbSession,
    current_user: CurrentUser,
    surface_key: str,
) -> None:
    """Reset ``surface_key`` back to its Python-declared default by deleting its override row."""
    row = db.execute(
        select(NavGateOverride).where(NavGateOverride.surface_key == surface_key)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No override to reset for {surface_key!r}.",
        )
    defaults = _all_nav_gate_defaults()
    default = defaults.get(
        surface_key,
        _NavGateDefault(
            surface_key=surface_key,
            label=surface_key,
            resource_key=row.resource_key,
            verb=row.verb,
            action=row.action,
            scope=row.scope,
        ),
    )
    before = _nav_gate_out(default, row).model_dump(mode="json")
    _record_audit(
        db,
        current_user,
        action=PermissionAuditAction.REVOKE,
        target_type=PermissionAuditTargetType.NAV_GATE_OVERRIDE,
        target_id=surface_key,
        before=before,
        after=_nav_gate_out(default, None).model_dump(mode="json"),
    )
    db.delete(row)
    db.commit()
