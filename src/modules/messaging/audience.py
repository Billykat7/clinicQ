"""Announcement audiences: resolve a broadcast's recipients from the sender's authority (Issue #112).

Every user may *send*, but *whom* a broadcast reaches is authorised here — the sender never hands
in a recipient list they could not otherwise reach. An :class:`AudienceType` is a **rule**,
resolved server-side to a concrete set of recipients.

The kernel ships one rule it can resolve without knowing your domain:

* :attr:`~src.modules.messaging.enums.AudienceType.GLOBAL` — every user. Gated by the
  ``communications.announcements:broadcast_global`` **named action** (Issue #160), seeded to no
  one by default; grant it to the role that should be able to address the whole user base.

**Your audiences go in :func:`resolve_recipients`**, one branch each, and the shape of the GLOBAL
branch is the one to copy. Two rules make an audience safe, and both are easy to lose:

1. **Authorise before resolving.** Raise :class:`AudienceNotAuthorisedError` (403) *before* any
   recipient is computed, so a sender targeting something that is not theirs learns nothing about
   who is in it — not even whether it exists.
2. **Empty is not forbidden.** A sender who was entitled but reaches no one gets
   :class:`EmptyAudienceError` (409). Collapsing the two into one error is how "you may not do
   that" and "there is nobody there" become indistinguishable to the person trying to send.

The sender is always excluded from their own broadcast, and ids are de-duplicated.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import PermissionAction
from src.commons.exceptions import (
    AudienceNotAuthorisedError,
    EmptyAudienceError,
)
from src.core.rbac import active_roles_for_user, role_has_named_action
from src.database.models import User
from src.modules.messaging.enums import AudienceType


def _may_broadcast_globally(db: Session, sender: User) -> bool:
    """Whether ``sender`` holds ``communications.announcements:broadcast_global`` (Issue #160, M28).

    Resolved by the same engine as every other permission check — the union of the sender's active
    roles (Issue #136), each role's inheritance closure, deny-beats-allow — instead of comparing
    their role's name to a string. Seeded to ``admin`` only, so the default answer is unchanged.
    """
    return role_has_named_action(
        db,
        active_roles_for_user(db, sender),
        "communications.announcements",
        PermissionAction.BROADCAST_GLOBAL.value,
    )


def _all_user_ids(db: Session) -> list[str]:
    """Ids of every non-deleted user — the recipient set for a global announcement."""
    return list(
        db.execute(select(User.id).where(User.is_deleted.is_(False))).scalars().all()
    )


def resolve_recipients(
    db: Session,
    *,
    sender: User,
    audience_type: AudienceType,
    audience_ref: str | None,
) -> list[str]:
    """Authorise ``sender`` for the audience, then resolve it to a de-duplicated recipient set.

    The sender is always excluded from the result (you are not a recipient of your own broadcast),
    and ids are de-duplicated so a person who qualifies twice is addressed once.

    Raises:
        AudienceNotAuthorisedError: the sender may not target this audience (mapped to 403).
        EmptyAudienceError: the sender is entitled but the audience is empty (mapped to 409).
    """
    if audience_type is AudienceType.GLOBAL:
        if not _may_broadcast_globally(db, sender):
            raise AudienceNotAuthorisedError(audience_type.value)
        candidate_ids = _all_user_ids(db)

    # ── your audiences ───────────────────────────────────────────────────────────────────────
    # elif audience_type is AudienceType.<YOURS>:
    #     if not <the sender is entitled to this audience>:
    #         raise AudienceNotAuthorisedError(audience_type.value, audience_ref)
    #     candidate_ids = <the user ids it resolves to>

    else:
        # An audience type with no branch is refused, not silently resolved to nobody: a rule the
        # server does not understand must never look like a successful send to an empty room.
        raise AudienceNotAuthorisedError(str(audience_type), audience_ref)

    # De-duplicate (preserving order) and drop the sender — they are not their own recipient.
    seen: set[str] = {sender.id}
    recipients: list[str] = []
    for user_id in candidate_ids:
        if user_id in seen:
            continue
        seen.add(user_id)
        recipients.append(user_id)

    if not recipients:
        raise EmptyAudienceError(audience_type.value)
    return recipients
