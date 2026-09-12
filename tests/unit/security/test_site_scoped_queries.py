"""No query on a site-scoped row is built outside the site guard (Issue 19).

Non-negotiable 3 says every site-scoped query goes through one helper. A rule like that survives
exactly as long as something checks it, so this walks the source: any ``select(Model)``,
``db.get(Model, …)`` or ``.query(Model)`` naming a **site-scoped model** — one carrying a
``site_id`` column — outside :mod:`src.core.site_scope` is a finding, as is a ``select(User)``
inside the staff module, whose rows are site-scoped through role assignments rather than a column.

The set of site-scoped models is **discovered from the mapped models**, not listed here, so a model
that gains a ``site_id`` tomorrow is covered without anyone remembering this file. The fixtures at
the bottom prove the check fails on the two shapes it exists to catch, and passes the same code
written through the helper: a guard that cannot fail passes forever.

Reads source, never rendered output (``.cursor/rules/testing-strategy.mdc``).
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.database.models import Base

_SRC = Path(__file__).resolve().parents[3] / "src"
#: The helper itself is where these queries are built.
_HELPER = _SRC / "core" / "site_scope.py"
#: Query constructors a model name can appear in.
_QUERY_CALLS = frozenset({"select", "get", "query"})
#: Calls that already apply the site filter: a model inside one of these is fine.
_SCOPED_CALLS = frozenset(
    {
        "scoped_select",
        "select_in_scope",
        "get_in_site_or_404",
        "staff_at_site",
        "staff_member_in_site_or_404",
        "roles_held_at_site",
        "published_select",
    }
)
#: Modules whose rows are site-scoped through role assignments rather than a column.
_ASSIGNMENT_SCOPED: dict[str, frozenset[str]] = {
    "modules/staff": frozenset({"User", "UserRoleAssignment"}),
}
#: The queries that are deliberately not site-scoped, keyed ``path::function``, each with the
#: reason. A **closed** list, and narrow on purpose: one function, not one file, so the next query
#: in the same module is still a finding. An entry here is a decision someone can read, not a way
#: around the rule.
_UNSCOPED_BY_DESIGN: dict[str, str] = {
    "modules/staff/assignments.py::_assignments_at": (
        "``user_roles`` has no ``site_id`` column: the clinic **is** its ``scope_id``, so the "
        "filter this applies (``scope_type='site' AND scope_id = access.site_id``) is the site "
        "filter, written once here for the writes the way ``roles_held_at_site`` writes it for the "
        "reads. There is no guard helper to route it through (Issue 28)"
    ),
    "modules/staff/assignments.py::_sync_queue_scoped_roles": (
        "reads the caller's queue-scoped ``user_roles`` rows **across clinics on purpose**: the "
        "same person may work at another clinic, and only this clinic's rows are ours to remove. "
        "The narrowing is the explicit ``this_clinic`` set below, taken from a guarded "
        "``scoped_select(Queue, access)`` (Issue 28)"
    ),
    "modules/sites/catalogue.py::seed_default_catalogue": (
        "a clinic's default catalogue is written **while the clinic is being onboarded** "
        "(Issue 29), before anyone holds a role at it, so there is no SiteAccess to scope by; the "
        "site id comes from the row just created, and the function refuses to do anything if the "
        "clinic already has a service (Issue 26)"
    ),
    "modules/queues/service.py::create_default_queues": (
        "a clinic's default queue set is created **while the clinic is being onboarded** (Issue 29), "
        "before anyone holds a role at it, so there is no SiteAccess to scope by; the site id comes "
        "from the row just created, and the function refuses to do anything if the clinic already "
        "has a queue (Issue 25)"
    ),
    "modules/queue/snapshot.py::reconcile_snapshots": (
        "the reconciliation sweep (Issue 36) is a scheduled system job with no caller and no "
        "clinic: it recounts **every** active queue on the platform and repairs each snapshot row "
        "against its own queue, so a site filter would be the wrong narrowing. It reads only "
        "queue ids and snapshot figures and writes only snapshot rows"
    ),
    "modules/staff/invitations.py::usable_invitation": (
        "an invitation link is opened by someone who has no account and therefore no clinic to be "
        "scoped by; the id comes from the signed token, and the row's own site_id is what "
        "acceptance then writes (Issue 22)"
    ),
}


def site_scoped_models() -> frozenset[str]:
    """Every mapped model carrying a ``site_id`` column, discovered from the metadata."""
    return frozenset(
        mapper.class_.__name__
        for mapper in Base.registry.mappers
        if "site_id" in mapper.columns
    )


def _models_named(call: ast.Call) -> set[str]:
    """The model names a query call mentions (``select(Ticket)``, ``select(Ticket.id)``)."""
    names: set[str] = set()
    for argument in call.args:
        node = argument.value if isinstance(argument, ast.Attribute) else argument
        if isinstance(node, ast.Name):
            names.add(node.id)
    return names


def _exempt_call_ids(tree: ast.Module, path: str) -> set[int]:
    """The ids of every call inside a function listed in :data:`_UNSCOPED_BY_DESIGN` for ``path``."""
    exempt: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if f"{path.removeprefix('src/')}::{node.name}" not in _UNSCOPED_BY_DESIGN:
            continue
        exempt.update(
            id(child) for child in ast.walk(node) if isinstance(child, ast.Call)
        )
    return exempt


def findings_in_source(source: str, *, path: str, models: frozenset[str]) -> list[str]:
    """Every unscoped query on one of ``models`` in ``source``, as readable lines."""
    tree = ast.parse(source)
    scoped_calls: set[int] = _exempt_call_ids(tree, path)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _SCOPED_CALLS
        ):
            scoped_calls.update(id(argument) for argument in node.args)
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else ""
        )
        if name not in _QUERY_CALLS or id(node) in scoped_calls:
            continue
        for model in sorted(_models_named(node) & models):
            out.append(
                f"{path}:{node.lineno} builds {name}({model}) outside the site guard; "
                "use src.core.site_scope (scoped_select / get_in_site_or_404 / staff_at_site)"
            )
    return out


def _source_files() -> list[Path]:
    """Every application source file except the guard itself."""
    return [
        path
        for path in sorted(_SRC.rglob("*.py"))
        if "__pycache__" not in path.parts and path != _HELPER
    ]


def _models_for(path: Path) -> frozenset[str]:
    """The model names that must be scoped in ``path``: the site-scoped ones, plus any local rule."""
    relative = path.relative_to(_SRC).as_posix()
    extra = frozenset(
        name
        for prefix, names in _ASSIGNMENT_SCOPED.items()
        if relative.startswith(prefix)
        for name in names
    )
    return site_scoped_models() | extra


def test_no_query_on_a_site_scoped_model_is_built_outside_the_guard() -> None:
    """The standing rule. A new router or service that forgets the helper fails here."""
    findings: list[str] = []
    for path in _source_files():
        findings += findings_in_source(
            path.read_text(encoding="utf-8"),
            path=str(path.relative_to(_SRC.parent)),
            models=_models_for(path),
        )
    assert not findings, "site-scoped queries outside the guard:\n" + "\n".join(
        findings
    )


_UNSCOPED_ROUTER = """
from sqlalchemy import select
from src.database.models import Ticket

def list_tickets(db, site_id):
    return db.execute(select(Ticket).where(Ticket.site_id == site_id)).scalars().all()
"""

_SCOPED_ROUTER = """
from src.core.site_scope import scoped_select
from src.database.models import Ticket

def list_tickets(db, access):
    return db.execute(scoped_select(Ticket, access)).scalars().all()
"""

_UNSCOPED_STAFF = """
from sqlalchemy import select
from src.database.models import User

def list_staff(db):
    return db.execute(select(User)).scalars().all()
"""


def test_the_check_fails_on_a_router_query_that_skips_the_helper() -> None:
    """The criterion, demonstrated: a hand-written query on a site-scoped model is a finding."""
    findings = findings_in_source(
        _UNSCOPED_ROUTER,
        path="src/modules/tickets/router.py",
        models=frozenset({"Ticket"}),
    )
    assert findings and "select(Ticket)" in findings[0]


def test_the_check_passes_the_same_query_written_through_the_helper() -> None:
    """And it is discriminating, not simply always red."""
    assert not findings_in_source(
        _SCOPED_ROUTER,
        path="src/modules/tickets/service.py",
        models=frozenset({"Ticket"}),
    )


def test_the_staff_module_may_not_select_users_directly() -> None:
    """Staff are site-scoped through their assignments; the rule covers that shape too."""
    assert findings_in_source(
        _UNSCOPED_STAFF,
        path="src/modules/staff/service.py",
        models=_models_for(_SRC / "modules" / "staff" / "service.py"),
    )


_EXEMPT_AND_ITS_NEIGHBOUR = """
from sqlalchemy import select
from src.database.models import StaffInvitation

def usable_invitation(db, token):
    return db.execute(select(StaffInvitation).where(StaffInvitation.id == token)).scalar_one()

def list_invitations(db, site_id):
    return db.execute(select(StaffInvitation)).scalars().all()
"""


def test_the_named_exception_covers_one_function_and_not_the_module() -> None:
    """An entry in the list exempts the function it names — and nothing else in the same file."""
    findings = findings_in_source(
        _EXEMPT_AND_ITS_NEIGHBOUR,
        path="src/modules/staff/invitations.py",
        models=frozenset({"StaffInvitation"}),
    )
    assert len(findings) == 1 and "list_invitations" not in findings[0]
    assert findings[0].endswith(
        "use src.core.site_scope (scoped_select / get_in_site_or_404 / staff_at_site)"
    )
    # The exempt function's own query is gone from the findings, which is the whole point.
    assert not any(":6:" in finding for finding in findings)


def test_every_named_exception_carries_a_reason() -> None:
    """A list of exemptions with no reasons is a list nobody can review."""
    assert all(reason.strip() for reason in _UNSCOPED_BY_DESIGN.values())
    for key in _UNSCOPED_BY_DESIGN:
        relative, _, function = key.partition("::")
        assert function and (_SRC / relative).exists(), key


def test_the_model_set_is_discovered_from_the_models_not_listed_here() -> None:
    """Membership follows the column: a model that gains ``site_id`` is covered with no edit here.

    Nothing carries one yet — ``audit_event`` gets it in Issue 20 and staff invitations in Issue 22
    — which is exactly why this is a property of the mappers rather than a list someone maintains.
    """
    discovered = site_scoped_models()
    for mapper in Base.registry.mappers:
        assert (mapper.class_.__name__ in discovered) == ("site_id" in mapper.columns)
