"""No transport can dispatch a message without passing the send-time gate (Issue 67).

The gate is :func:`src.modules.notifications.preferences.resolve`: consent (Issue 21), then preferences,
quiet hours and opt-outs. It is only a gate if there is no way around it, so this reads the source
(``docs/IDE/RULES/testing-strategy.mdc``) and holds four things:

1. **A transport is defined only in** ``src/modules/notifications/transports/``: a class deriving from
   ``Transport`` anywhere else is a finding, because it would be a second door.
2. **A transport's ``send`` is called in exactly one place**, the service's ``_deliver_via_transport``, and
   **an SMS provider's** ``send``/``send_message`` **only by the SMS transport**. The one other ``provider.send``
   in the tree, the e-signature provider's, is named with its reason.
3. **``_deliver_via_transport`` is called only from** ``attempt``.
4. **Inside ``attempt``, the gate is asked before the transport is called**: the ``resolve`` call comes
   first in the function, and a suppressed decision returns before any delivery.

Each rule has a fixture proving it catches the shape it exists for: a transport that skips the gate fails
here (How to verify, step 3).
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "src"
_NOTIFICATIONS = _SRC / "modules" / "notifications"
_TRANSPORTS = _NOTIFICATIONS / "transports"
_SERVICE = _NOTIFICATIONS / "service.py"
_SMS_TRANSPORT = _TRANSPORTS / "sms.py"

#: Method names that hand a message to a transport or a gateway.
_DISPATCH = frozenset({"send", "send_message"})
#: The names a transport or a provider goes by where it is called.
_DISPATCHERS = frozenset({"transport", "provider", "self.provider"})


def _sources() -> list[Path]:
    return [
        path for path in sorted(_SRC.rglob("*.py")) if "__pycache__" not in path.parts
    ]


def _receiver(call: ast.Call) -> str:
    """``transport`` for ``transport.send(...)``, ``self.provider`` for ``self.provider.send(...)``."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return ""
    value = func.value
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
        return f"{value.value.id}.{value.attr}"
    return ""


def transports_defined_outside(source: str, path: Path) -> list[str]:
    """Classes deriving from ``Transport`` in ``source``, when ``path`` is not in the transports package."""
    if path.is_relative_to(_TRANSPORTS):
        return []
    return [
        f"{path.name}:{node.lineno} class {node.name}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ClassDef)
        and any(
            (isinstance(base, ast.Name) and base.id == "Transport")
            or (isinstance(base, ast.Attribute) and base.attr == "Transport")
            for base in node.bases
        )
    ]


def dispatch_calls(source: str) -> list[tuple[str, str, int]]:
    """``(enclosing function, receiver, line)`` for every ``<transport|provider>.send(...)`` in ``source``."""
    found: list[tuple[str, str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr in _DISPATCH
                and _receiver(child) in _DISPATCHERS
            ):
                found.append((node.name, _receiver(child), child.lineno))
    return found


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone: this guard needs rewriting, not deleting")


def _calls_named(node: ast.AST, name: str) -> list[int]:
    return sorted(
        child.lineno
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
        and (
            (isinstance(child.func, ast.Name) and child.func.id == name)
            or (isinstance(child.func, ast.Attribute) and child.func.attr == name)
        )
    )


# --- the rules, on the shipped tree -----------------------------------------------------------------------


def test_every_transport_lives_in_the_transports_package() -> None:
    findings = [
        finding
        for path in _sources()
        for finding in transports_defined_outside(
            path.read_text(encoding="utf-8"), path
        )
    ]
    assert findings == [], (
        f"a transport outside src/modules/notifications/transports: {findings}"
    )


def test_transports_and_providers_are_called_only_through_the_gate() -> None:
    allowed = {
        (
            _SERVICE,
            "_deliver_via_transport",
            "transport",
        ): "the one door, reached only from attempt",
        (
            _SMS_TRANSPORT,
            "send",
            "self.provider",
        ): "the SMS transport handing its message to its gateway",
        (
            _SRC / "modules" / "documents" / "esign_service.py",
            "send_for_signature",
            "provider",
        ): (
            "an e-signature provider sending a lease document for signing: not a patient notification"
        ),
    }
    findings = []
    for path in _sources():
        for function, receiver, line in dispatch_calls(
            path.read_text(encoding="utf-8")
        ):
            if (path, function, receiver) not in allowed:
                findings.append(
                    f"{path.relative_to(_SRC.parent)}:{line} {function}() calls {receiver}.send"
                )
    assert findings == [], (
        "a message handed to a transport or gateway past the gate:\n"
        + "\n".join(findings)
    )


def test_the_one_door_is_opened_only_by_attempt_after_the_gate() -> None:
    tree = ast.parse(_SERVICE.read_text(encoding="utf-8"))
    callers = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and _calls_named(node, "_deliver_via_transport")
    ]
    assert callers == ["attempt"], callers
    attempt = _function(tree, "attempt")
    gate = _calls_named(attempt, "resolve")
    door = _calls_named(attempt, "_deliver_via_transport")
    assert gate and door and gate[0] < door[0], (
        "attempt must ask resolve() before it delivers"
    )
    suppressed = [
        node
        for node in ast.walk(attempt)
        if isinstance(node, ast.If)
        and "SUPPRESS" in ast.unparse(node.test)
        and any(isinstance(child, ast.Return) for child in node.body)
    ]
    assert suppressed and suppressed[0].lineno < door[0], (
        "a suppressed decision must return before delivery"
    )


# --- the guard fails on what it exists to catch --------------------------------------------------------------


def test_the_guard_fails_on_a_transport_that_skips_the_gate() -> None:
    """How to verify, step 3: add a transport that skips the gate, and the guard fails."""
    sneaky = (
        "from src.modules.notifications.transports.base import Transport\n"
        "class PagerTransport(Transport):\n"
        '    """Beeps a pager."""\n'
        "def page_patient(db, patient, message):\n"
        '    """Straight to the pager, no questions asked."""\n'
        "    transport = PagerTransport()\n"
        "    return transport.send(to=patient.pager, message=message)\n"
    )
    path = _SRC / "modules" / "queue" / "pager.py"
    assert transports_defined_outside(sneaky, path) == [
        "pager.py:2 class PagerTransport"
    ]
    assert dispatch_calls(sneaky) == [("page_patient", "transport", 7)]
