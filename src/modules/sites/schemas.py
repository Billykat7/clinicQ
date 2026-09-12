"""Request and response models for the sites API (Issue 23).

Separate ``In`` and ``Out`` shapes, like every other module: what an operator may set and what the
API returns are different sets of fields. Two decisions worth naming:

* **A coordinate crosses the wire as ``latitude`` / ``longitude``**, the order a person reads one,
  and is converted to :class:`~src.commons.geo.Coordinates` in exactly one place
  (:meth:`SiteLocationIn.to_coordinates`). PostGIS's ``POINT(lon lat)`` ordering never leaves
  :mod:`src.database.types`.
* **``status`` is not settable on create.** Every site starts at
  :data:`~src.commons.enums.SITE_DEFAULT_STATUS`, and moves through the verification workflow
  (Issue 29), so a client cannot post itself into ``verified``.
"""

from __future__ import annotations

from datetime import date, datetime, time
from itertools import pairwise

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from src.commons.enums import (
    SITE_DEFAULT_BOARD_LANGUAGE,
    BoardLanguage,
    DisplayMode,
    SaProvince,
    ServiceCategory,
    SiteSector,
    SiteStatus,
)
from src.commons.geo import (
    CoordinateOutOfRangeError,
    Coordinates,
    assert_within_operating_area,
)
from src.database.models.clinic_service import (
    MAX_EXPECTED_MINUTES,
    MIN_EXPECTED_MINUTES,
)
from src.modules.sites.settings import (
    REASON_RETENTION_CEILING_DAYS,
    REASON_RETENTION_DEFAULT_DAYS,
    REASON_RETENTION_FLOOR_DAYS,
)

#: A slug is lowercase letters, digits and single hyphens: it appears in URLs and USSD menus.
SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
#: E.164, the one phone format this project stores.
PHONE_PATTERN = r"^\+[1-9]\d{6,14}$"


class SiteLocationIn(BaseModel):
    """A coordinate as a client sends it: latitude first, in degrees."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    def to_coordinates(self) -> Coordinates:
        """Return the validated value object, or raise for a point outside the operating country.

        Raises:
            ValueError: If the point is outside the operating area. Pydantic turns it into a 422
                carrying the message from :mod:`src.commons.geo`, which names the numbers read.
        """
        try:
            return assert_within_operating_area(
                Coordinates(latitude=self.latitude, longitude=self.longitude)
            )
        except CoordinateOutOfRangeError as exc:
            raise ValueError(str(exc)) from exc


class SiteLocationOut(BaseModel):
    """A coordinate as the API returns it."""

    latitude: float
    longitude: float

    @classmethod
    def of(cls, point: Coordinates) -> SiteLocationOut:
        """Build the response shape from the stored value object."""
        return cls(latitude=point.latitude, longitude=point.longitude)


class SiteIn(BaseModel):
    """The clinic profile an operator may set, on create and on update."""

    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(min_length=2, max_length=80, pattern=SLUG_PATTERN)
    sector: SiteSector
    location: SiteLocationIn
    address_line: str = Field(min_length=3, max_length=200)
    suburb: str | None = Field(default=None, max_length=120)
    city: str = Field(min_length=2, max_length=120)
    province: SaProvince
    postal_code: str | None = Field(default=None, max_length=10)
    phone_e164: str | None = Field(default=None, pattern=PHONE_PATTERN)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("location")
    @classmethod
    def _inside_the_operating_area(cls, value: SiteLocationIn) -> SiteLocationIn:
        """Refuse a location outside the operating country, with the reason (Issue 23)."""
        value.to_coordinates()
        return value


class SiteOut(BaseModel):
    """One clinic as the API returns it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    sector: SiteSector
    status: SiteStatus
    location: SiteLocationOut
    address_line: str
    suburb: str | None
    city: str
    province: SaProvince
    postal_code: str | None
    phone_e164: str | None
    notes: str | None
    is_active: bool
    created_at: datetime
    modified_at: datetime


class SiteListOut(BaseModel):
    """A page of clinics, plus the total the caller's scope and filters matched."""

    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    items: list[SiteOut]


class GeocodeIn(BaseModel):
    """An address to look up. Sent to ClinicQ, never from the browser to a geocoder."""

    address: str = Field(min_length=3, max_length=300)


class GeocodeCandidateOut(BaseModel):
    """One candidate the geocoder offered, for a person to accept or reject."""

    label: str
    location: SiteLocationOut


class GeocodeOut(BaseModel):
    """The candidates for one address, best first. Empty is a valid answer, not an error."""

    query: str
    candidates: list[GeocodeCandidateOut]


# --------------------------------------------------------------------------------------
# Opening hours, public holidays and ad-hoc closures (Issue 24)
# --------------------------------------------------------------------------------------

#: Monday is 0, as :meth:`datetime.date.weekday` numbers them.
MIN_WEEKDAY, MAX_WEEKDAY = 0, 6
#: The most spans one weekday may carry. A lunch break is two; a clinic with more than four
#: sessions in a day is describing something this model is the wrong shape for.
MAX_SPANS_PER_DAY = 4
#: How long a closure reason may be. It is shown to a patient, so it is a sentence, not an essay.
MAX_CLOSURE_REASON = 200


class TimeSpanIn(BaseModel):
    """One stretch of a clinic's day, in ``Africa/Johannesburg`` wall clock."""

    opens_at: time
    closes_at: time


class DayHoursIn(BaseModel):
    """One weekday's spans. No spans means the clinic does not open that day."""

    weekday: int = Field(ge=MIN_WEEKDAY, le=MAX_WEEKDAY)
    spans: list[TimeSpanIn] = Field(default_factory=list, max_length=MAX_SPANS_PER_DAY)

    @field_validator("spans")
    @classmethod
    def _spans_do_not_overlap(cls, value: list[TimeSpanIn]) -> list[TimeSpanIn]:
        """Refuse overlapping spans on one day: two answers to "are you open" is not an answer.

        A span whose ``closes_at`` is at or before its ``opens_at`` crosses midnight, which is
        legitimate (an after-hours service) but can only be the **last** span of a day, since
        anything after it would be the following morning.
        """
        ordered = sorted(value, key=lambda span: span.opens_at)
        for earlier, later in pairwise(ordered):
            if earlier.closes_at <= earlier.opens_at:
                raise ValueError(
                    "A span that crosses midnight has to be the last one of its day."
                )
            if later.opens_at < earlier.closes_at:
                raise ValueError(
                    f"The spans {earlier.opens_at}-{earlier.closes_at} and "
                    f"{later.opens_at}-{later.closes_at} overlap."
                )
        return ordered


class WeeklyHoursIn(BaseModel):
    """A clinic's whole ordinary week, replaced in one request.

    A whole-week replacement rather than per-day edits on purpose: "what are your hours" is one
    answer, and a partial update is how a clinic ends up with Tuesday from last year.
    """

    days: list[DayHoursIn] = Field(max_length=7)

    @field_validator("days")
    @classmethod
    def _one_entry_per_weekday(cls, value: list[DayHoursIn]) -> list[DayHoursIn]:
        """Refuse a payload that names the same weekday twice."""
        weekdays = [day.weekday for day in value]
        if len(weekdays) != len(set(weekdays)):
            raise ValueError("Each weekday may appear at most once.")
        return sorted(value, key=lambda day: day.weekday)


class TimeSpanOut(BaseModel):
    """One stretch of a clinic's day, as the API returns it."""

    opens_at: time
    closes_at: time
    #: True when the span runs past midnight into the next morning.
    crosses_midnight: bool


class DayHoursOut(BaseModel):
    """One weekday's spans, as the API returns them."""

    weekday: int
    spans: list[TimeSpanOut]


class WeeklyHoursOut(BaseModel):
    """A clinic's ordinary week: all seven days, so a caller never has to infer a missing one."""

    site_id: str
    days: list[DayHoursOut]


class HolidayRuleIn(BaseModel):
    """What a clinic does on one public holiday: closed, or open for these hours."""

    opens_at: time | None = None
    closes_at: time | None = None

    @model_validator(mode="after")
    def _both_or_neither(self) -> HolidayRuleIn:
        """Half a rule is not a rule: either the clinic opens, with both times, or it is closed.

        A ``model_validator`` rather than a ``field_validator`` on ``closes_at``, because a field
        validator does not run for a field the payload omitted — which is exactly the case this
        refuses (``opens_at`` given, ``closes_at`` left out).
        """
        if (self.opens_at is None) != (self.closes_at is None):
            raise ValueError(
                "Give both opens_at and closes_at to open on this holiday, or neither to close."
            )
        return self


class HolidayOut(BaseModel):
    """One public holiday, and what this clinic does on it."""

    holiday_date: date
    name: str
    #: Set when the holiday exists only because the one it observes fell on a Sunday.
    observed_for: str | None
    is_open: bool
    opens_at: time | None
    closes_at: time | None
    #: False when the clinic has written no rule, so it is closed by the safe default.
    has_rule: bool


class HolidayListOut(BaseModel):
    """A clinic's holiday calendar for the window asked about, in date order."""

    site_id: str
    items: list[HolidayOut]


class ClosureIn(BaseModel):
    """An ad-hoc closure a clinic manager announces."""

    reason: str = Field(min_length=3, max_length=MAX_CLOSURE_REASON)
    """Shown to every patient holding a ticket, in the manager's own words."""
    starts_at: datetime | None = None
    """``None`` means now: the commonest case, because something has just gone wrong."""
    ends_at: datetime | None = None
    """``None`` means "until further notice", which a manager has to lift by hand."""


class ClosureOut(BaseModel):
    """One closure as the API returns it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    site_id: str
    reason: str
    starts_at: datetime
    ends_at: datetime | None
    lifted_at: datetime | None
    created_at: datetime


class ClosureListOut(BaseModel):
    """A clinic's closures, most recent first."""

    total: int = Field(ge=0)
    items: list[ClosureOut]


class OpenStateOut(BaseModel):
    """Whether a clinic is open right now, when it opens next, and whether joins are accepted.

    One payload rather than three endpoints, because discovery, the board and every channel menu
    ask all three questions at once and must never show a half-updated answer.
    """

    site_id: str
    is_open: bool
    #: ``Africa/Johannesburg``. ``None`` means it does not open again within the horizon.
    next_open_at: datetime | None
    #: The manager's own words when an ad-hoc closure is what is keeping it shut.
    closure_reason: str | None
    #: The single server-side answer every channel reads (Issue 24).
    accepting_joins: bool
    #: What a patient is told when ``accepting_joins`` is false.
    refusal: str | None


# --------------------------------------------------------------------------------------
# Display and privacy settings (Issue 27, non-negotiable 4)
# --------------------------------------------------------------------------------------


class DisplaySettingsIn(BaseModel):
    """What a clinic manager may set about its waiting-room board.

    The two ``confirm_`` fields are not decoration. The server refuses a change that newly puts
    something about a patient on a public screen unless the request says so explicitly, so a
    manager who has not seen the warning cannot agree to it by accident and an API client cannot
    skip it by not rendering one.
    """

    display_mode: DisplayMode
    display_show_comment: bool = False
    board_language: BoardLanguage = SITE_DEFAULT_BOARD_LANGUAGE
    announce_audio: bool = True
    reason_retention_days: int = Field(
        default=REASON_RETENTION_DEFAULT_DAYS,
        ge=REASON_RETENTION_FLOOR_DAYS,
        le=REASON_RETENTION_CEILING_DAYS,
    )
    """Validated against the platform's retention ceiling (:mod:`src.modules.sites.settings`),
    which a clinic cannot raise."""

    confirm_public_display: bool = False
    """"I understand what will appear on the screen." Required to reveal a name or a comment."""
    confirm_comment_with_full_name: bool = False
    """A second, separate agreement: a reason beside a **full** name is the sharpest combination."""


class DisplaySettingsOut(BaseModel):
    """A clinic's display settings, with the warning that describes what they mean."""

    site_id: str
    display_mode: DisplayMode
    display_show_comment: bool
    board_language: BoardLanguage
    announce_audio: bool
    reason_retention_days: int
    #: The ceiling a clinic cannot raise, echoed so a screen can show it without hardcoding it.
    reason_retention_ceiling_days: int
    #: What these settings put on the screen, in plain language.
    warning_lines: list[str]


class DisplayModeOptionOut(BaseModel):
    """One display mode a clinic may choose, and what choosing it would mean."""

    value: DisplayMode
    #: The plain-language description of the screen, from :mod:`src.modules.sites.settings`.
    warning: str
    requires_confirmation: bool


class DisplayOptionsOut(BaseModel):
    """Everything a settings screen needs to render itself without hardcoding a rule.

    Served so that the warning a manager reads and the rule the server enforces are the same text,
    read from the same place — the failure this prevents is a screen that reassures somebody about
    a setting the server treats differently.
    """

    modes: list[DisplayModeOptionOut]
    languages: list[BoardLanguage]
    retention_floor_days: int
    retention_ceiling_days: int
    comment_warning: str
    comment_with_full_name_warning: str


# --------------------------------------------------------------------------------------
# The services catalogue (Issue 26)
# --------------------------------------------------------------------------------------


class ClinicServiceIn(BaseModel):
    """What a clinic manager may set about one service it offers."""

    name: str = Field(min_length=2, max_length=120)
    slug: str = Field(min_length=2, max_length=80, pattern=SLUG_PATTERN)
    category: ServiceCategory = ServiceCategory.OTHER
    description: str | None = Field(default=None, max_length=500)
    expected_minutes: int = Field(
        default=15, ge=MIN_EXPECTED_MINUTES, le=MAX_EXPECTED_MINUTES
    )
    """Validated to a sensible range: this is the wait estimator's prior (Issue 42), so zero would
    make an estimate divide by nothing and a value in hours would report a wait in days."""
    display_order: int = Field(default=0, ge=0, le=999)
    requires_appointment: bool = False
    queue_ids: list[str] = Field(default_factory=list, max_length=50)
    """Which of this clinic's queues handle it. Optional, and another clinic's id is dropped."""
    is_active: bool = True


class ClinicServiceOut(BaseModel):
    """One service as the API returns it."""

    id: str
    site_id: str
    name: str
    slug: str
    category: ServiceCategory
    description: str | None
    expected_minutes: int
    display_order: int
    requires_appointment: bool
    is_active: bool
    queue_ids: list[str]
    created_at: datetime
    modified_at: datetime


class ClinicServiceListOut(BaseModel):
    """A clinic's catalogue, in the order the clinic put it in."""

    site_id: str
    total: int = Field(ge=0)
    items: list[ClinicServiceOut]
