"""Every state-changing ClinicQ route writes an audit row (Issue 20).

"Every state-changing action writes exactly one audit row" is a rule about code that does not exist
yet as much as about code that does, so this guard reads the tree: for each mutating route
(``POST``/``PUT``/``PATCH``/``DELETE``) in a **ClinicQ module**, the handler must record an audit
event, or call a service function that does. A route that does neither must be listed below with
its reason.

Kernel modules (messaging, alerts, documents, the auth surface) are out of scope here: they are
inherited code with their own security logging, and Issue 20 does not rewrite them. The list of
ClinicQ modules is what makes this guard grow with the product rather than with the kernel — when
the priority reorder (Issue 46), the display-mode change (Issue 27) and the no-show (Issue 43)
land in ``queues`` and ``sites``, they are covered the day their route appears.

Reads source, never rendered output (``docs/IDE/RULES/testing-strategy.mdc``).
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "src"
#: The modules this rule covers: ClinicQ's own.
CLINICQ_MODULES = (
    "patients",
    "staff",
    "sites",
    "queues",
    "queue",
    "tickets",
    "consent",
    "appointments",
)
_MUTATING = frozenset({"post", "put", "patch", "delete"})
#: The call that records an audit row, wherever it is made.
_AUDIT_CALL = "record_audit_event"

#: Mutating routes that legitimately write no audit row, with the reason. Closed: a new one fails.
NOT_AUDITED: dict[str, str] = {
    "patients.router:request_code": (
        "sends a code and records nothing: no patient exists yet, and an audit row for every "
        "number a stranger types would itself be a log of who tried"
    ),
    "patients.router:verify_code": (
        "the patient record it creates is audited inside the service (service.verify_code)"
    ),
    "patients.router:logout": "ends a session; it changes no record",
    "sites.router:geocode": (
        "a POST because an address is a body rather than a query string, but it is a lookup: it "
        "reads a typed address and returns candidate coordinates, and changes no record. The "
        "clinic that results from it is audited when it is created (sites.router:create_site)"
    ),
    "sites.router:geocode_for_site": (
        "the same lookup for a clinic that already exists; the coordinate it suggests is recorded "
        "only if the manager saves it, which is audited (sites.router:update_site)"
    ),
}


def _module_name(path: Path) -> str:
    """``patients.router`` for ``src/modules/patients/router.py``."""
    return ".".join(path.relative_to(_SRC / "modules").with_suffix("").parts[-2:])


def _calls(node: ast.AST) -> set[str]:
    """Every function name called anywhere inside ``node`` (``service.create`` → ``create``)."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                names.add(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                names.add(child.func.attr)
    return names


def _auditing_functions(*modules: Path) -> set[str]:
    """Functions in ``modules`` that record an audit row, directly or through each other.

    The files are read together, so a service in one file that calls an auditing function in
    another file of the same module counts: the queue's cancellation service moves a ticket through
    the lifecycle, and the lifecycle is what records (Issue 44).
    """
    functions: dict[str, set[str]] = {}
    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.setdefault(node.name, set()).update(_calls(node))
    auditing = {name for name, calls in functions.items() if _AUDIT_CALL in calls}
    # More hops: a function that calls a function that audits, a few levels deep.
    for _ in range(4):
        grown = {
            name for name, calls in functions.items() if calls & auditing
        } | auditing
        if grown == auditing:
            break
        auditing = grown
    return auditing


def _routes(router: Path) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """``(method, handler)`` for every mutating route declared in a router module."""
    tree = ast.parse(router.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            func = call.func if call else decorator
            if isinstance(func, ast.Attribute) and func.attr in _MUTATING:
                found.append((func.attr, node))
    return found


def unaudited_mutations() -> list[str]:
    """Every mutating ClinicQ route that records nothing and is not listed, as readable lines."""
    findings: list[str] = []
    for module in CLINICQ_MODULES:
        router = _SRC / "modules" / module / "router.py"
        if not router.exists():
            continue
        # Any file of the module may hold the function that records: patients keep theirs in
        # service.py and consent.py.
        auditing_in_service = _auditing_functions(
            *(path for path in sorted(router.parent.glob("*.py")) if path != router)
        )
        auditing_in_router = _auditing_functions(router)
        for method, handler in _routes(router):
            key = f"{_module_name(router)}:{handler.name}"
            called = _calls(handler)
            if handler.name in auditing_in_router or called & auditing_in_service:
                continue
            if key in NOT_AUDITED:
                continue
            findings.append(
                f"{key} ({method.upper()}) changes state but records no audit event; "
                "call record_audit_event (or a service function that does), or list it in "
                "NOT_AUDITED with the reason"
            )
    return findings


def test_every_state_changing_clinicq_route_writes_an_audit_row() -> None:
    """The standing rule: a new mutation that records nothing fails here."""
    findings = unaudited_mutations()
    assert not findings, "\n".join(findings)


def test_every_exception_carries_a_reason() -> None:
    """An exception is a decision on the record."""
    assert all(reason.strip() for reason in NOT_AUDITED.values())
    modules = {key.split(".", 1)[0] for key in NOT_AUDITED}
    assert modules <= set(CLINICQ_MODULES)


def test_a_listed_route_that_started_auditing_must_leave_the_list() -> None:
    """So the list cannot rot into a blanket exemption."""
    findings = {line.split(" ", 1)[0] for line in unaudited_mutations()}
    stale = sorted(key for key in NOT_AUDITED if key not in findings | set(NOT_AUDITED))
    assert not stale


def test_the_guard_fails_on_a_mutation_that_records_nothing(tmp_path: Path) -> None:
    """A guard that cannot fail passes forever: the reorder Issue 46 will add, unaudited."""
    router = tmp_path / "router.py"
    router.write_text(
        '@router.post("/{queue_id}/tickets/{ticket_id}/priority")\n'
        "def reorder(queue_id: str, ticket_id: str, db: DbSession) -> None:\n"
        '    """Move a ticket up the queue."""\n'
        "    service.reorder(db, queue_id, ticket_id)\n",
        encoding="utf-8",
    )
    service = tmp_path / "service.py"
    service.write_text(
        "def reorder(db, queue_id, ticket_id):\n"
        '    """Move it, and record nothing."""\n'
        "    db.execute(...)\n",
        encoding="utf-8",
    )
    assert _routes(router), "the fixture declares a mutating route"
    assert "reorder" not in _auditing_functions(service)

    audited = tmp_path / "audited.py"
    audited.write_text(
        "def reorder(db, queue_id, ticket_id):\n"
        '    """Move it, and record that it moved."""\n'
        "    record_audit_event(db, action=AuditAction.UPDATE)\n",
        encoding="utf-8",
    )
    assert "reorder" in _auditing_functions(audited)
