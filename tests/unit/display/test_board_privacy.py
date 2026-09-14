"""The board is given the privacy projection and nothing else (Issue 58, non-negotiable 4).

    Enforced by: Issues 27 and 58, plus a guard test that fails if a template receives raw ticket data.

Three kinds of check, each with a fixture proving it can fail (a guard that cannot fail passes forever):

* **At runtime, the template door.** :func:`~src.web.display.board_template_response` checks its whole
  context before it looks the template up. A ``Ticket``, a ``Patient`` or a ``Site`` anywhere in it,
  however deeply nested, raises :class:`UnprojectedBoardDataError` and nothing is rendered.
* **At runtime, the payload lock.** :meth:`BoardState.payload` refuses to serialise a personal key the
  display mode forbids, so a projection bug is an error, never a name on a screen.
* **At runtime, the stream door.** :func:`~src.modules.display.board_state.board_event` checks the
  whole event it builds, so a stream handed a ticket raises instead of sending it (Issue 57).
* **In the source.** ``displayed_select`` (the only query helper that reaches a board's tickets) and
  ``BoardTicket(...)`` appear only in the projection module; a ``display/`` template is rendered only
  through ``board_template_response``; the board stream formats only ``state_event``, ``board_event``
  and heartbeats; and the projection reads consent through ``board_projection``, which asks
  ``has_consent``.

No HTML is read: the template door refuses before anything renders
(``docs/IDE/RULES/testing-strategy.mdc``).
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from src.commons.enums import (
    BoardLanguage,
    BoardTheme,
    DisplayMode,
    LiveEventType,
    TicketStatus,
)
from src.commons.time import APP_TIMEZONE
from src.core.live_events import LiveEvent
from src.database.models import Patient, Site, Ticket
from src.modules.display.board_state import board_event
from src.modules.display.projection import (
    BoardPrivacyError,
    BoardQueue,
    BoardState,
    BoardTicket,
    UnprojectedBoardDataError,
    ensure_projected,
)
from src.web.display import board_template_response

_SRC = Path(__file__).resolve().parents[3] / "src"
_PROJECTION = _SRC / "modules" / "display" / "projection.py"
_BOARD_TEMPLATE = "display/board.html"


def _request() -> Request:
    """A bare GET request, enough for a template response to be attempted."""
    return Request({"type": "http", "method": "GET", "path": "/display", "headers": []})


def _state(*, mode: DisplayMode, tickets: tuple[BoardTicket, ...]) -> BoardState:
    """A one-queue board in ``mode`` with ``tickets`` waiting."""
    return BoardState(
        site_id="site",
        clinic_name="Zola Clinic",
        display_mode=mode,
        language=BoardLanguage.ENGLISH,
        theme=BoardTheme.DIM,
        announce_audio=True,
        announce_volume=80,
        as_of=datetime(2026, 9, 14, 9, 0, tzinfo=APP_TIMEZONE),
        queues=(
            BoardQueue(
                id="q",
                label="Triage",
                room="Room 2",
                now_serving=(),
                up_next=tickets,
                waiting=len(tickets),
            ),
        ),
    )


# --- the template door ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(Ticket(number="A001", reason_text="Chest pain"), id="ticket"),
        pytest.param(Patient(display_name="Thabo Mokoena"), id="patient"),
        pytest.param(Site(name="Zola Clinic"), id="site"),
        pytest.param([{"row": Ticket(number="A001")}], id="ticket-nested-in-a-list"),
        pytest.param(SimpleNamespace(name="Thabo Mokoena"), id="any-other-object"),
    ],
)
def test_a_board_template_handed_raw_data_refuses_before_rendering(raw: object) -> None:
    """The guard the non-negotiable names: raw ticket data given to a template is an error."""
    with pytest.raises(UnprojectedBoardDataError, match="privacy projection"):
        board_template_response(_request(), _BOARD_TEMPLATE, {"board": raw})


def test_the_error_names_where_the_raw_data_was_found() -> None:
    """A reviewer reading the failure learns which key held it."""
    with pytest.raises(UnprojectedBoardDataError, match=r"board\['queues'\]\[0\]"):
        ensure_projected({"queues": [Ticket(number="A001")]})


def test_projected_data_and_plain_values_pass_the_door() -> None:
    """The projection's own dataclasses, enum members and text are exactly what a board is for."""
    ensure_projected(
        {
            "board": _state(mode=DisplayMode.NUMBER_ONLY, tickets=()),
            "mode": DisplayMode.NUMBER_ONLY,
            "title": "Zola Clinic",
            "limits": (3, 5),
        }
    )


def test_a_board_stream_handed_raw_data_refuses_before_sending() -> None:
    """The stream's guard: an event built from anything but the projection is an error, not a message."""
    called = LiveEvent(LiveEventType.TICKET_CALLED, "site", queue_id="q")
    with pytest.raises(UnprojectedBoardDataError, match=r"stream\['board'\]"):
        board_event(called, {"queues": [Ticket(number="A001")]})  # type: ignore[dict-item]
    sent = board_event(
        called, _state(mode=DisplayMode.NUMBER_ONLY, tickets=()).payload()
    )
    assert set(sent.payload()) == {"type", "site_id", "at", "queue_id", "board"}


# --- the payload lock ----------------------------------------------------------------------


def test_a_number_only_payload_with_a_name_in_it_is_refused_not_sent() -> None:
    """If the projection ever put a name on a number-only board, serialising it fails."""
    leaked = _state(
        mode=DisplayMode.NUMBER_ONLY,
        tickets=(BoardTicket("A001", TicketStatus.WAITING, name="Thabo M."),),
    )
    with pytest.raises(
        BoardPrivacyError, match=r"number_only board payload carries \['name'\]"
    ):
        leaked.payload()


def test_a_name_lite_payload_with_a_reason_in_it_is_refused_not_sent() -> None:
    """A reason is a full-mode field only."""
    leaked = _state(
        mode=DisplayMode.NAME_LITE,
        tickets=(
            BoardTicket("A001", TicketStatus.WAITING, name="Thabo M.", comment="Rash"),
        ),
    )
    with pytest.raises(BoardPrivacyError, match=r"\['comment'\]"):
        leaked.payload()


def test_a_number_only_payload_has_no_personal_key_at_all() -> None:
    """Absent, not ``null``: the key itself is missing."""
    body = _state(
        mode=DisplayMode.NUMBER_ONLY,
        tickets=(BoardTicket("A001", TicketStatus.WAITING),),
    ).payload()
    assert body["queues"][0]["up_next"] == [{"number": "A001", "status": "waiting"}]


# --- the source ----------------------------------------------------------------------------


def _python_sources() -> list[Path]:
    """Every application source file."""
    return [p for p in sorted(_SRC.rglob("*.py")) if "__pycache__" not in p.parts]


def _call_name(node: ast.Call) -> str:
    """The called function's name: ``f`` for ``f(…)`` and ``obj.f(…)``."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def calls_outside(source: str, name: str) -> list[int]:
    """The lines of every call to ``name`` in ``source``."""
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and _call_name(node) == name
    ]


def display_templates_rendered_directly(source: str) -> list[int]:
    """Lines where a ``display/`` template name is passed to anything but the guarded renderer."""
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if (
            not isinstance(node, ast.Call)
            or _call_name(node) == "board_template_response"
        ):
            continue
        for argument in node.args:
            if (
                isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
                and argument.value.startswith("display/")
                and argument.value.endswith(".html")
            ):
                found.append(node.lineno)
    return found


def test_only_the_projection_reads_a_boards_tickets_or_builds_a_board_ticket() -> None:
    """``displayed_select`` and ``BoardTicket(...)`` belong to the projection module alone."""
    offenders = [
        f"{path.relative_to(_SRC.parent)}:{line} calls {name}"
        for path in _python_sources()
        if path != _PROJECTION and path.name != "site_scope.py"
        for name in ("displayed_select", "BoardTicket")
        for line in calls_outside(path.read_text(encoding="utf-8"), name)
    ]
    assert offenders == [], (
        "a board's tickets are read, and its entries built, only in "
        f"src/modules/display/projection.py (non-negotiable 4): {offenders}"
    )


def test_a_display_template_is_rendered_only_through_the_guarded_renderer() -> None:
    """A second way to render a board template would be a way past the context check."""
    offenders = [
        f"{path.relative_to(_SRC.parent)}:{line}"
        for path in _python_sources()
        for line in display_templates_rendered_directly(
            path.read_text(encoding="utf-8")
        )
    ]
    assert offenders == [], (
        f"render display/ templates with board_template_response: {offenders}"
    )


#: What the board stream may format: the full board, a change with its board, or a bare heartbeat.
_STREAM_BUILDERS = frozenset({"state_event", "board_event"})


def stream_sends_unprojected(source: str) -> list[int]:
    """Lines where a board stream formats something other than a projected event or a heartbeat.

    ``format_event(state_event(...))`` and ``format_event(board_event(...))`` are projected. A bare
    event is allowed only inside the ``if event.type is LiveEventType.HEARTBEAT`` branch.
    """
    tree = ast.parse(source)
    heartbeat_calls: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "HEARTBEAT" in ast.unparse(node.test):
            heartbeat_calls.update(
                id(child) for stmt in node.body for child in ast.walk(stmt)
            )
    found: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) != "format_event":
            continue
        argument = node.args[0] if node.args else None
        projected = (
            isinstance(argument, ast.Call) and _call_name(argument) in _STREAM_BUILDERS
        )
        if not projected and id(node) not in heartbeat_calls:
            found.append(node.lineno)
    return found


def test_the_board_stream_sends_only_projected_events_and_heartbeats() -> None:
    """The stream route formats nothing a guard has not checked (Issue 57)."""
    stream = _SRC / "web" / "display_stream.py"
    assert stream_sends_unprojected(stream.read_text(encoding="utf-8")) == []
    bypass = (
        "async def body():\n"
        "    async for event in events:\n"
        "        rows = db.execute(select(Ticket)).all()\n"
        "        yield format_event(LiveEvent(event.type, site_id, extra={'rows': rows}), 1)\n"
    )
    assert stream_sends_unprojected(bypass) == [4]


def test_the_projection_asks_for_consent_on_every_shown_ticket() -> None:
    """``project_board`` goes through ``board_projection``, which reads ``has_consent`` afresh."""
    projection = ast.parse(_PROJECTION.read_text(encoding="utf-8"))
    assert "board_projection" in {
        _call_name(node) for node in ast.walk(projection) if isinstance(node, ast.Call)
    }
    consent = ast.parse(
        (_SRC / "modules" / "patients" / "consent.py").read_text(encoding="utf-8")
    )
    board_projection = next(
        node
        for node in ast.walk(consent)
        if isinstance(node, ast.FunctionDef) and node.name == "board_projection"
    )
    assert "has_consent" in {
        _call_name(node)
        for node in ast.walk(board_projection)
        if isinstance(node, ast.Call)
    }


def test_the_source_guards_fail_on_the_shapes_they_exist_to_catch() -> None:
    """A handler that renders a board template itself, or reads tickets for a board itself."""
    handler = (
        "def board(request, db, site_id):\n"
        '    """Render the board from the tickets."""\n'
        "    rows = db.execute(displayed_select(Ticket, site_id)).scalars().all()\n"
        "    return templates.TemplateResponse(request, 'display/board.html', {'rows': rows})\n"
    )
    assert calls_outside(handler, "displayed_select") == [3]
    assert display_templates_rendered_directly(handler) == [4]
    guarded = (
        "board_template_response(request, 'display/board.html', {'board': state})\n"
    )
    assert display_templates_rendered_directly(guarded) == []
