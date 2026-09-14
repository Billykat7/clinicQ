"""Site model: one clinic, with a real position on a map (Issue 23).

Everything else in ClinicQ hangs off this row. Queues belong to a site (Issue 25), tickets belong
to those queues (Issue 39), staff hold their role *at* a site (Issue 15), and the tenancy guard
(Issue 19) reads every site-scoped query's filter from this table's ids. It is therefore the one
model worth over-explaining:

* **The location is a PostGIS ``geography(Point, 4326)``**, not a latitude/longitude pair, because
  discovery (Issue 31) asks "within 5 km of here" and ``ST_DWithin`` on a geography answers that in
  metres over the spheroid with an index behind it. :class:`~src.database.types.PointGeography`
  keeps the Python side a :class:`~src.commons.geo.Coordinates` value, so nothing above the column
  handles WKB.
* **The GiST index is declared here and created by the migration** (``0007``), not conjured by the
  ORM layer: it is the difference between a radius search scanning every clinic in the country and
  one touching a handful, and M5 must not have to migrate a populated table to get it.
* **``slug`` is the stable public handle.** Ids are UUIDv7 and fine in a URL, but a clinic's page,
  a USSD menu entry and a seeded fixture all want a name that does not change when a row is
  re-created, so the slug is unique and immutable in practice.
* **``sector`` and ``status`` are enums** (:class:`~src.commons.enums.SiteSector`,
  :class:`~src.commons.enums.SiteStatus`), never free strings: a directory filter, a report and a
  channel menu all read them.

The site row has **no ``site_id`` column** — it *is* the site — so it is not picked up by the
site-scope guard's model discovery. Its routes go through
:func:`~src.core.site_scope.require_site_access` all the same; that is what the cross-tenant suite
asserts over HTTP.
"""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import (
    SITE_DEFAULT_BOARD_LANGUAGE,
    SITE_DEFAULT_BOARD_THEME,
    SITE_DEFAULT_DISPLAY_MODE,
    SITE_DEFAULT_STATUS,
    SITE_DEFAULT_TRANSFER_PLACEMENT,
    BoardLanguage,
    BoardTheme,
    DisplayMode,
    SaProvince,
    SiteSector,
    SiteStatus,
    TransferPlacement,
)
from src.commons.geo import Coordinates
from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import ActiveMixin, SoftDeleteMixin, TimestampMixin
from src.database.types import PointGeography


class Site(Base, TimestampMixin, ActiveMixin, SoftDeleteMixin):
    """One clinic: what it is called, where it is, and whether patients may see it."""

    __tablename__ = "site"
    __table_args__ = (
        # The radius search (Issue 31). GiST over the geography column is what makes ST_DWithin an
        # index scan; ``EXPLAIN`` over that query is asserted in tests/integration/sites/.
        Index(
            "ix_clinicq_site_location_gist",
            "location",
            postgresql_using="gist",
        ),
        # The directory's own filter: "public clinics that are verified", before distance narrows
        # it. A composite over the two columns every listing restricts on.
        Index("ix_clinicq_site_sector_status", "sector", "status"),
        # The verification console's own read: this status, oldest submission first (Issue 29).
        Index("ix_clinicq_site_status_submitted_at", "status", "submitted_at"),
        # Where a transferred patient lands: one of TransferPlacement's values (Issue 45).
        CheckConstraint(
            "transfer_placement IN ("
            + ", ".join(
                f"'{placement.value}'" for placement in sorted(TransferPlacement)
            )
            + ")",
            name="transfer_placement",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    """The stable public handle: ``hillbrow-chc``. Lowercase, hyphenated, unique platform-wide."""
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    """What the clinic calls itself, as it appears to a patient."""
    sector: Mapped[str] = mapped_column(String(16), nullable=False)
    """:class:`~src.commons.enums.SiteSector`: a public facility or a private practice."""
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=SITE_DEFAULT_STATUS.value
    )
    """:class:`~src.commons.enums.SiteStatus`. Only ``verified`` is ever visible in discovery."""

    location: Mapped[Coordinates] = mapped_column(PointGeography, nullable=False)
    """WGS 84 position. Required: a clinic nobody can find is not in the directory (Issue 23)."""

    address_line: Mapped[str] = mapped_column(String(200), nullable=False)
    suburb: Mapped[str | None] = mapped_column(String(120), nullable=True)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    province: Mapped[str] = mapped_column(String(32), nullable=False)
    """:class:`~src.commons.enums.SaProvince`: one of the nine, spelled one way."""
    postal_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    phone_e164: Mapped[str | None] = mapped_column(String(20), nullable=True)
    """The clinic's own switchboard, in E.164. Not a patient's number; not personal data."""
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Free text for the operator: "entrance on the Klein Street side", and the like."""

    # ── Onboarding and verification (Issue 29) ────────────────────────────────────────────
    #
    # A directory is only trustworthy if the entries are real, so anyone may submit a clinic and a
    # platform admin checks it before it is visible to a patient. These columns are the submission
    # and the decision; the transitions and their audit rows are src.modules.sites.onboarding's.
    #
    # The contact is a **person at the clinic**, not a patient: a name, a work address and a work
    # number, held so the platform can reach whoever is responsible for the entry. It is still
    # personal information, which is why it is named in the audit module's redaction set through
    # the field names it shares (``email``, ``phone_e164``) and why Issue 95's data map lists it.

    contact_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    """Who is responsible for this entry at the clinic."""
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When it was put forward for checking. ``None`` for a clinic an operator typed in."""
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """The platform admin who decided. No foreign key: the decision outlives their account."""
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Why it was rejected, or what more is needed. Shown to the submitter, so it is written for
    them rather than for the operator's own notes."""

    # ── Display and privacy (Issue 27, non-negotiable 4) ──────────────────────────────────
    #
    # The waiting-room board is the sharpest privacy surface in the product, and these five columns
    # are where a clinic chooses what it shows. Two things about them are not ordinary settings:
    #
    # * ``display_mode`` is created ``number_only`` **on every code path** — the API, onboarding and
    #   the seed alike — and the default below is the only place that member is written.
    #   ``tests/unit/sites/test_display_defaults.py`` fails the build if any path sets another;
    # * the server applies the mode **before anything reaches the board** (Issue 58): under
    #   ``number_only`` a name is not in the payload at all, not merely hidden by CSS.

    display_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SITE_DEFAULT_DISPLAY_MODE.value
    )
    """:class:`~src.commons.enums.DisplayMode`. ``number_only`` on creation, always."""
    display_show_comment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    """Whether the reason a patient gave may appear beside their ticket. Off on creation.

    Health information on a public screen, so it is a separate decision from the name, and it still
    needs the patient's own per-visit consent at render time (Issue 58)."""
    board_language: Mapped[str] = mapped_column(
        String(8), nullable=False, default=SITE_DEFAULT_BOARD_LANGUAGE.value
    )
    """:class:`~src.commons.enums.BoardLanguage`: what the board and its announcements speak."""
    board_theme: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=SITE_DEFAULT_BOARD_THEME.value,
        server_default=SITE_DEFAULT_BOARD_THEME.value,
    )
    """:class:`~src.commons.enums.BoardTheme`: how the board is coloured for its room (Issue 59)."""
    announce_audio: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    """Whether a call is announced aloud as well as shown (Issue 60).

    On by default, and deliberately: a patient who cannot read the board is the one the chime is
    for. What the announcement *says* is still governed by ``display_mode``."""
    reason_retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    """How long a visit's ``reason_text`` is kept before the purge job (Issue 95) deletes it.

    Validated against :data:`~src.modules.sites.settings.REASON_RETENTION_CEILING_DAYS`, the
    interim policy ceiling this issue chose until the M13 data map sets the real one."""

    transfer_placement: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=SITE_DEFAULT_TRANSFER_PLACEMENT.value,
        server_default=SITE_DEFAULT_TRANSFER_PLACEMENT.value,
    )
    """:class:`~src.commons.enums.TransferPlacement`: where a transferred patient goes (Issue 45)."""
    recall_timeout_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """The clinic's recall timeout in minutes for queues that set none (Issue 43). ``None`` uses the
    platform default (``QUEUE_RECALL_TIMEOUT_MINUTES``)."""
    analytics_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    """Whether anonymous discovery events about this clinic are recorded (Issue 38).

    On by default; a clinic that objects switches it off and no view or join of it is recorded from
    then on. The events never identify a patient either way."""

    @property
    def sector_enum(self) -> SiteSector:
        """``sector`` as its enum member, for code that compares rather than renders."""
        return SiteSector(self.sector)

    @property
    def status_enum(self) -> SiteStatus:
        """``status`` as its enum member."""
        return SiteStatus(self.status)

    @property
    def province_enum(self) -> SaProvince:
        """``province`` as its enum member."""
        return SaProvince(self.province)

    @property
    def display_mode_enum(self) -> DisplayMode:
        """``display_mode`` as its enum member."""
        return DisplayMode(self.display_mode)

    @property
    def board_theme_enum(self) -> BoardTheme:
        """``board_theme`` as its enum member."""
        return BoardTheme(self.board_theme)

    @property
    def board_language_enum(self) -> BoardLanguage:
        """``board_language`` as its enum member."""
        return BoardLanguage(self.board_language)

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return f"Site(id={self.id!r}, slug={self.slug!r})"
