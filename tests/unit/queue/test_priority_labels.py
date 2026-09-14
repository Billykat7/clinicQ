"""Every priority reason has words for a staff screen (Issue 52)."""

from src.commons.enums import PriorityReason
from src.modules.queue.priority import PRIORITY_REASON_LABELS


def test_every_priority_reason_has_a_label_and_no_label_is_a_wire_value() -> None:
    """A reason added to the enum must be given words before the reason prompt can offer it."""
    assert set(PRIORITY_REASON_LABELS) == set(PriorityReason)
    assert all(
        label and label != reason.value
        for reason, label in PRIORITY_REASON_LABELS.items()
    )
