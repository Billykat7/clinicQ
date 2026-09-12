"""A clinic signs itself up, and a platform admin checks it before patients see it (Issue 29).

A discovery directory is only trustworthy if the entries are real. Anyone may submit a clinic;
nobody but a platform admin can make one visible. That is the whole module, and three things about
it are worth stating before the code:

* **The lifecycle is a small state machine**, not a status column anybody may set.
  :data:`ALLOWED_TRANSITIONS` is the whole of it, and :func:`transition` is the only writer — the
  same shape non-negotiable 2 gives a ticket's status, for the same reason: four consumers
  (discovery, the join gate, the admin queue and the submitter's notification) derive behaviour
  from this value, and one direct write outside the machine makes them disagree.
* **A submission grants nothing.** The public form creates a clinic in ``pending_verification``
  with the submitter's contact details on it, and no account, no role and no session. Whoever
  submitted it cannot see it in the admin queue, cannot edit it, and is not staff anywhere — the
  first manager of a verified clinic is invited afterwards (Issue 22).
* **The decision is audited and announced, never delivered.** Every transition writes an audit row
  naming the deciding admin, and publishes
  :class:`~src.core.domain_events.SiteStatusChanged` after the commit; the notification service
  (Issue 63) subscribes. A platform admin must be able to reject a clinic while the SMS gateway is
  down.

**Suspension stops joins immediately** because the join gate
(:func:`src.modules.sites.availability.join_gate`) reads the status on every request, and the
patient-facing search (:mod:`src.modules.sites.discovery`) filters on it in its base query. There is
no cache to invalidate and no list to rebuild.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from src.commons.enums import AuditAction, AuditEntityType, SiteStatus
from src.commons.time import now_sast
from src.core.audit import record_audit_event
from src.core.domain_events import SiteStatusChanged, publish_after_commit
from src.database.models.site import Site
from src.database.models.user import User
from src.modules.queues.service import create_default_queues
from src.modules.sites.catalogue import seed_default_catalogue
from src.modules.sites.schemas import SiteRegistrationIn
from src.modules.sites.service import SlugAlreadyUsedError, slug_is_free

#: The only moves a clinic's listing may make. Read as "from → the statuses it may go to".
#:
#: ``VERIFIED`` can go back to ``PENDING_VERIFICATION`` on purpose: "we need more information"
#: about a clinic that is already listed has to be possible without suspending it, and a platform
#: admin who is unsure should have a step short of switching a working clinic off.
ALLOWED_TRANSITIONS: Mapping[SiteStatus, frozenset[SiteStatus]] = {
    SiteStatus.DRAFT: frozenset(
        {SiteStatus.PENDING_VERIFICATION, SiteStatus.SUSPENDED}
    ),
    SiteStatus.PENDING_VERIFICATION: frozenset(
        {SiteStatus.VERIFIED, SiteStatus.DRAFT, SiteStatus.SUSPENDED}
    ),
    SiteStatus.VERIFIED: frozenset(
        {SiteStatus.SUSPENDED, SiteStatus.PENDING_VERIFICATION}
    ),
    SiteStatus.SUSPENDED: frozenset({SiteStatus.VERIFIED, SiteStatus.DRAFT}),
}

#: What the submitter is told when their clinic is rejected with no reason typed. A rejection with
#: no words is the one thing this workflow must not produce, so there is a sentence even then.
DEFAULT_REJECTION_NOTE = (
    "We could not confirm this clinic's details. Please check them and submit again."
)


class TransitionNotAllowedError(ValueError):
    """That is not a move this clinic's listing may make."""


class ReviewNoteRequiredError(ValueError):
    """A rejection or a request for more information has to say what is wrong."""


def submit_registration(db: Session, payload: SiteRegistrationIn) -> Site:
    """Create a clinic from the public form, waiting for a platform admin. The caller commits.

    It gets the default queue set and services catalogue straight away, so an approving admin sees
    a clinic that is ready rather than an empty shell — and so nothing about onboarding depends on
    somebody remembering a second step. ``display_mode`` is not touched here (non-negotiable 4:
    the column's default is the only thing that decides it).

    Raises:
        SlugAlreadyUsedError: If a clinic already holds that slug. The message is the same one the
            operator's create path gives; it does **not** say whether the existing clinic is
            visible, because a stranger submitting a form should not learn that from a refusal.
    """
    if not slug_is_free(db, payload.slug):
        raise SlugAlreadyUsedError(
            f"The web address {payload.slug!r} is already in use. Please choose another."
        )
    site = Site(
        slug=payload.slug,
        name=payload.name,
        sector=payload.sector.value,
        status=SiteStatus.PENDING_VERIFICATION.value,
        location=payload.location.to_coordinates(),
        address_line=payload.address_line,
        suburb=payload.suburb,
        city=payload.city,
        province=payload.province.value,
        phone_e164=payload.phone_e164,
        contact_name=payload.contact_name,
        contact_email=str(payload.contact_email),
        contact_phone=payload.contact_phone,
        submitted_at=now_sast(),
    )
    db.add(site)
    db.flush()
    create_default_queues(db, site.id)
    seed_default_catalogue(db, site.id)
    publish_after_commit(
        db,
        SiteStatusChanged(
            site_id=site.id,
            site_name=site.name,
            from_status=SiteStatus.DRAFT.value,
            to_status=SiteStatus.PENDING_VERIFICATION.value,
            decided_by="submitter",
            note=None,
            contact_email=site.contact_email,
            contact_phone=site.contact_phone,
        ),
    )
    return site


def pending_queue(
    db: Session, *, status: SiteStatus | None = None, query: str | None = None
) -> Select[tuple[Site]]:
    """The platform admin's verification queue, oldest submission first.

    Not site-scoped, and that is the point: this is the **cross-clinic** console the operator works
    in, reachable only through a ``business``-tier grant on ``sites``. The site guard's job is to
    stop a clinic reading another clinic; it is not what gates the operator's own queue.
    """
    statement = select(Site).where(Site.is_deleted.is_(False))
    statement = statement.where(
        Site.status == (status or SiteStatus.PENDING_VERIFICATION).value
    )
    if query:
        pattern = f"%{query.strip().lower()}%"
        statement = statement.where(
            func.lower(Site.name).like(pattern)
            | func.lower(Site.city).like(pattern)
            | func.lower(Site.suburb).like(pattern)
        )
    return statement.order_by(Site.submitted_at, Site.created_at)


def transition(
    db: Session,
    site: Site,
    to_status: SiteStatus,
    *,
    decided_by: User,
    note: str | None = None,
    ip_address: str | None = None,
) -> Site:
    """Move a clinic's listing, audit it and announce it. The caller commits.

    The **only** writer of ``site.status``. Everything that derives behaviour from it — discovery,
    the join gate, the queue below, the submitter's notification — reads one value written in one
    place, which is what keeps them from disagreeing.

    Args:
        db: The session.
        site: The clinic.
        to_status: Where it is going.
        decided_by: The platform admin deciding.
        note: What to tell the submitter. **Required** when rejecting or asking for more.
        ip_address: The deciding request's client IP, for the audit row.

    Returns:
        The clinic, moved.

    Raises:
        TransitionNotAllowedError: If that is not a move this listing may make.
        ReviewNoteRequiredError: If a rejection or a request for more information says nothing.
    """
    current = site.status_enum
    if to_status not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise TransitionNotAllowedError(
            f"A clinic that is {current.value!r} cannot become {to_status.value!r}."
        )
    sends_it_back = to_status in {
        SiteStatus.DRAFT,
        SiteStatus.PENDING_VERIFICATION,
        SiteStatus.SUSPENDED,
    }
    if sends_it_back and current is not SiteStatus.DRAFT and not (note or "").strip():
        raise ReviewNoteRequiredError(
            "Say what is wrong, or what more is needed. The person who submitted this clinic is "
            "shown exactly what you write here."
        )

    site.status = to_status.value
    site.reviewed_at = now_sast()
    site.reviewed_by = str(decided_by.id)
    site.review_note = (note or "").strip() or None
    db.flush()

    record_audit_event(
        db,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.SITE,
        entity_id=site.id,
        actor=decided_by.email,
        actor_id=str(decided_by.id),
        ip_address=ip_address,
        context=(
            f"listing {current.value} -> {to_status.value}"
            + (f": {site.review_note}" if site.review_note else "")
        ),
    )
    publish_after_commit(
        db,
        SiteStatusChanged(
            site_id=site.id,
            site_name=site.name,
            from_status=current.value,
            to_status=to_status.value,
            decided_by=decided_by.email,
            note=site.review_note,
            contact_email=site.contact_email,
            contact_phone=site.contact_phone,
        ),
    )
    return site
