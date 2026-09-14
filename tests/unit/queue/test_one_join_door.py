"""Non-negotiable 1, enforced: nothing issues a ticket except ``join_queue()`` (Issue 40).

"Every channel calls one function" is only true while nobody adds a second door: a USSD handler that
allocates its own number, a board shortcut that builds a ``Ticket``, an import script that inserts
rows. So this reads ``src/`` and fails, naming the file and line, on:

* a call to :func:`~src.modules.queue.sequence.issue_ticket` or
  :func:`~src.modules.queue.sequence.allocate_sequence` from anywhere but the functions listed in
  :data:`ALLOWED_CALLERS`;
* a ``Ticket(...)`` built anywhere but :mod:`src.modules.queue.sequence`.

The fixtures at the bottom prove it fails on both shapes. Reads source, never rendered output.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "src"
#: The storage primitives a ticket's number comes from.
_ISSUING = frozenset({"issue_ticket", "allocate_sequence"})
#: Where they may be called from, as ``path::function``, each with the reason. Closed.
ALLOWED_CALLERS: dict[str, str] = {
    "modules/queue/service.py::join_queue": "the one join service every channel calls (Issue 40)",
    "modules/queue/sequence.py::issue_ticket": "issue_ticket allocates through allocate_sequence",
}
#: The one module that constructs a ticket row.
_CONSTRUCTOR_HOME = "modules/queue/sequence.py"


def findings_in_source(source: str, *, path: str) -> list[str]:
    """Every second door in one module, as readable lines."""
    tree = ast.parse(source)
    out: list[str] = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if name in _ISSUING and f"{path}::{function.name}" not in ALLOWED_CALLERS:
                out.append(
                    f"src/{path}:{node.lineno} calls {name} in {function.name}(); tickets are "
                    "issued only by join_queue() (non-negotiable 1)"
                )
            if name == "Ticket" and path != _CONSTRUCTOR_HOME:
                out.append(
                    f"src/{path}:{node.lineno} builds a Ticket in {function.name}(); only "
                    "sequence.issue_ticket does, behind join_queue()"
                )
    return out


def test_nothing_but_the_join_service_issues_a_ticket() -> None:
    """The standing rule over the whole application."""
    findings: list[str] = []
    for file in sorted(_SRC.rglob("*.py")):
        if "__pycache__" in file.parts:
            continue
        relative = file.relative_to(_SRC).as_posix()
        findings += findings_in_source(file.read_text(encoding="utf-8"), path=relative)
    assert not findings, "a second way to issue a ticket:\n" + "\n".join(findings)


def test_every_allowed_caller_still_exists() -> None:
    """An entry for a function that is gone would be an exemption waiting for a new owner."""
    for key in ALLOWED_CALLERS:
        path, function = key.split("::")
        tree = ast.parse((_SRC / path).read_text(encoding="utf-8"))
        assert any(
            isinstance(node, ast.FunctionDef) and node.name == function
            for node in ast.walk(tree)
        ), key


def test_the_guard_fails_on_a_channel_that_issues_its_own_ticket() -> None:
    """A USSD handler allocating a number itself is exactly the second door."""
    handler = (
        "def on_ussd_join(db, queue, patient):\n"
        "    return issue_ticket(db, queue=queue, source=TicketSource.USSD, patient_id=patient.id)\n"
    )
    assert findings_in_source(handler, path="modules/channels/ussd.py")


def test_the_guard_fails_on_a_ticket_built_by_hand() -> None:
    """So is a route that builds the row."""
    route = (
        "def quick_walk_in(db, queue):\n"
        "    db.add(Ticket(queue_id=queue.id, sequence=1, number='A001'))\n"
    )
    assert findings_in_source(route, path="modules/queue/router.py")


def test_the_guard_passes_the_join_service_itself() -> None:
    """And it lets the one door through, so it is discriminating rather than always red."""
    service = "def join_queue(db, queue):\n    return issue_ticket(db, queue=queue)\n"
    assert findings_in_source(service, path="modules/queue/service.py") == []
