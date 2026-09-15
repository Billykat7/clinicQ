"""The queue engine calls one function and knows nothing about transports (Issue 63).

Reads the source, not behaviour (``docs/IDE/RULES/testing-strategy.mdc``): across
``src/modules/queue``, exactly one module imports the notification service, it imports nothing else
from ``src.modules.notifications``, and the only thing it calls on the service is ``notify``. A queue
module that reached for a transport, an SMS provider or ``send_sms`` would fail here, and the
fixture at the bottom proves it does.
"""

from __future__ import annotations

import ast
from pathlib import Path

_QUEUE = Path(__file__).resolve().parents[3] / "src" / "modules" / "queue"
_NOTIFICATIONS = "src.modules.notifications"
#: The queue's one door to notifications.
_DOOR = "notices.py"


def notification_imports(source: str) -> list[str]:
    """Every ``src.modules.notifications`` name ``source`` imports, as ``module.name``."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            _NOTIFICATIONS
        ):
            found += [f"{node.module}.{alias.name}" for alias in node.names]
        elif isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.startswith(_NOTIFICATIONS)]
    return found


def service_calls(source: str, alias: str) -> set[str]:
    """The attributes called on ``alias`` (``notifications.notify`` gives ``notify``)."""
    return {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == alias
    }


def test_only_the_notices_module_imports_notifications() -> None:
    """Every other queue module tells a patient through ``notices``, never directly."""
    importers = {
        path.name: notification_imports(path.read_text(encoding="utf-8"))
        for path in sorted(_QUEUE.rglob("*.py"))
        if "__pycache__" not in path.parts
    }
    assert {name for name, names in importers.items() if names} == {_DOOR}
    assert importers[_DOOR] == [f"{_NOTIFICATIONS}.service"]


def test_the_notices_module_calls_only_notify() -> None:
    """No transport, provider, template or retry: the one function, with an event name."""
    source = (_QUEUE / _DOOR).read_text(encoding="utf-8")
    assert service_calls(source, "notifications") == {"notify"}


def test_the_guard_fails_on_a_queue_module_that_sends_an_sms_itself() -> None:
    """A guard that cannot fail passes forever."""
    offender = (
        "from src.modules.notifications import service as notifications\n"
        "from src.modules.notifications.sms import build_sms_provider\n"
        "def recall(db, ticket, phone):\n"
        "    notifications.send_sms(db, to=phone, template=None, context={},\n"
        "                           provider=build_sms_provider())\n"
    )
    assert f"{_NOTIFICATIONS}.sms.build_sms_provider" in notification_imports(offender)
    assert service_calls(offender, "notifications") == {"send_sms"}
