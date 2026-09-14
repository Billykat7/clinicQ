"""Non-negotiable 2, enforced: nothing but ``transition_ticket()`` writes a ticket's status (Issue 41).

The most important test of the lifecycle, because the rule it guards is the one that silently
breaks: one ``ticket.status = "called"`` in a router and the board, the notifications, the reports
and the patient's screen stop agreeing. Two layers, because each sees what the other cannot:

* **In the source, before anything runs** (this file's first test): every file under ``src/`` is
  parsed and each of these is a finding, named by file and line, anywhere outside
  :func:`src.modules.queue.lifecycle.transition_ticket`:

  - an assignment to ``<something>.status`` where the something is a ticket (its name says so),
    or in any module that imports ``Ticket`` whatever the variable is called;
  - ``setattr(…, "status", …)`` in a module that imports ``Ticket``;
  - ``Ticket(…, status=…)``: a ticket starts ``waiting`` by default and is never built in another
    status;
  - ``update(Ticket)``: a bulk ``UPDATE`` bypasses the ORM, and so the runtime guard below;
  - a string of SQL that updates ``ticket`` and sets ``status``;
  - entering ``status_write_permitted()``, the runtime guard's key, anywhere but the lifecycle.

* **At runtime** (the model's attribute listener): assigning ``Ticket.status`` outside the lifecycle
  raises :class:`~src.database.models.ticket.DirectStatusWriteError` the moment it runs, which
  catches a write the source scan could not attribute to a ticket.

The fixtures prove every rule fails on the shape it exists to catch, and passes the lifecycle
itself. A guard that cannot fail passes forever.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.commons.enums import TicketStatus
from src.database.models import Ticket
from src.database.models.ticket import DirectStatusWriteError
from tests.factories import QueueFactory, SiteFactory, TicketFactory

_SRC = Path(__file__).resolve().parents[3] / "src"
#: The one function allowed to write the status, as ``path::function``.
_WRITER = "modules/queue/lifecycle.py::transition_ticket"
#: Raw SQL that changes a ticket's status.
_SQL_STATUS_UPDATE = re.compile(
    r"\bupdate\b[^;]*\bticket\b[^;]*\bset\b[^;]*\bstatus\b", re.I | re.S
)


def _imports_ticket(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and any(alias.name == "Ticket" for alias in node.names)
        for node in ast.walk(tree)
    )


def _owner_text(node: ast.expr) -> str:
    """The source-ish name of what an attribute hangs off (``ticket``, ``result.ticket``)."""
    return ast.unparse(node)


def _enclosing_functions(tree: ast.Module) -> dict[int, str]:
    """``{id(node): name of the innermost function containing it}``."""
    owner: dict[int, str] = {}

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = (
                child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else function
            )
            owner[id(child)] = name
            visit(child, name)

    visit(tree, "<module>")
    return owner


def findings_in_source(source: str, *, path: str) -> list[str]:
    """Every write to a ticket's status outside the lifecycle, as readable lines."""
    tree = ast.parse(source)
    imports_ticket = _imports_ticket(tree)
    functions = _enclosing_functions(tree)
    out: list[str] = []

    def finding(node: ast.AST, what: str) -> None:
        where = f"{path}::{functions.get(id(node), '<module>')}"
        if where != _WRITER:
            out.append(
                f"src/{path}:{getattr(node, 'lineno', 0)} {what}; only transition_ticket() "
                "writes a ticket's status (non-negotiable 2)"
            )

    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr == "status":
                owner = _owner_text(target.value)
                if imports_ticket or "ticket" in owner.lower():
                    finding(node, f"assigns {owner}.status")
        if not isinstance(node, ast.Call):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _SQL_STATUS_UPDATE.search(node.value)
            ):
                finding(node, "runs SQL that updates a ticket's status")
            continue
        name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else ""
        )
        if (
            name == "setattr"
            and imports_ticket
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "status"
        ):
            finding(node, "sets status with setattr")
        if name == "Ticket" and any(
            keyword.arg == "status" for keyword in node.keywords
        ):
            finding(node, "builds a Ticket with a status")
        if name == "update" and any(
            isinstance(argument, ast.Name) and argument.id == "Ticket"
            for argument in node.args
        ):
            finding(node, "builds a bulk update(Ticket)")
        if name == "status_write_permitted":
            finding(node, "opens status_write_permitted()")
    return out


def test_nothing_outside_transition_ticket_writes_a_tickets_status() -> None:
    """The standing rule over the whole application: ``src/`` has exactly one writer."""
    findings: list[str] = []
    for file in sorted(_SRC.rglob("*.py")):
        if "__pycache__" in file.parts:
            continue
        relative = file.relative_to(_SRC).as_posix()
        if relative == "database/models/ticket.py":
            continue  # defines the runtime guard and its key; writes no status
        findings += findings_in_source(file.read_text(encoding="utf-8"), path=relative)
    assert not findings, (
        "a ticket's status written outside the lifecycle:\n" + "\n".join(findings)
    )


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        (
            "an assignment in a router",
            "from src.database.models import Ticket\n"
            "def call(db, ticket_id):\n"
            "    ticket = db.get(Ticket, ticket_id)\n"
            "    ticket.status = TicketStatus.CALLED.value\n",
        ),
        (
            "an assignment under another name, in a module that imports Ticket",
            "from src.database.models import Ticket\n"
            "def close(row):\n"
            "    row.status = 'done'\n",
        ),
        (
            "a ticket reached through another object",
            "def board(result):\n    result.ticket.status = TicketStatus.DONE\n",
        ),
        (
            "setattr",
            "from src.database.models import Ticket\n"
            "def close(row):\n"
            "    setattr(row, 'status', 'done')\n",
        ),
        (
            "a ticket built in a status",
            "def make():\n    return Ticket(status='called')\n",
        ),
        (
            "a bulk update",
            "def sweep(db):\n"
            "    db.execute(update(Ticket).values(status=TicketStatus.NO_SHOW.value))\n",
        ),
        (
            "raw SQL",
            "def sweep(db):\n"
            "    db.execute(text(\"UPDATE clinicq.ticket SET status = 'no_show' WHERE id = :id\"))\n",
        ),
        (
            "the runtime guard's key",
            "def sneak(ticket):\n"
            "    with status_write_permitted():\n"
            "        ticket.status = 'done'\n",
        ),
    ],
)
def test_the_guard_fails_on_every_shape_it_exists_to_catch(
    shape: str, source: str
) -> None:
    """How to verify, step 2, for each way a direct write can be written."""
    assert findings_in_source(source, path="modules/queue/router.py"), shape


def test_the_guard_passes_the_lifecycle_and_other_records_statuses() -> None:
    """Discriminating, not always red: the one writer, and a site's own status, are fine."""
    lifecycle = (
        "from src.database.models.ticket import Ticket, status_write_permitted\n"
        "def transition_ticket(db, ticket_id, requested):\n"
        "    with status_write_permitted():\n"
        "        ticket.status = requested.value\n"
    )
    assert findings_in_source(lifecycle, path="modules/queue/lifecycle.py") == []
    onboarding = "def verify(site):\n    site.status = SiteStatus.VERIFIED.value\n"
    assert findings_in_source(onboarding, path="modules/sites/onboarding.py") == []


def test_a_direct_write_at_runtime_is_refused_the_moment_it_runs(
    session_factory: sessionmaker[Session],
) -> None:
    """The second layer: a write the scan could not see still cannot happen."""
    with session_factory() as db:
        queue = QueueFactory.create(db, site_id=SiteFactory.create(db).id)
        ticket = TicketFactory.create(db, queue=queue)
        db.commit()
        row: Ticket = ticket  # a name the source scan would not attribute to a ticket
        with pytest.raises(DirectStatusWriteError, match="transition_ticket"):
            row.status = TicketStatus.DONE.value
        db.expire_all()
        assert db.get(Ticket, ticket.id).status == TicketStatus.WAITING.value  # type: ignore[union-attr]
