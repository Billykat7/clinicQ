"""site_payment_profile: what a private clinic says it accepts (Issue 37).

**A self-reported directory tag, not an eligibility check.** A private clinic declares whether it
takes cash and cards and which medical schemes it accepts; ClinicQ shows that, always with "reported
by the clinic, please confirm before travelling", and lets a patient filter by it under Private. It
never checks a membership, a benefit or a claim: the real claims integration is backlog item 1.

* :class:`SitePaymentProfile` is one row per clinic, keyed by the clinic itself, with the date the
  clinic last confirmed it. A profile not confirmed in six months is shown as stale.
* :class:`SitePaymentMedicalAid` is one row per accepted scheme, from the controlled
  :class:`~src.commons.enums.MedicalAidScheme` list; ``other_name`` holds the scheme's name when the
  clinic chose ``other``. Rows rather than a list column, so discovery filters with an index.

**Only a private clinic can hold a profile.** That is enforced in
:mod:`src.modules.sites.payment_profile`, which refuses to write one for a public clinic and removes
one when a clinic's sector changes to public; a check constraint cannot see the site's sector.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema, MedicalAidScheme
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

SCHEMA = DbSchema.CLINICQ.value


class SitePaymentProfile(Base, TimestampMixin):
    """One private clinic's declared payment methods, and when it last confirmed them."""

    __tablename__ = "site_payment_profile"

    site_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.site.id", ondelete="CASCADE"),
        primary_key=True,
    )
    accepts_cash: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    accepts_card: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    copay_notice: Mapped[str | None] = mapped_column(Text, nullable=True)
    """What a patient on a scheme may still pay, in the clinic's words: "A co-payment may apply"."""
    last_confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """When the clinic last saved or re-confirmed this (Africa/Johannesburg)."""
    confirmed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """The staff member who confirmed it. No foreign key: the record outlives their account."""


class SitePaymentMedicalAid(Base):
    """One scheme a private clinic says it accepts."""

    __tablename__ = "site_payment_medical_aid"
    __table_args__ = (
        UniqueConstraint(
            "site_id", "scheme", name="uq_site_payment_medical_aid_site_id"
        ),
        # Discovery's filter: "private clinics that accept this scheme".
        Index("ix_clinicq_site_payment_medical_aid_scheme", "scheme", "site_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    site_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.site_payment_profile.site_id", ondelete="CASCADE"),
        nullable=False,
    )
    scheme: Mapped[str] = mapped_column(String(32), nullable=False)
    """:class:`~src.commons.enums.MedicalAidScheme`."""
    other_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    """The scheme's name as the clinic typed it, only when ``scheme`` is ``other``."""

    @property
    def scheme_enum(self) -> MedicalAidScheme:
        """``scheme`` as its enum member."""
        return MedicalAidScheme(self.scheme)
