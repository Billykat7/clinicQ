"""Every priority and transfer reason has words for a staff screen (Issues 52 and 53)."""

from src.commons.enums import PriorityReason, TransferReason
from src.modules.queue.priority import PRIORITY_REASON_LABELS
from src.modules.queue.transfer import TRANSFER_REASON_LABELS


def test_every_priority_reason_has_a_label_and_no_label_is_a_wire_value() -> None:
    """A reason added to the enum must be given words before the reason prompt can offer it."""
    assert set(PRIORITY_REASON_LABELS) == set(PriorityReason)
    assert all(
        label and label != reason.value
        for reason, label in PRIORITY_REASON_LABELS.items()
    )


def test_every_transfer_reason_has_a_label_and_no_label_is_a_wire_value() -> None:
    """The room view's transfer prompt offers each reason in words, never ``wrong_queue``."""
    assert set(TRANSFER_REASON_LABELS) == set(TransferReason)
    assert all(
        label and label != reason.value
        for reason, label in TRANSFER_REASON_LABELS.items()
    )
