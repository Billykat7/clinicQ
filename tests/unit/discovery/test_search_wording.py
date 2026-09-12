"""The folding a place name is matched in, and the words a distance is shown in (Issue 34).

Both are pure and both are promises: the folding is frozen into migration ``0014``'s stored keys, and
the label is the one place "approximate" is written for every channel.
"""

import pytest

from src.commons.enums import DistanceBasis
from src.commons.search import MAX_SEARCH_KEY_LENGTH, fold_for_search
from src.modules.discovery.wording import distance_label


@pytest.mark.parametrize(
    ("typed", "folded"),
    [
        ("Soweto", "soweto"),
        ("  SOWETO ", "soweto"),
        ("Kwa-Thema", "kwathema"),
        ("KwaThéma", "kwathema"),
        ("Botha's Hill", "bothashill"),
        ("Umdloti / eMdloti", "umdlotiemdloti"),
        ("Bartlett Ext 20", "bartlettext20"),
        ("", ""),
    ],
)
def test_a_name_folds_to_lowercase_ascii_letters_and_digits(
    typed: str, folded: str
) -> None:
    """Accents, case, spaces and punctuation never decide whether two spellings match."""
    assert fold_for_search(typed) == folded


def test_a_folded_key_never_exceeds_the_column() -> None:
    """``area_name.search_key`` is 160 characters; the folding cannot overflow it."""
    assert len(fold_for_search("a" * 500)) == MAX_SEARCH_KEY_LENGTH


@pytest.mark.parametrize(
    ("metres", "basis", "area", "label"),
    [
        (1382, DistanceBasis.POSITION, None, "1.4 km"),
        (347, DistanceBasis.POSITION, None, "350 m"),
        (10, DistanceBasis.POSITION, None, "50 m"),
        (
            3950,
            DistanceBasis.AREA_CENTROID,
            "Soweto",
            "about 4.0 km from the middle of Soweto",
        ),
        (
            400,
            DistanceBasis.AREA_CENTROID,
            "Hillbrow",
            "about 400 m from the middle of Hillbrow",
        ),
        (3950, DistanceBasis.AREA_CENTROID, None, "about 4.0 km"),
    ],
)
def test_a_distance_from_an_area_is_never_shown_as_a_bare_figure(
    metres: int, basis: DistanceBasis, area: str | None, label: str
) -> None:
    """Exact from a position; "about", and from where, from an area's centroid."""
    assert distance_label(metres, basis, area) == label
