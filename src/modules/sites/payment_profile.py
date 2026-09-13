"""What a private clinic says it accepts: cash, card and medical schemes (Issue 37).

**A self-reported directory tag, not a claims integration.** Sending a patient to a private clinic
that will not take their scheme is worse than not filtering at all, so this data is only ever shown
as the clinic's own statement, with :data:`CLINIC_REPORTED_NOTICE` beside it wherever it appears,
and it is never checked against a scheme, a membership or a benefit. The real integration is backlog
item 1 (``docs/GITHUB/ISSUES/BACKLOG/BACKLOG_01_medical_aid_integration.md``).

The rules, each enforced here on the server so no client can skip them:

* **Only a private clinic holds a profile.** :func:`save_profile` refuses a public clinic with
  :class:`PublicClinicPaymentProfileError`, and :func:`remove_if_public` deletes a profile the moment
  a clinic's sector becomes public, so a stale "accepts Discovery" can never follow a clinic into the
  public sector.
* **Schemes come from a controlled list** (:class:`~src.commons.enums.MedicalAidScheme`), with a
  free-text name only for ``other``, so a patient's filter and a clinic's declaration meet on one
  value.
* **Saving is confirming.** Every save stamps ``last_confirmed_at``; :func:`confirm_profile` re-stamps
  it without changes. A profile not confirmed in :data:`STALE_AFTER_MONTHS` months is marked stale
  (:func:`is_stale`), and every surface shows that.

Discovery reads published profiles in bulk through :func:`published_profiles`, and filters on them
under Private only (:mod:`src.modules.discovery.service`). All of it is behind
``PAYMENT_FILTER_ENABLED`` on the patient's side.
"""

import calendar
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.commons.enums import MedicalAidScheme, SiteSector
from src.commons.ids import new_id
from src.commons.time import APP_TIMEZONE, business_date, now_sast, stored_sast
from src.core.site_scope import SiteAccess, published_select, scoped_select
from src.database.models import Site, SitePaymentMedicalAid, SitePaymentProfile

#: The one sentence every piece of payment or medical-aid information is shown with.
CLINIC_REPORTED_NOTICE: Final = (
    "Reported by the clinic. Please confirm with the clinic before you travel."
)
#: A profile confirmed longer ago than this is shown as stale.
STALE_AFTER_MONTHS: Final = 6

#: How each scheme is named on screen. A scheme added to the enum without a label fails
#: ``tests/unit/sites/test_payment_profile_rules.py``.
SCHEME_LABELS: Final[dict[MedicalAidScheme, str]] = {
    MedicalAidScheme.DISCOVERY_HEALTH: "Discovery Health",
    MedicalAidScheme.GEMS: "GEMS",
    MedicalAidScheme.BONITAS: "Bonitas",
    MedicalAidScheme.MOMENTUM_HEALTH: "Momentum Health",
    MedicalAidScheme.MEDSHIELD: "Medshield",
    MedicalAidScheme.BESTMED: "Bestmed",
    MedicalAidScheme.FEDHEALTH: "Fedhealth",
    MedicalAidScheme.MEDIHELP: "Medihelp",
    MedicalAidScheme.PROFMED: "Profmed",
    MedicalAidScheme.KEYHEALTH: "KeyHealth",
    MedicalAidScheme.SIZWE_HOSMED: "Sizwe Hosmed",
    MedicalAidScheme.COMPCARE: "CompCare",
    MedicalAidScheme.BANKMED: "Bankmed",
    MedicalAidScheme.POLMED: "Polmed",
    MedicalAidScheme.LA_HEALTH: "LA Health",
    MedicalAidScheme.OTHER: "Other",
}

#: Said when a public clinic's profile is written. Public clinics never carry one.
PUBLIC_CLINIC_REFUSAL: Final = (
    "Only a private clinic can list payment methods and medical aids. Public clinics never carry a "
    "payment profile."
)


class PublicClinicPaymentProfileError(ValueError):
    """A payment profile was written for a public clinic. Answered as 409."""


class PaymentProfileNotFoundError(LookupError):
    """The clinic has no payment profile to confirm. Answered as 404."""


@dataclass(frozen=True, slots=True)
class AcceptedScheme:
    """One scheme a clinic says it accepts, with the words to show."""

    scheme: MedicalAidScheme
    label: str


@dataclass(frozen=True, slots=True)
class PaymentChange:
    """What a clinic manager declares. ``other_name`` only with ``MedicalAidScheme.OTHER``."""

    accepts_cash: bool
    accepts_card: bool
    schemes: frozenset[MedicalAidScheme]
    other_name: str | None = None
    copay_notice: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentProfile:
    """A private clinic's declared payment methods, as every surface shows them."""

    site_id: str
    accepts_cash: bool
    accepts_card: bool
    schemes: tuple[AcceptedScheme, ...]
    copay_notice: str | None
    last_confirmed_at: datetime
    stale: bool
    notice: str = CLINIC_REPORTED_NOTICE


def months_before(day: date, months: int) -> date:
    """``day`` moved back ``months`` calendar months, clamped to the end of a shorter month."""
    year, month_index = divmod(day.year * 12 + (day.month - 1) - months, 12)
    month = month_index + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def is_stale(last_confirmed_at: datetime, moment: datetime | None = None) -> bool:
    """Whether a profile confirmed at ``last_confirmed_at`` is more than six months old at ``moment``.

    Calendar months in Johannesburg: confirmed on 13 March, stale from 14 September.
    """
    moment = moment or now_sast()
    confirmed = last_confirmed_at.astimezone(APP_TIMEZONE).date()
    return confirmed < months_before(business_date(moment), STALE_AFTER_MONTHS)


def _profile(
    row: SitePaymentProfile, schemes: Sequence[SitePaymentMedicalAid], moment: datetime
) -> PaymentProfile:
    """The plain-data view of a stored profile, schemes in the controlled list's order."""
    order = {scheme: n for n, scheme in enumerate(MedicalAidScheme)}
    confirmed = stored_sast(row.last_confirmed_at)
    return PaymentProfile(
        site_id=row.site_id,
        accepts_cash=row.accepts_cash,
        accepts_card=row.accepts_card,
        schemes=tuple(
            AcceptedScheme(
                scheme=item.scheme_enum,
                label=item.other_name
                if item.scheme_enum is MedicalAidScheme.OTHER and item.other_name
                else SCHEME_LABELS[item.scheme_enum],
            )
            for item in sorted(schemes, key=lambda item: order[item.scheme_enum])
        ),
        copay_notice=row.copay_notice,
        last_confirmed_at=confirmed,
        stale=is_stale(confirmed, moment),
    )


def get_profile(
    db: Session, access: SiteAccess, *, moment: datetime | None = None
) -> PaymentProfile | None:
    """This clinic's profile, or ``None``, read through the site guard."""
    row = db.execute(scoped_select(SitePaymentProfile, access)).scalar_one_or_none()
    if row is None:
        return None
    schemes = db.execute(scoped_select(SitePaymentMedicalAid, access)).scalars().all()
    return _profile(row, schemes, moment or now_sast())


def save_profile(
    db: Session,
    access: SiteAccess,
    site: Site,
    change: PaymentChange,
    *,
    confirmed_by: str | None,
    moment: datetime | None = None,
) -> PaymentProfile:
    """Declare (or replace) a private clinic's payment profile, confirming it now. Caller commits.

    Raises:
        PublicClinicPaymentProfileError: If the clinic is public, whatever the client sent.
    """
    if site.sector_enum is not SiteSector.PRIVATE:
        raise PublicClinicPaymentProfileError(PUBLIC_CLINIC_REFUSAL)
    moment = moment or now_sast()
    row = db.execute(scoped_select(SitePaymentProfile, access)).scalar_one_or_none()
    if row is None:
        row = SitePaymentProfile(site_id=access.site_id)
        db.add(row)
    row.accepts_cash = change.accepts_cash
    row.accepts_card = change.accepts_card
    row.copay_notice = (change.copay_notice or "").strip() or None
    row.last_confirmed_at = moment
    row.confirmed_by = confirmed_by
    db.flush()
    db.execute(
        delete(SitePaymentMedicalAid).where(
            SitePaymentMedicalAid.site_id == access.site_id
        )
    )
    for scheme in change.schemes:
        db.add(
            SitePaymentMedicalAid(
                id=new_id(),
                site_id=access.site_id,
                scheme=scheme.value,
                other_name=(change.other_name or "").strip() or None
                if scheme is MedicalAidScheme.OTHER
                else None,
            )
        )
    db.flush()
    found = get_profile(db, access, moment=moment)
    assert found is not None  # just written
    return found


def confirm_profile(
    db: Session,
    access: SiteAccess,
    site: Site,
    *,
    confirmed_by: str | None,
    moment: datetime | None = None,
) -> PaymentProfile:
    """Re-confirm a profile unchanged, clearing its staleness. Caller commits.

    Raises:
        PublicClinicPaymentProfileError: If the clinic is public.
        PaymentProfileNotFoundError: If the clinic has not declared a profile yet.
    """
    if site.sector_enum is not SiteSector.PRIVATE:
        raise PublicClinicPaymentProfileError(PUBLIC_CLINIC_REFUSAL)
    row = db.execute(scoped_select(SitePaymentProfile, access)).scalar_one_or_none()
    if row is None:
        raise PaymentProfileNotFoundError(
            "This clinic has not listed its payment methods yet."
        )
    moment = moment or now_sast()
    row.last_confirmed_at = moment
    row.confirmed_by = confirmed_by
    db.flush()
    found = get_profile(db, access, moment=moment)
    assert found is not None
    return found


def remove_if_public(db: Session, access: SiteAccess, site: Site) -> bool:
    """Delete a clinic's profile when it is (or has just become) public. Returns whether one went."""
    if site.sector_enum is SiteSector.PRIVATE:
        return False
    row = db.execute(scoped_select(SitePaymentProfile, access)).scalar_one_or_none()
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def published_profiles(
    db: Session, site_ids: Collection[str], *, moment: datetime | None = None
) -> dict[str, PaymentProfile]:
    """The profiles of many publicly visible **private** clinics at once, for discovery.

    Two queries whatever the number of clinics. A public clinic contributes nothing even if a row
    somehow existed, because the sector is part of the read.
    """
    if not site_ids:
        return {}
    moment = moment or now_sast()
    rows = (
        db.execute(
            published_select(SitePaymentProfile, site_ids).where(
                Site.sector == SiteSector.PRIVATE.value
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return {}
    by_site: dict[str, list[SitePaymentMedicalAid]] = {}
    for item in db.execute(
        published_select(SitePaymentMedicalAid, [row.site_id for row in rows])
    ).scalars():
        by_site.setdefault(item.site_id, []).append(item)
    return {
        row.site_id: _profile(row, by_site.get(row.site_id, []), moment) for row in rows
    }
