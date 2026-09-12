"""Searching from a suburb instead of a position (Issue 34).

**This is how a feature-phone patient finds a clinic at all.** A USSD session has no GPS; neither
does a patient who declined the browser's location prompt, or whose old handset cannot get a fix
indoors. Each of them types a place name instead, and every channel (the web page, USSD and
WhatsApp) calls the same two functions here: :func:`search_areas` to turn what was typed into a
place, and :func:`~src.modules.discovery.service.find_nearby_sites` with an
:class:`~src.modules.discovery.service.AreaOrigin` to search from that place's centroid. Nothing in
a channel adapter matches place names itself; ``tests/unit/discovery/test_one_area_search.py`` fails
the build if anything outside this module queries ``area_name``.

**Forgiving on purpose.** People type the way they say a name, on a keypad, often in a hurry:
"Soweeto", "Tembisa" for Thembisa, "joburg". The stored names and the query are folded the same way
(:func:`~src.commons.search.fold_for_search`) and compared by trigram similarity under a GIN index,
and alternative names are rows of their own, so an old or everyday name finds the place as well as
the official one. A prefix match ranks above a fuzzy one, so the typeahead settles on the right
place after three or four letters.

**Distances from here are approximate**, because the patient may live at the edge of the suburb
rather than its middle. The search result says so (:class:`~src.commons.enums.DistanceBasis`), and
:func:`src.modules.discovery.wording.distance_label` writes it into the words every surface shows.

**Recently used areas** are remembered per patient (:func:`remember_area`), so choosing one again is
one tap on the web or one digit on USSD. They are suburbs, never positions.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import Float, and_, case, cast, delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.commons.enums import AreaKind, SaProvince
from src.commons.geo import Coordinates
from src.commons.search import fold_for_search
from src.commons.time import now_sast
from src.database.models import Area, AreaName, PatientRecentArea

#: Fewer folded characters than this is not a search: "s" matches half the province.
MIN_QUERY_LENGTH: Final = 2
#: How many suggestions the typeahead offers: a phone screen, or a USSD menu of single digits.
DEFAULT_SUGGESTIONS: Final = 8
#: The most a caller may ask for.
MAX_SUGGESTIONS: Final = 20
#: How similar a folded name must be to count as a fuzzy match. ``pg_trgm``'s own default: "soweeto"
#: scores 0.67 against "soweto", "tembisa" 0.55 against "thembisa", and unrelated names stay below.
SIMILARITY_THRESHOLD: Final = 0.3
#: How many recently used areas are kept per patient.
RECENT_AREAS_KEPT: Final = 5

#: Bigger places first among equally good matches: "Soweto" the city before a "Soweto" street
#: settlement, if the dataset ever holds both.
_KIND_RANK: Final[dict[AreaKind, int]] = {
    AreaKind.CITY: 0,
    AreaKind.TOWN: 1,
    AreaKind.SUBURB: 2,
    AreaKind.NEIGHBOURHOOD: 3,
    AreaKind.VILLAGE: 4,
}


class AreaNotFoundError(LookupError):
    """No area has this id. Answered as 404 by the API."""


@dataclass(frozen=True, slots=True)
class AreaSummary:
    """One place a search can start from, as every channel shows it.

    ``matched_name`` is the spelling the patient's text matched, which differs from ``name`` when an
    alternative did ("Tembisa" finding Thembisa), so a surface can say "Thembisa (Tembisa)".
    """

    area_id: str
    name: str
    kind: AreaKind
    municipality: str | None
    province: SaProvince
    centroid: Coordinates
    matched_name: str | None = None

    @property
    def label(self) -> str:
        """The name and its municipality, which is enough to tell same-named places apart."""
        return f"{self.name}, {self.municipality}" if self.municipality else self.name


def _summary(area: Area, matched_name: str | None = None) -> AreaSummary:
    """The plain-data view of one stored area."""
    return AreaSummary(
        area_id=area.id,
        name=area.name,
        kind=area.kind_enum,
        municipality=area.municipality,
        province=area.province_enum,
        centroid=area.centroid,
        matched_name=matched_name,
    )


def search_areas(
    db: Session,
    text: str,
    *,
    limit: int = DEFAULT_SUGGESTIONS,
    province: SaProvince | None = None,
) -> list[AreaSummary]:
    """Suggest the places ``text`` most likely means, best first.

    The one place-name search every channel calls. **PostgreSQL only** (``pg_trgm``).

    Ranking, in order: an exact match of a name, then a name that starts with what was typed, then
    trigram similarity; among equals, a primary name before an alternative, a bigger place before a
    smaller one, and then the name.

    Args:
        db: The session.
        text: What the patient typed, as typed.
        limit: How many suggestions, clamped to ``1..MAX_SUGGESTIONS``.
        province: Restrict to one province.

    Returns:
        Up to ``limit`` distinct places. Empty when the folded text is shorter than
        :data:`MIN_QUERY_LENGTH`.
    """
    key = fold_for_search(text)
    if len(key) < MIN_QUERY_LENGTH:
        return []
    limit = min(MAX_SUGGESTIONS, max(1, limit))
    prefix = f"{key}%"
    similarity = cast(func.similarity(AreaName.search_key, key), Float)
    exact = case((AreaName.search_key == key, 1), else_=0)
    starts = case((AreaName.search_key.like(prefix), 1), else_=0)

    # The best-scoring name per area, so an area matched by two of its names appears once.
    best_per_area = (
        select(
            AreaName.area_id,
            AreaName.name.label("matched_name"),
            AreaName.is_primary,
            exact.label("exact"),
            starts.label("starts"),
            similarity.label("similarity"),
        )
        .where(AreaName.search_key.op("%")(key) | AreaName.search_key.like(prefix))
        .distinct(AreaName.area_id)
        .order_by(
            AreaName.area_id,
            exact.desc(),
            starts.desc(),
            similarity.desc(),
            AreaName.is_primary.desc(),
        )
        .subquery()
    )
    kind_rank = case(
        *((Area.kind == kind.value, rank) for kind, rank in _KIND_RANK.items()),
        else_=len(_KIND_RANK),
    )
    statement = (
        select(Area, best_per_area.c.matched_name)
        .join(best_per_area, best_per_area.c.area_id == Area.id)
        .order_by(
            best_per_area.c.exact.desc(),
            best_per_area.c.starts.desc(),
            best_per_area.c.similarity.desc(),
            best_per_area.c.is_primary.desc(),
            kind_rank,
            Area.name,
        )
        .limit(limit)
    )
    if province is not None:
        statement = statement.where(Area.province == province.value)

    # ``%`` compares against pg_trgm.similarity_threshold; set it for this transaction only, so the
    # threshold is this module's constant rather than whatever the server was configured with.
    db.execute(
        select(
            func.set_config(
                "pg_trgm.similarity_threshold", literal(str(SIMILARITY_THRESHOLD)), True
            )
        )
    )
    return [
        _summary(area, matched_name if matched_name != area.name else None)
        for area, matched_name in db.execute(statement).all()
    ]


def get_area(db: Session, area_id: str) -> AreaSummary:
    """One area by id.

    Raises:
        AreaNotFoundError: If there is no such area.
    """
    area = db.get(Area, area_id)
    if area is None:
        raise AreaNotFoundError(f"No area has the id {area_id!r}.")
    return _summary(area)


def remember_area(
    db: Session, patient_id: str, area_id: str, *, moment: datetime | None = None
) -> None:
    """Record that a patient searched from ``area_id``, keeping only their most recent few.

    Idempotent per area: searching from Soweto twice moves it to the top rather than listing it
    twice. The caller commits.

    Raises:
        AreaNotFoundError: If there is no such area.
    """
    get_area(db, area_id)
    moment = moment or now_sast()
    upsert = pg_insert(PatientRecentArea).values(
        patient_id=patient_id, area_id=area_id, used_at=moment
    )
    db.execute(
        upsert.on_conflict_do_update(
            constraint="uq_patient_recent_area_patient_id",
            set_={"used_at": moment},
        )
    )
    keep = (
        select(PatientRecentArea.id)
        .where(PatientRecentArea.patient_id == patient_id)
        .order_by(PatientRecentArea.used_at.desc())
        .limit(RECENT_AREAS_KEPT)
    )
    db.execute(
        delete(PatientRecentArea).where(
            and_(
                PatientRecentArea.patient_id == patient_id,
                PatientRecentArea.id.not_in(keep.scalar_subquery()),
            )
        )
    )


def recent_areas(db: Session, patient_id: str) -> Sequence[AreaSummary]:
    """The areas a patient searched from most recently, newest first.

    Each carries its ``area_id``, which is all :func:`~src.modules.discovery.service.find_nearby_sites`
    needs, so choosing one is a single interaction on every channel.
    """
    rows = db.execute(
        select(Area)
        .join(PatientRecentArea, PatientRecentArea.area_id == Area.id)
        .where(PatientRecentArea.patient_id == patient_id)
        .order_by(PatientRecentArea.used_at.desc())
        .limit(RECENT_AREAS_KEPT)
    ).scalars()
    return [_summary(area) for area in rows]
