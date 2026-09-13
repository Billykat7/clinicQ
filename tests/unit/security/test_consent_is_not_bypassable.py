"""Nothing reaches a patient, or a public screen, without passing the consent check (Issue 21).

The criterion is "the board and notification services cannot bypass ``has_consent()``", and the way
to make that true is to leave exactly one door in each direction and then check, mechanically, that
there is no second one:

* **Messages.** Every send path resolves through
  :func:`src.modules.notifications.preferences.resolve`, which asks for consent. So this asserts
  that ``resolve`` still asks, that the send paths still resolve, and that nothing outside the
  notification module hands a message to a transport directly.
* **The board.** A public screen may show only what
  :func:`src.modules.patients.consent.board_projection` returns. Outside the ``patients`` module —
  which owns the record, and shows a patient their own name in their own session — nothing may read
  ``Patient.display_name`` at all, so a board handler cannot build its own row from a patient.

Each rule has a fixture proving it fails on the shape it exists to catch. Reads source, not
rendered output (``docs/IDE/RULES/testing-strategy.mdc``).
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "src"
_NOTIFICATIONS = _SRC / "modules" / "notifications"
_CONSENT = _SRC / "modules" / "patients" / "consent.py"
#: The module that owns the name: a patient's own session may see their own record there. Every
#: other part of the app gets a name only from ``board_projection``, which asks for consent.
_PATIENTS = _SRC / "modules" / "patients"

#: The functions that hand a message to a transport. Only the notification service may call them.
_TRANSPORT_CALLS = frozenset(
    {"deliver_smtp", "_deliver_sms_transport", "_deliver_email_transport"}
)
#: The send paths that must resolve preferences (and so consent) before they deliver.
_SEND_PATHS = ("deliver_email", "send_sms", "attempt")


def _function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """The named top-level function of a parsed module."""
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    raise AssertionError(f"{name} is gone: this guard needs rewriting, not deleting")


def _calls(node: ast.AST) -> set[str]:
    """Every function name called inside ``node``."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                names.add(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                names.add(child.func.attr)
    return names


def _reads_display_name(source: str) -> bool:
    """Whether ``source`` reads ``.display_name`` off anything."""
    return any(
        isinstance(node, ast.Attribute) and node.attr == "display_name"
        for node in ast.walk(ast.parse(source))
    )


# --- messages ----------------------------------------------------------------------------


def test_resolving_a_send_asks_for_consent() -> None:
    """``preferences.resolve`` is where consent is asked; if it stops asking, nothing else does."""
    tree = ast.parse((_NOTIFICATIONS / "preferences.py").read_text(encoding="utf-8"))
    assert "_patient_consent_denies" in _calls(_function(tree, "resolve"))
    assert "has_consent" in _calls(_function(tree, "_patient_consent_denies"))


def test_every_send_path_resolves_before_it_delivers() -> None:
    """A path that delivered without resolving would be a way past the consent check."""
    tree = ast.parse((_NOTIFICATIONS / "service.py").read_text(encoding="utf-8"))
    for name in _SEND_PATHS:
        calls = _calls(_function(tree, name))
        assert "resolve" in calls or "_safe_resolve" in calls, name


def test_nothing_outside_the_notification_service_touches_a_transport() -> None:
    """The transports are private to the service, so every message goes through the resolve above."""
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if "__pycache__" in path.parts or path.is_relative_to(_NOTIFICATIONS):
            continue
        calls = _calls(ast.parse(path.read_text(encoding="utf-8")))
        for transport in sorted(calls & _TRANSPORT_CALLS):
            offenders.append(f"{path.relative_to(_SRC.parent)} calls {transport}")
    assert not offenders, (
        "messages must go through the notification service:\n" + "\n".join(offenders)
    )


def test_the_guard_fails_on_a_send_path_that_skips_the_resolve() -> None:
    """A guard that cannot fail passes forever."""
    skipping = ast.parse(
        "def send_sms(db, to, template, context):\n"
        '    """Hand it straight to the gateway."""\n'
        "    return _deliver_sms_transport(to=to, message=context, provider=provider)\n"
    )
    calls = _calls(_function(skipping, "send_sms"))
    assert "resolve" not in calls and _TRANSPORT_CALLS & calls


# --- the board ---------------------------------------------------------------------------


def test_nothing_outside_the_patients_module_reads_a_patients_name() -> None:
    """A public screen may show only what ``board_projection`` returns, and it asks for consent."""
    readers = [
        str(path.relative_to(_SRC.parent))
        for path in sorted(_SRC.rglob("*.py"))
        if "__pycache__" not in path.parts
        and not path.is_relative_to(_PATIENTS)
        and _reads_display_name(path.read_text(encoding="utf-8"))
    ]
    assert readers == [], (
        "outside src/modules/patients, a patient's name comes from consent.board_projection, "
        f"which asks for consent first (Issue 21). Found: {readers}"
    )
    tree = ast.parse(_CONSENT.read_text(encoding="utf-8"))
    assert "has_consent" in _calls(_function(tree, "board_projection"))


def test_the_guard_fails_on_a_template_context_built_from_a_patient() -> None:
    """The shape it exists to catch: a board handler reading the name itself."""
    assert _reads_display_name(
        "def board(request, db, tickets):\n"
        '    """Render the waiting-room board."""\n'
        "    rows = [{'name': t.patient.display_name} for t in tickets]\n"
        "    return templates.TemplateResponse(request, 'board.html', {'rows': rows})\n"
    )
