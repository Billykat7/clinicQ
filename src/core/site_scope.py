"""The site guard: one helper every site-scoped query goes through (Issue 19).

**Non-negotiable 3** (``docs/guideline.md``): ClinicQ is multi-tenant from the first pilot, so a
receptionist at Clinic A must never read — or learn the existence of — a ticket at Clinic B.
Retrofitting that across forty routes costs far more than holding it from the start, so it lives
here, in one module, with three rules:

1. **Which sites a caller may reach is read from their role assignments**, never from a column, a
   claim in a token or a request parameter: :func:`permitted_site_ids` reads ``user_roles`` rows
   with ``scope_type='site'`` (Issue 15).
2. **A site the caller may not reach answers 404, never 403** (:func:`require_site_access`). A 403
   would confirm that the id exists, which is all an attacker needs to enumerate another clinic.
   The body is the same for a site that does not exist at all.
3. **Every query on a site-scoped row is built here**: :func:`scoped_select`,
   :func:`get_in_site_or_404`, :func:`staff_at_site`, and for a patient who has no role anywhere,
   :func:`published_select`. A router or service that writes its own
   ``select(SomeSiteScopedModel)`` fails ``tests/unit/security/test_site_scoped_queries.py``.

The **platform-admin escape hatch** is explicit, audited and read-only: a caller whose grant reaches
``business`` may read another clinic only by sending ``X-ClinicQ-Cross-Site-Reason`` with a reason,
and every such request writes an audit row naming the site, the path and the reason. Without the
header it is a 404 like anyone else's, so cross-site access is never something that just happens.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import ColumnElement, Select, select
from sqlalchemy.orm import Session

from src.commons.enums import (
    SITE_PUBLICLY_VISIBLE_STATUSES,
    AssignmentScopeType,
    AuditAction,
    AuditEntityType,
    GrantScope,
    ScopeShape,
    UserRole,
)
from src.core.audit import record_audit_event
from src.core.client_ip import resolve_client_ip
from src.core.request_logging import bind_request_context
from src.core.security import CurrentStaff
from src.database.models import Site, User, UserRoleAssignment
from src.database.session import get_db

#: The header a caller sends to read a clinic they are not assigned to, with the reason why.
CROSS_SITE_REASON_HEADER = "X-ClinicQ-Cross-Site-Reason"
#: How long a reason may be; it is stored on the audit row.
MAX_REASON_LENGTH = 200
#: The methods the escape hatch covers: reading. A cross-site **write** is refused outright.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: The one answer for "this site is not yours" and for "no such site". Identical, deliberately.
NOT_FOUND_DETAIL = "Not found."


def site_not_found() -> HTTPException:
    """The 404 every out-of-scope or unknown site id gets: the same body for both."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)


@dataclass(frozen=True, slots=True)
class SiteAccess:
    """The answer to "may this caller act in this clinic, and over which rows".

    Handed to every query helper below, so a service never decides scope for itself.
    """

    #: The site the request is about.
    site_id: str
    #: The signed-in staff member.
    user: User
    #: Whether this was the audited cross-site hatch rather than the caller's own clinic.
    cross_site: bool = False
    #: The queues a query may touch at this clinic, or ``None`` for all of them (Issue 53). Set for a
    #: caller whose grant on a queue-shaped resource reaches only ``own``: a nurse's rooms. Every
    #: helper below narrows by it, so another room's ticket answers 404 exactly as another clinic's.
    queue_ids: frozenset[str] | None = None

    @property
    def site_ids(self) -> frozenset[str]:
        """The sites a query may touch: this one. A filter is never wider than the request."""
        return frozenset({self.site_id})

    def whole_site(self) -> SiteAccess:
        """The same access without the room narrowing, for a lookup that is about the clinic.

        Used where a room-scoped caller legitimately names another queue at their clinic: the
        destination of a transfer (Issue 45), which is not a queue they act *in*.
        """
        return replace(self, queue_ids=None)


def permitted_site_ids(db: Session, user: User) -> frozenset[str]:
    """The clinics ``user`` holds a role at (``user_roles`` with ``scope_type='site'``).

    The whole answer to "whose rows": no column on the user, no claim in the token (a token lives
    15 minutes and a withdrawn assignment must stop working at once, Issue 15).
    """
    from src.core.scope import assigned_scope_ids

    return assigned_scope_ids(db, user, AssignmentScopeType.SITE)


def permitted_queue_ids(db: Session, user: User) -> frozenset[str]:
    """The queues ``user`` was put on (``user_roles`` with ``scope_type='queue'``, Issue 28)."""
    from src.core.scope import assigned_scope_ids

    return assigned_scope_ids(db, user, AssignmentScopeType.QUEUE)


def site_ids_in_scope(
    db: Session, user: User, resource_key: str
) -> frozenset[str] | None:
    """The clinics ``user`` may list on ``resource_key``, or ``None`` for "every clinic".

    The list-endpoint counterpart of :func:`require_site_access`, which answers for one site named
    in the path. A platform admin's grant reaches ``business``, so their listing is the platform's;
    everyone else sees exactly the clinics they hold a role at, and somebody assigned to none sees
    an empty list rather than everything (:func:`permitted_site_ids` returns an empty set, which is
    a real answer).

    ``None`` means *apply no filter* and is returned only for a ``business``-tier grant, so a
    caller reading this value cannot confuse "unrestricted" with "assigned to nothing".
    """
    if reaches_every_site(db, user, resource_key):
        return None
    return permitted_site_ids(db, user)


def reaches_every_site(db: Session, user: User, resource_key: str) -> bool:
    """Whether the caller's **grant** on this resource reaches the whole platform.

    Read from the grant's tier (``business``), never from the role's name — the property the
    kernel's scope tier exists to provide. ``platform_admin`` has it; a clinic manager does not.
    """
    from src.core.scope import resolve_scope_tier

    return resolve_scope_tier(db, user, resource_key) is GrantScope.BUSINESS


def _cross_site_reason(request: Request) -> str | None:
    """The reason header, trimmed, or ``None`` when it is absent or empty."""
    raw = (request.headers.get(CROSS_SITE_REASON_HEADER) or "").strip()
    return raw[:MAX_REASON_LENGTH] or None


def _audit_cross_site(
    db: Session, request: Request, user: User, site_id: str, reason: str
) -> None:
    """Record one platform-admin cross-site read. Committed here: the read itself writes nothing."""
    record_audit_event(
        db,
        action=AuditAction.READ,
        entity_type=AuditEntityType.SITE,
        entity_id=site_id,
        actor=user.email,
        actor_id=str(user.id),
        ip_address=resolve_client_ip(request),
        context=f"cross-site read of {request.url.path}: {reason}",
    )
    db.commit()


def resolve_site_access(
    db: Session, request: Request, user: User, site_id: str, resource_key: str
) -> SiteAccess:
    """Resolve ``user``'s access to ``site_id``, or raise 404. The one membership decision.

    A caller assigned to the site gets it. A caller whose grant reaches ``business`` gets it only
    for a safe method **and** only with a reason header, and that read is audited. Everyone else
    gets the same 404 an unknown site id gets.
    """
    if site_id in permitted_site_ids(db, user):
        bind_request_context(site_id=site_id)
        return SiteAccess(site_id=site_id, user=user)
    reason = _cross_site_reason(request)
    if (
        request.method.upper() in SAFE_METHODS
        and reason is not None
        and reaches_every_site(db, user, resource_key)
    ):
        _audit_cross_site(db, request, user, site_id, reason)
        bind_request_context(site_id=site_id)
        return SiteAccess(site_id=site_id, user=user, cross_site=True)
    raise site_not_found()


def own_queue_scope(
    db: Session, user: User, resource_key: str, site_id: str
) -> frozenset[str] | None:
    """The queues ``user`` may act in at ``site_id`` on ``resource_key``, or ``None`` for every queue.

    The row half of the ``own`` tier on a queue-shaped resource (``ScopeShape.QUEUE``, Issue 53): a
    nurse's grant on ``queues.call`` or ``queues.tickets`` reaches only the queues they were put on
    (Issue 28), and this is where that becomes a filter rather than a hope. A grant that reaches
    ``assigned`` or wider, or a resource of another shape, is not narrowed here.

    Resolved with the roles held **at this clinic**, like the verb, so a receptionist here who is a
    nurse elsewhere is not narrowed here.
    """
    from src.core.rbac_manifest_registry import manifest_scope_shape_map
    from src.core.scope import resolve_scope_tier

    if manifest_scope_shape_map().get(resource_key) is not ScopeShape.QUEUE:
        return None
    tier = resolve_scope_tier(
        db,
        user,
        resource_key,
        scope_type=AssignmentScopeType.SITE.value,
        scope_id=site_id,
    )
    if tier.satisfies(GrantScope.ASSIGNED):
        return None
    return permitted_queue_ids(db, user)


def require_site_access(resource_key: str, verb: str):
    """Dependency factory for a route with a ``{site_id}`` path parameter (Issue 19).

    Two gates in the order that keeps 404 honest:

    1. **Whose clinic** — :func:`resolve_site_access`. A site the caller is not assigned to is a
       404 before any permission is considered, so a refusal cannot be used to learn that an id
       exists;
    2. **What they may do there** — the RBAC verb, resolved with the roles the caller holds *at
       that site* (``scope_type='site'``), so a manager at Clinic A is not a manager at Clinic B.
       A caller who is at the site but lacks the verb gets 403: they already know the site exists.

    Returns the :class:`SiteAccess` the query helpers take.
    """
    from src.api.rbac_deps import RBAC_GATE_ATTRIBUTE
    from src.core.rbac import ensure_permission_key

    def dependency(
        site_id: str,
        request: Request,
        staff: CurrentStaff,
        db: Annotated[Session, Depends(get_db)],
    ) -> SiteAccess:
        access = resolve_site_access(db, request, staff, site_id, resource_key)
        ensure_permission_key(
            db,
            {"uid": str(staff.id), "email": staff.email, "sub": staff.email},
            resource_key,
            verb,
            scope_type=AssignmentScopeType.SITE.value,
            scope_id=site_id,
        )
        rooms = own_queue_scope(db, staff, resource_key, site_id)
        return access if rooms is None else replace(access, queue_ids=rooms)

    setattr(dependency, RBAC_GATE_ATTRIBUTE, (resource_key, verb))
    return dependency


# --------------------------------------------------------------------------------------
# The query helpers: every read of a site-scoped row is built by one of these
# --------------------------------------------------------------------------------------


def scoped_select(model: type[Any], access: SiteAccess) -> Select[Any]:
    """``SELECT * FROM <model> WHERE site_id = <the request's site>``.

    The only way a service builds a query on a site-scoped model: the filter is applied here, from
    the access the guard resolved, so a service cannot forget it or widen it.
    """
    statement = select(model).where(model.site_id.in_(access.site_ids))
    if access.queue_ids is not None:
        # A room-scoped caller (Issue 53): only their queues, and the rows hanging off them.
        from src.database.models import Queue

        if model is Queue:
            statement = statement.where(Queue.id.in_(sorted(access.queue_ids)))
        elif "queue_id" in model.__table__.columns:
            statement = statement.where(model.queue_id.in_(sorted(access.queue_ids)))
    return statement


def select_in_scope(model: type[Any], access: SiteAccess | None) -> Select[Any]:
    """``scoped_select`` for a clinic, or the whole platform when ``access`` is ``None``.

    The one way a query reads across clinics, so "this read is platform-wide" is a visible argument
    rather than a missing filter. Only a route whose gate demands a ``business``-tier grant may pass
    ``None``; the audit read API is the first (Issue 20).
    """
    return select(model) if access is None else scoped_select(model, access)


def publicly_visible_site_clauses() -> tuple[ColumnElement[bool], ...]:
    """The ``where`` clauses that make a clinic one a patient may be shown (Issues 29, 31).

    Live, switched on, and in a status the platform has published
    (:data:`~src.commons.enums.SITE_PUBLICLY_VISIBLE_STATUSES`). Written **once**, here, so the
    discovery search and every public read of a clinic's hours and queues apply the same rule.
    """
    return (
        Site.is_deleted.is_(False),
        Site.is_active.is_(True),
        Site.status.in_([status.value for status in SITE_PUBLICLY_VISIBLE_STATUSES]),
    )


def published_select(model: type[Any], site_ids: Iterable[str]) -> Select[Any]:
    """Rows of a site-scoped ``model`` belonging to ``site_ids``, **only where a patient may look**.

    The patient-facing counterpart of :func:`scoped_select`. A patient searching for a clinic holds
    no role anywhere, so there is no :class:`SiteAccess` to narrow by, and the rule that replaces it
    is the directory's: a row is readable only if its clinic is publicly visible. A draft, pending or
    suspended clinic's hours and queues are therefore unreachable from here even when its id is
    passed in, which is what keeps an unchecked clinic out of discovery by construction rather than
    by every caller remembering to filter first.

    It reads what a clinic publishes about itself (opening hours, queue names); a ticket or a
    patient is never a published row, and nothing here may be used to read one.
    """
    return (
        select(model)
        .join(Site, Site.id == model.site_id)
        .where(model.site_id.in_(list(site_ids)), *publicly_visible_site_clauses())
    )


def get_in_site_or_404(
    db: Session, model: type[Any], row_id: str, access: SiteAccess
) -> Any:
    """One row of ``model`` by id **within the caller's site**, or 404.

    A row at another clinic and a row that does not exist answer identically, which is what makes
    sequential or guessed ids useless (non-negotiable 3).
    """
    row = db.execute(
        scoped_select(model, access).where(model.id == row_id)
    ).scalar_one_or_none()
    if row is None:
        raise site_not_found()
    return row


def staff_at_site(
    access: SiteAccess, *, roles: Iterable[UserRole] | None = None
) -> Select[Any]:
    """The staff of the request's clinic: users holding a role **at that site**.

    Staff are site-scoped through their assignments rather than a column (Issue 15), so this is
    their :func:`scoped_select`. ``roles`` narrows to particular roles.
    """
    statement = (
        select(User)
        .join(UserRoleAssignment, UserRoleAssignment.user_id == User.id)
        .where(
            UserRoleAssignment.scope_type == AssignmentScopeType.SITE.value,
            UserRoleAssignment.scope_id.in_(access.site_ids),
            User.is_deleted.is_(False),
        )
        .distinct()
    )
    if roles is not None:
        statement = statement.where(
            UserRoleAssignment.role.in_([role.value for role in roles])
        )
    return statement


def roles_held_at_site(db: Session, user_id: str, access: SiteAccess) -> list[str]:
    """The roles ``user_id`` holds **at the request's clinic**, sorted.

    Here rather than in the staff module for the same reason as every other query on this page: the
    site filter is written once. A role someone holds at another clinic is not this clinic's
    business and never appears.
    """
    rows = db.execute(
        select(UserRoleAssignment.role).where(
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.scope_type == AssignmentScopeType.SITE.value,
            UserRoleAssignment.scope_id.in_(access.site_ids),
        )
    ).scalars()
    return sorted(set(rows))


def staff_member_in_site_or_404(db: Session, user_id: str, access: SiteAccess) -> User:
    """One staff member of the request's clinic by id, or the same 404 a stranger's id gets."""
    member = db.execute(
        staff_at_site(access).where(User.id == user_id)
    ).scalar_one_or_none()
    if member is None:
        raise site_not_found()
    return member
