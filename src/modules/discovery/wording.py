"""The words a distance is shown in, written once for every channel (Issue 34).

"Distances from an area centroid are labelled as approximate everywhere they appear" is only true if
there is one place that writes the label. The web list, the detail page, the USSD menu and the
WhatsApp reply all call :func:`distance_label`, so none of them can show "3.9 km" for a distance that
was measured from the middle of Soweto rather than from the patient.

Plain text, not markup: a USSD menu cannot render anything else, and a template escapes it.
"""

from typing import Final

from src.commons.enums import DistanceBasis

#: Below this, metres; at and above it, kilometres with one decimal.
_KILOMETRE: Final = 1000
#: Metres are rounded to this step: "350 m", never "347 m", which claims a precision nobody has.
_METRE_STEP: Final = 50


def _amount(distance_m: int) -> str:
    """``350 m`` or ``3.9 km``."""
    if distance_m < _KILOMETRE:
        rounded = max(_METRE_STEP, round(distance_m / _METRE_STEP) * _METRE_STEP)
        return f"{rounded} m"
    return f"{distance_m / _KILOMETRE:.1f} km"


def distance_label(
    distance_m: int, basis: DistanceBasis, area_name: str | None = None
) -> str:
    """How far a clinic is, in words a patient reads: exact from a position, approximate otherwise.

    >>> distance_label(1382, DistanceBasis.POSITION)
    '1.4 km'
    >>> distance_label(3950, DistanceBasis.AREA_CENTROID, "Soweto")
    'about 4.0 km from the middle of Soweto'

    Args:
        distance_m: Straight-line distance in metres.
        basis: What it was measured from.
        area_name: The area, when ``basis`` is an area's centroid.

    Returns:
        The label. Never a bare figure when the distance is approximate.
    """
    amount = _amount(distance_m)
    if not basis.approximate:
        return amount
    if area_name:
        return f"about {amount} from the middle of {area_name}"
    return f"about {amount}"
