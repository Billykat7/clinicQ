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

Reads source, never rendered output (``docs/IDE/RULES/testing-strategy.mdc``).
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
        "displayed_select",
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
    "modules/display/devices.py::device_for_secret": (
        "a kiosk box authenticating: the box presents its secret and holds no role anywhere, so there is "
        "no SiteAccess; the row is found by the SHA-256 of that secret (unique) and the clinic it may "
        "show is read **from the row**, never from the request (Issue 61), as a refresh token is"
    ),
    "modules/display/devices.py::_pending_by_code": (
        "a box waiting to be paired belongs to **no clinic yet** (site_id is NULL, and the query requires "
        "it): the code's digest finds the one row, and pair_device then writes the manager's clinic from "
        "the SiteAccess the site guard resolved (Issue 61)"
    ),
    "modules/display/devices.py::_watched": (
        "the display device watch (Issue 61) is a scheduled system job with no caller and no clinic: it "
        "reads every paired box on the platform to find the silent ones, like the recall timer sweep"
    ),
    "modules/display/devices.py::purge_unpaired": (
        "the same system job deletes boxes that were never paired, which belong to no clinic (site_id "
        "is NULL, and the query requires it)"
    ),
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
    "modules/queues/live.py::read_waiting_counts": (
        "counts ``waiting`` tickets per queue for queues the caller has **already** narrowed: the "
        "published directory (published_select, so only clinics a patient may see) or the "
        "reconciliation sweep. It filters by those queue ids and returns one number per queue, "
        "never a ticket row, so there is nothing a site filter would add (Issue 39)"
    ),
    "modules/queue/tickets.py::patient_tickets_select": (
        "a patient's **own** tickets, at whichever clinics they joined: a patient holds no role at "
        "a clinic, so there is no SiteAccess to scope by, and the narrowing is the patient id a "
        "route has authenticated (the same shape as reading one's own consent). It never lists "
        "another patient's tickets (Issue 39)"
    ),
    "modules/queue/service.py::_existing_ticket": (
        "the join service's duplicate check (Issue 40) runs for a patient, who holds no role at a "
        "clinic and so has no SiteAccess. It is narrowed by the queue the caller already resolved "
        "(published_select for a patient, get_queue for the desk) and by the joining patient's "
        "own id, and returns only that patient's ticket"
    ),
    "modules/queue/lifecycle.py::_locked": (
        "the lifecycle's row lock re-reads **one ticket by id** for update (Issue 41). Its callers "
        "have already scoped that id: a route through get_in_site_or_404, call_next through a "
        "queue from get_queue, and the recall timer (Issue 43) as a system job with no clinic. "
        "Filtering again here would not narrow anything, and the lock must see the row whatever "
        "site a job runs for"
    ),
    "modules/queue/lifecycle.py::transition_ticket": (
        "reads the moved ticket's own queue by id to write its snapshot through (Issue 36's hook); "
        "the queue is the ticket's, which the caller already scoped"
    ),
    "modules/queue/lifecycle.py::call_next": (
        "picks the next waiting ticket **in a queue the caller resolved through the site guard** "
        "(get_queue), filtered by that queue's id, with FOR UPDATE SKIP LOCKED; it returns one "
        "ticket id from that queue and nothing else (Issue 41)"
    ),
    "modules/queue/waits.py::recent_samples": (
        "reads the recent wait samples of queues the caller has **already** narrowed (the published "
        "directory, the reconciliation sweep, or one queue a join just resolved), filtered by those "
        "queue ids. It returns interval minutes and call hours, never a ticket or a patient "
        "(Issue 42)"
    ),
    "modules/queue/timers.py::_due": (
        "the recall timer sweep (Issue 43) is a scheduled system job with no caller and no clinic: "
        "it reads **every** called or recalled ticket on the platform, with its own queue's and "
        "clinic's timeout, to find the deadlines that have passed. A site filter would be the wrong "
        "narrowing, like the snapshot reconciliation's"
    ),
    "modules/queue/ticket_page.py::find_by_page_token": (
        "the ticket page (Issue 68) is opened by its unguessable link, by someone who holds no role "
        "at any clinic and may have no account at all: the token (256 random bits, unique) finds the "
        "one ticket, as a device secret finds its box (Issue 61)"
    ),
    "modules/queue/ticket_page.py::active_page_for_patient": (
        "the installed patient app (Issue 69) opens at the signed-in patient's own open ticket: the patient "
        "holds no role at any clinic, and the query is narrowed to that patient's tickets, as the patient's "
        "own ticket list is"
    ),
    "modules/queue/ticket_page.py::page_state": (
        "reads the found ticket's own queue and clinic by id to show where the patient is; the ticket "
        "came from find_by_page_token and nothing else is reachable from here (Issue 68)"
    ),
    "modules/queue/ticket_page.py::_next_leg": (
        "reads the ticket a transfer issued **from the found ticket**, by its transferred_from_id, so "
        "the family following along can follow the visit (Issue 68)"
    ),
    "modules/queue/notices.py::tell": (
        "reads the moved ticket's own queue and clinic by id to word the patient's message (Issues "
        "43, 45, 63); the ticket came from the move that caused the message, already scoped by its "
        "route or taken by the recall sweep, and nothing else is reachable from here"
    ),
    "modules/queue/notices.py::tell_next_in_line": (
        "reads the first waiting ticket **in the moved ticket's own queue and day** to tell that "
        "patient they are next (Issue 63); the queue id comes from the ticket the move already "
        "scoped, never from a request"
    ),
    "modules/queue/cancellation.py::cancel_own_ticket": (
        "a patient cancelling **their own** ticket (Issue 44) holds no role at a clinic, so there is "
        "no SiteAccess; the ticket is read by id and refused unless its patient_id is the caller's, "
        "with the same not-found either way. Staff cancellations go through get_in_site_or_404"
    ),
    "modules/queue/router.py::my_tickets": (
        "reads the queue of each of the signed-in patient's **own** tickets by id, to estimate that "
        "ticket's wait (Issues 42, 44); the tickets came from patient_tickets_select, scoped by "
        "the patient"
    ),
    "modules/queue/transfer.py::transfer_ticket": (
        "reads the moved ticket's own queue, visit and clinic by id (Issue 45); the ticket was "
        "scoped by the route (get_in_site_or_404) and the target queue by get_queue, and the "
        "function refuses a target at another clinic"
    ),
    "modules/queue/transfer.py::_placement_key": (
        "reads the waiting tickets, and when their visits began, **in the target queue** the route "
        "resolved through get_queue, to find the moved patient's place (Issue 45). It returns an "
        "order key, never a row"
    ),
    "modules/notifications/delivery_stats.py::channel_stats": (
        "delivery health is platform-wide (Issue 71): a gateway outage fails every clinic's messages at once "
        "and is one incident, so the operators' delivery panel and the failure-rate alert count the whole "
        "ledger per transport; only callers with the platform `logs` grant (or the scheduler) reach it"
    ),
    "modules/notifications/service.py::_by_dedupe_key": (
        "the notification ledger (Issue 63) is platform operational data, not a clinic's records: finds the row one queue event already recorded, by its unique dedupe key, so a "
        "replay sends nothing; the key is built by the move that caused the event"
    ),
    "modules/notifications/service.py::_tried_channels": (
        "the notification ledger (Issue 63) is platform operational data, not a clinic's records: walks one message's own fallback chain by id, from a row the service is delivering"
    ),
    "modules/notifications/service.py::deliver_committed": (
        "the notification ledger (Issue 63) is platform operational data, not a clinic's records: the post-commit delivery of one row by the id its own commit published; no caller, "
        "no clinic, like the retry sweep"
    ),
    "modules/notifications/service.py::apply_sms_receipt": (
        "the notification ledger (Issue 63) is platform operational data, not a clinic's records: a "
        "gateway's delivery receipt (Issue 65) names its SMS by the gateway's own message id, and the "
        "gateway holds no role at any clinic"
    ),
    "modules/notifications/service.py::run_retry_sweep": (
        "the notification ledger (Issue 63) is platform operational data, not a clinic's records: a scheduled system job that retries **every** due message on the platform, like the "
        "recall timer sweep"
    ),
    "modules/notifications/service.py::record_delivery_status": (
        "the notification ledger (Issue 63) is platform operational data, not a clinic's records: a provider's delivery receipt names its message by the provider's id; the provider "
        "holds no role at any clinic"
    ),
    "modules/notifications/service.py::get_status": (
        "the notification ledger (Issue 63) is platform operational data, not a clinic's records: the admin status query, gated by the platform ``logs`` READ verb, not a clinic role"
    ),
    "modules/notifications/service.py::list_notifications": (
        "the notification ledger (Issue 63) is platform operational data, not a clinic's records: the admin delivery viewer, gated by the platform ``logs`` READ verb, not a clinic role"
    ),
    "modules/notifications/service.py::_safe_finalise": (
        "the notification ledger (Issue 63) is platform operational data, not a clinic's records: the email path finishing the row it recorded a moment earlier, by that row's id"
    ),
    "modules/queue/priority.py::_key_ahead_of": (
        "reads the order key of the waiting ticket just ahead of the named one, **in that ticket's "
        "own queue and day**; both tickets were scoped by the route (get_in_site_or_404), and it "
        "returns a number, never a row (Issue 46)"
    ),
    "modules/queue/priority.py::override_priority": (
        "reads the moved ticket's own queue by id to write its snapshot through (Issue 36's hook); "
        "the ticket was scoped by the route (Issue 46)"
    ),
    "modules/staff/invitations.py::usable_invitation": (
        "an invitation link is opened by someone who has no account and therefore no clinic to be "
        "scoped by; the id comes from the signed token, and the row's own site_id is what "
        "acceptance then writes (Issue 22)"
    ),
    "modules/discovery/analytics.py::conversion_by_site": (
        "the platform-wide view-to-join numbers the M12 reports read (Issue 38): a report across "
        "clinics has no single clinic to scope by. It returns counts per site id, never an event "
        "row, and no clinic-facing route calls it; a clinic's own report goes through "
        "conversion_for_site, which uses scoped_select"
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
