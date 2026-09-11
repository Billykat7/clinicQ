"""The Python half of the component library (Issue 5): the values the Jinja macros choose between.

The macros in ``src/templates/components/`` render markup; what a template may *pass* them is
decided here, as enums, so a template never compares a status with a string and the board, the
dashboard and the patient's ticket page show a ticket status the same way.

:func:`register_components` puts these on the Jinja environment as globals, once, when
``src.web.routes`` builds it.
"""

import json
from enum import StrEnum
from typing import Any, NamedTuple

from jinja2 import Environment

from src.commons.enums import TicketSource, TicketStatus


class BadgeTone(StrEnum):
    """The colour role of a badge; each value is a ``.badge-<tone>`` class in components.css."""

    OK = "ok"
    WARN = "warn"
    MUTED = "muted"
    CURRENT = "current"


class ButtonVariant(StrEnum):
    """A button's weight; each value but ``PRIMARY`` is a ``.btn-<variant>`` class."""

    PRIMARY = "primary"
    QUIET = "quiet"
    DANGER = "danger"


class ToastKind(StrEnum):
    """A toast's tone; ``ui-feedback.js`` styles ``info`` plainly, ``ok`` green, ``error`` red."""

    INFO = "info"
    OK = "ok"
    ERROR = "error"


class StatusBadge(NamedTuple):
    """How one ticket status is shown: the words a person reads and the badge's tone."""

    label: str
    tone: BadgeTone


#: Every ticket status, as every surface shows it. Words for a patient at a clinic, not the wire
#: value: "No-show", not ``no_show``. A status added to the enum without an entry here fails
#: ``tests/unit/platform/test_ui_shell.py``, so no surface can meet one it cannot draw.
TICKET_STATUS_BADGES: dict[TicketStatus, StatusBadge] = {
    TicketStatus.WAITING: StatusBadge("Waiting", BadgeTone.MUTED),
    TicketStatus.CALLED: StatusBadge("Called", BadgeTone.CURRENT),
    TicketStatus.RECALLED: StatusBadge("Called again", BadgeTone.WARN),
    TicketStatus.IN_PROGRESS: StatusBadge("Being seen", BadgeTone.OK),
    TicketStatus.DONE: StatusBadge("Done", BadgeTone.OK),
    TicketStatus.NO_SHOW: StatusBadge("No-show", BadgeTone.WARN),
    TicketStatus.CANCELLED: StatusBadge("Cancelled", BadgeTone.MUTED),
    TicketStatus.TRANSFERRED: StatusBadge("Transferred", BadgeTone.MUTED),
}

#: How each channel is named on screen. A source added to the enum without a label here fails
#: ``tests/unit/platform/test_ui_shell.py``.
TICKET_SOURCE_LABELS: dict[TicketSource, str] = {
    TicketSource.WEB: "Web",
    TicketSource.USSD: "USSD",
    TicketSource.WHATSAPP: "WhatsApp",
    TicketSource.WALK_IN: "Walk-in",
}

#: The event ``ui-feedback.js`` listens for to show a toast raised by the server.
TOAST_EVENT = "bkp:toast"


def toast_trigger(message: str, kind: ToastKind = ToastKind.INFO) -> dict[str, str]:
    """Return the ``HX-Trigger`` header that shows a toast when an htmx response lands.

    htmx fires the named event on the element that made the request; ``ui-feedback.js`` catches it
    as it bubbles and calls ``BKP.toast``. Merge the result into the response's headers.

    Args:
        message: What the toast says, as plain text (it is never parsed as HTML).
        kind: The toast's tone.

    Returns:
        ``{"HX-Trigger": "<json>"}``.
    """
    payload = {TOAST_EVENT: {"message": message, "kind": kind.value}}
    return {"HX-Trigger": json.dumps(payload)}


def register_components(env: Environment) -> None:
    """Expose the component vocabulary to every template as Jinja globals."""
    globals_: dict[str, Any] = {
        "BadgeTone": BadgeTone,
        "ButtonVariant": ButtonVariant,
        "ToastKind": ToastKind,
        "TicketStatus": TicketStatus,
        "TICKET_STATUS_BADGES": TICKET_STATUS_BADGES,
        "TICKET_SOURCE_LABELS": TICKET_SOURCE_LABELS,
    }
    env.globals.update(globals_)
