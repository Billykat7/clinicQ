"""Record-level audit trail: snapshot, diff and write an audit event (Issue #78).

Where :mod:`src.core.request_logging` records *requests* and the ``SECURITY_AUDIT`` log lines
record security-relevant *actions*, this module records the *records themselves*: every mutation
of a sensitive entity (tenant identity, lease, payment, document) becomes one immutable
:class:`~src.database.models.audit_event.AuditEvent` row carrying a usable before/after diff.

Three small pieces, deliberately decoupled from the ORM so any service can adopt them:

* :func:`snapshot` — capture a mapped model's column values as a plain, JSON-safe dict *before*
  and *after* a change (call it on either side of the mutation);
* :func:`build_diff` — reduce two snapshots to only the fields that changed, redacting the
  values of sensitive/encrypted fields so the diff records *that* a secret changed without
  becoming a second plaintext copy of it;
* :func:`record_audit_event` — construct the ``AuditEvent`` (filling in the request's id and site
  from the request context, Issue 20) and add it to the session (it is
  flushed/committed with the surrounding unit of work, so the audit row and the change it
  describes commit together — an audit write cannot be lost while the change lands, or vice
  versa).

The event is written into the *same* session and transaction as the mutation on purpose: the
audit trail and the operational change share one commit boundary, so they are atomic.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from src.commons.enums import AUDIT_REDACTED_FIELDS, AuditAction, AuditEntityType
from src.core.request_logging import current_request_context
from src.database.models.audit_event import AuditEvent

# The placeholder a redacted value is replaced with in a diff. A fixed sentinel (never the real
# value, and never revealing its length) so a diff over a sensitive field says only "it changed".
REDACTED = "<redacted>"

# Actor label used when a change is made by a background job rather than a signed-in user.
SYSTEM_ACTOR = "system"


@dataclass(frozen=True)
class AuditContext:
    """Who is making a change and from where, passed into a service to attribute an audit event.

    A small carrier so a service function can take one optional ``audit`` argument rather than
    three loose parameters. Built by a router from the caller's token claims and request IP; when
    a service is called by a background job (no request), pass ``None`` and the event is attributed
    to :data:`SYSTEM_ACTOR`.
    """

    actor: str | None = None
    actor_id: str | None = None
    ip_address: str | None = None


def _jsonable(value: Any) -> Any:
    """Coerce a column value into something the JSON diff column can store.

    Datetimes/dates become ISO strings, ``Decimal`` becomes a string (never a lossy float),
    ``UUID`` and ``Enum`` become their canonical string; everything already JSON-native
    (``str``/``int``/``float``/``bool``/``None``) passes through unchanged.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _redact(field: str, value: Any) -> Any:
    """Return the JSON-safe value for ``field``, or :data:`REDACTED` if it is sensitive.

    A ``None`` is left as ``None`` even for a redacted field so the diff can still show a secret
    being *set* (``None`` -> redacted) or *cleared* (redacted -> ``None``) without leaking it.
    """
    if value is not None and field in AUDIT_REDACTED_FIELDS:
        return REDACTED
    return _jsonable(value)


def snapshot(instance: object, *, fields: list[str] | None = None) -> dict[str, Any]:
    """Capture a mapped model instance's column values as a plain, JSON-safe dict.

    Args:
        instance: A SQLAlchemy-mapped model instance.
        fields: Optional explicit subset of column names to capture; by default every mapped
            column is captured. Restricting to the columns a service actually mutates keeps a
            diff focused (and avoids reading lazily-loaded relationships).

    Returns:
        ``{column_name: json_safe_value}``. Sensitive/encrypted fields are **not** redacted
        here — redaction happens in :func:`build_diff`, so a snapshot can also be used to build a
        POPIA export where the data subject is entitled to their own data.
    """
    if fields is not None:
        names = fields
    else:
        state = inspect(instance)
        assert (
            state is not None
        )  # a mapped instance always inspects to an InstanceState
        names = [c.key for c in state.mapper.column_attrs]
    return {name: _jsonable(getattr(instance, name)) for name in names}


def build_diff(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Reduce two snapshots to only the fields that changed, with sensitive values redacted.

    Args:
        before: Snapshot before the change (``None`` for a create).
        after: Snapshot after the change (``None`` for a delete).

    Returns:
        ``{field: {"before": ..., "after": ...}}`` for every field whose value differs. For a
        create only ``after`` is present per field; for a delete only ``before``. Values of
        fields in :data:`~src.commons.enums.AUDIT_REDACTED_FIELDS` are replaced with
        :data:`REDACTED`, so the diff proves *that* a secret changed without recording it.
    """
    before = before or {}
    after = after or {}
    diff: dict[str, dict[str, Any]] = {}
    for field in before.keys() | after.keys():
        old = before.get(field)
        new = after.get(field)
        if old == new:
            continue
        diff[field] = {
            "before": _redact(field, old),
            "after": _redact(field, new),
        }
    return diff


def record_audit_event(
    db: Session,
    *,
    action: AuditAction,
    entity_type: AuditEntityType,
    entity_id: str,
    actor: str | None,
    actor_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    diff: dict[str, dict[str, Any]] | None = None,
    ip_address: str | None = None,
    context: str | None = None,
    site_id: str | None = None,
    actor_role: str | None = None,
) -> AuditEvent:
    """Build an :class:`AuditEvent` for a change and add it to the session.

    The row is **added, not committed** here: it rides the caller's unit of work so the audit
    record and the change it describes share one commit boundary (both land, or neither does).

    Pass either a precomputed ``diff`` or the ``before``/``after`` snapshots (from which a diff
    is built via :func:`build_diff`); for actions with no field delta — a ``READ`` or ``EXPORT``
    — pass neither and the ``diff`` column stays null.

    Args:
        db: The session the surrounding mutation runs in.
        action: What happened (:class:`~src.commons.enums.AuditAction`).
        entity_type: The kind of record (:class:`~src.commons.enums.AuditEntityType`).
        entity_id: Primary key of the affected record (or the data-subject id for a meta-event).
        actor: Human-readable identity of the caller; ``None`` is normalised to
            :data:`SYSTEM_ACTOR` so the ``actor`` column is never null.
        actor_id: The acting ``user.id`` when known (search convenience only).
        before: Snapshot before the change, for diff building.
        after: Snapshot after the change, for diff building.
        diff: A precomputed diff; takes precedence over ``before``/``after`` when given.
        ip_address: Caller IP (IPv4/IPv6), when resolvable.
        context: Optional free-text note (e.g. the search filter behind a read event).
        site_id: The clinic the action happened at. Left unset it is taken from the request's site
            context, which the site guard binds (Issue 19), so a route inside a clinic records the
            clinic without every call site remembering to pass it.
        actor_role: What the actor was acting as. Recorded at the time, so a role withdrawn later
            does not change what the trail says.

    Returns:
        The added (unflushed) :class:`AuditEvent`.
    """
    context_now = current_request_context()
    if diff is None and (before is not None or after is not None):
        diff = build_diff(before, after)
    event = AuditEvent(
        actor=actor or SYSTEM_ACTOR,
        actor_id=actor_id,
        action=action.value,
        entity_type=entity_type.value,
        entity_id=entity_id,
        diff=diff or None,
        # The request the row belongs to: its id joins the row to that request's log lines, and its
        # site is the clinic the guard resolved. A background job has neither.
        site_id=site_id or (context_now.site_id if context_now else None),
        request_id=context_now.request_id if context_now else None,
        actor_role=actor_role,
        ip_address=ip_address,
        context=context,
    )
    db.add(event)
    return event
