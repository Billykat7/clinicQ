"""Every protected API route declares its permission through a gate dependency (Issue 18).

Walks the real application's router tree (FastAPI includes routers lazily, so ``app.routes`` alone
shows almost nothing) and reads each ``/api/v1`` route's dependency tree for a dependency made by
one of the factories in :mod:`src.api.rbac_deps` (``require``, ``require_action``,
``require_management``, ``require_patient``), which tag what they build with
``RBAC_GATE_ATTRIBUTE``. A route with no gate must be on one of three **closed** lists below, each
entry with its reason:

* ``PUBLIC``: reachable without a session, on purpose (signing in, a signed link, a webhook that
  verifies its own signature, module metadata);
* ``SELF_SERVICE``: authenticated, and acting only on the caller's own account, which the session
  itself names;
* ``DECIDED_IN_THE_HANDLER``: the kernel's messaging, alerts and documents routes, whose resource
  key depends on the record (an alert folder, a document's owner type, thread participation), so
  the handler calls the RBAC resolver itself. Kernel code this project inherited, listed so it is
  visible; nothing new may be added to it.

A new route that is on none of them fails this test, and the fixture at the bottom proves the check
can fail. Reads route objects, not rendered output (``docs/IDE/RULES/testing-strategy.mdc``).
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute

from src.api.rbac_deps import RBAC_GATE_ATTRIBUTE, require
from src.core.security import get_current_user
from src.main import create_app

#: Reachable without a session, each for a stated reason.
PUBLIC: dict[str, str] = {
    "GET /api/v1/auth/config": "the sign-in page reads which methods are on",
    "POST /api/v1/auth/signup": "self-registration, behind SIGNUP_ENABLED",
    "GET /api/v1/auth/activate": "the emailed activation link: a typed, signed token",
    "POST /api/v1/auth/otp/request": "staff sign-in: asks for a code",
    "POST /api/v1/auth/otp/verify": "staff sign-in: proves the code",
    "POST /api/v1/auth/password/login": "staff sign-in",
    "POST /api/v1/auth/password/forgot": "starts a reset; answers the same for every address",
    "POST /api/v1/auth/password/reset": "the emailed reset link: a typed, signed, single-use token",
    "POST /api/v1/auth/refresh": "authenticated by the refresh cookie itself (Issue 16)",
    "POST /api/v1/auth/logout": "authenticated by the refresh cookie itself (Issue 16)",
    "GET /api/v1/auth/avatars/{avatar_token}": "an unguessable, per-upload token is the capability",
    "GET /api/v1/auth/email/change/confirm": "the emailed confirmation link: a typed, signed token",
    "GET /api/v1/audit/info": "module metadata",
    "GET /api/v1/documents/info": "module metadata",
    "GET /api/v1/widgets/info": "module metadata",
    "GET /api/v1/patients/info": "module metadata",
    "GET /api/v1/sites/info": "module metadata",
    "POST /api/v1/sites/register": (
        "the public clinic-registration form (Issue 29): whoever fills it in has no account, and "
        "a submission grants nothing at all — the clinic it creates is pending_verification, "
        "invisible to every patient-facing surface, and no role, session or staff membership comes "
        "with it. The worst a stranger can do is put an entry in a queue a platform admin reads"
    ),
    "GET /api/v1/sites/queues/info": "module metadata",
    "GET /api/v1/clinics/info": "module metadata",
    "GET /api/v1/clinics/nearby": (
        "clinic discovery (Issue 31): a patient looking for a clinic has no account, and the search "
        "returns only what verified clinics publish about themselves (name, address, hours, queue "
        "lengths), never a ticket, a patient or anything about an unverified clinic. The radius is "
        "capped on the server, and Issue 38 adds the rate limit against scraping"
    ),
    "GET /api/v1/clinics/{slug}": (
        "one verified clinic's public profile (Issue 35): what the clinic publishes about itself "
        "(hours, queue names and lengths, services, its own switchboard number); an unverified "
        "clinic answers the same 404 as a missing one"
    ),
    "GET /api/v1/clinics/areas": (
        "the place-name typeahead (Issue 34) a patient without GPS types a suburb into: it reads "
        "only the public OpenStreetMap place dataset, and nothing about any person"
    ),
    "GET /api/v1/sites/staff/info": "module metadata",
    "GET /api/v1/documents/download": "a signed, expiring, single-document link is the capability",
    "POST /api/v1/webhooks/stripe": "verifies the gateway's signature before anything else",
    "POST /api/v1/webhooks/paystack": "verifies the gateway's signature before anything else",
    "POST /api/v1/esign/webhook": "verifies the provider's signature before anything else",
    "POST /api/v1/notifications/webhooks/delivery": "verifies the provider's shared secret",
    "GET /api/v1/reference/enums": "the public wire vocabulary (Issue 4)",
    "GET /api/v1/tickets/{page_token}": (
        "the patient's ticket page (Issue 68): the link is the credential, 256 random bits that are "
        "never an id, and it opens one ticket's number, clinic and place in line to whoever holds it, "
        "so a patient can share it with family. Nothing about the patient is in it, and cancelling "
        "still needs the patient's own session on the cancel route"
    ),
    "POST /api/v1/patients/otp/request": "patient sign-in: asks for a code (Issue 17)",
    "POST /api/v1/patients/otp/verify": "patient sign-in: proves the code (Issue 17)",
    "GET /api/v1/staff/invitations/preview": (
        "the invitation link: a typed, signed token naming one row that decides everything "
        "about it (Issue 22) — the person opening it has no account yet, which is the point"
    ),
    "POST /api/v1/staff/invitations/accept": (
        "the invitation link again, this time spending it: single use, enforced on the row "
        "in the same transaction that creates the account (Issue 22)"
    ),
}

#: Authenticated, acting only on the caller's own account.
SELF_SERVICE: dict[str, str] = {
    **{
        f"{method} /api/v1/auth/me{suffix}": "the caller's own account"
        for method, suffix in (
            ("GET", ""),
            ("PATCH", ""),
            ("POST", "/avatar"),
            ("DELETE", "/avatar"),
            ("POST", "/password"),
            ("POST", "/email"),
            ("POST", "/resend-verification"),
            ("GET", "/sessions"),
            ("DELETE", "/sessions"),
            ("DELETE", "/sessions/{session_id}"),
        )
    },
    "GET /api/v1/notifications/preferences": "the caller's own notification preferences",
    "PUT /api/v1/notifications/preferences": "the caller's own notification preferences",
}

#: Kernel routes whose resource key depends on the record, so the handler resolves it. Closed.
DECIDED_IN_THE_HANDLER: dict[str, str] = {
    **dict.fromkeys(
        (
            "POST /api/v1/messaging/threads",
            "GET /api/v1/messaging/threads",
            "GET /api/v1/messaging/managed-threads",
            "DELETE /api/v1/messaging/threads/{thread_id}",
            "POST /api/v1/messaging/threads/{thread_id}/restore",
            "GET /api/v1/messaging/announcement-threads",
            "GET /api/v1/messaging/threads/{thread_id}",
            "POST /api/v1/messaging/threads/{thread_id}/messages",
            "POST /api/v1/messaging/threads/{thread_id}/read",
            "GET /api/v1/messaging/unread-count",
            "GET /api/v1/messaging/managed-unread-count",
            "POST /api/v1/messaging/announcements",
            "POST /api/v1/messaging/drafts",
            "GET /api/v1/messaging/drafts",
            "GET /api/v1/messaging/drafts/{draft_id}",
            "PATCH /api/v1/messaging/drafts/{draft_id}",
            "DELETE /api/v1/messaging/drafts/{draft_id}",
            "POST /api/v1/messaging/drafts/{draft_id}/send",
        ),
        "messaging: participant, or manager of the thread's resource (resolved per thread)",
    ),
    **dict.fromkeys(
        (
            "GET /api/v1/alerts",
            "POST /api/v1/alerts/drafts",
            "GET /api/v1/alerts/drafts",
            "GET /api/v1/alerts/drafts/{draft_id}",
            "PATCH /api/v1/alerts/drafts/{draft_id}",
            "DELETE /api/v1/alerts/drafts/{draft_id}",
            "POST /api/v1/alerts/drafts/{draft_id}/send",
        ),
        "alerts: the folder names the resource key (communications.alerts.<folder>)",
    ),
    **dict.fromkeys(
        (
            "POST /api/v1/documents",
            "GET /api/v1/documents",
            "GET /api/v1/documents/{document_id}",
            "POST /api/v1/documents/{document_id}/link",
            "DELETE /api/v1/documents/{document_id}",
        ),
        "documents: the owner type names the resource key (OWNER_TYPE_RESOURCE)",
    ),
}


def _walk(routes: list[object], prefix: str = "") -> Iterator[tuple[str, APIRoute]]:
    """``(full path, route)`` for every ``APIRoute`` in the router tree, through included routers."""
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _walk(
                included.routes, prefix + (route.include_context.prefix or "")
            )
        elif isinstance(route, APIRoute):
            yield prefix + route.path, route


def _calls(dependant: object) -> Iterator[object]:
    """Every dependency callable in a route's dependency tree."""
    for sub in dependant.dependencies:  # type: ignore[attr-defined]
        yield sub.call
        yield from _calls(sub)


def ungated_routes(app: FastAPI) -> dict[str, bool]:
    """``{"METHOD /path": authenticated?}`` for every ``/api/v1`` route with no gate dependency."""
    out: dict[str, bool] = {}
    for path, route in _walk(app.routes):
        if not path.startswith("/api/v1"):
            continue
        calls = list(_calls(route.dependant))
        if any(hasattr(call, RBAC_GATE_ATTRIBUTE) for call in calls):
            continue
        for method in sorted(route.methods or ()):
            out[f"{method} {path}"] = bool(calls)
    return out


def _findings(app: FastAPI) -> list[str]:
    """Every ungated route that is on no list, as a readable line."""
    allowed = PUBLIC.keys() | SELF_SERVICE.keys() | DECIDED_IN_THE_HANDLER.keys()
    return sorted(key for key in ungated_routes(app) if key not in allowed)


def test_every_api_route_is_gated_or_listed_with_its_reason() -> None:
    """The standing guard: a new ungated route fails here until it is gated (or listed)."""
    findings = _findings(create_app())
    assert not findings, (
        "API routes without a gate dependency (Depends(require(...))) and on no list:\n"
        + "\n".join(findings)
    )


def test_the_lists_hold_only_routes_that_exist_and_are_still_ungated() -> None:
    """A list entry whose route was gated or deleted must go, so the lists cannot rot open."""
    ungated = ungated_routes(create_app())
    for listed in (PUBLIC, SELF_SERVICE, DECIDED_IN_THE_HANDLER):
        stale = sorted(key for key in listed if key not in ungated)
        assert not stale, f"listed but gated or gone, remove from the list: {stale}"


def test_every_listed_route_carries_a_reason() -> None:
    """An exception is a decision on the record, not an omission."""
    for listed in (PUBLIC, SELF_SERVICE, DECIDED_IN_THE_HANDLER):
        assert all(reason.strip() for reason in listed.values())


def test_no_clinicq_module_decides_in_the_handler() -> None:
    """The in-handler list is inherited kernel code only; ClinicQ modules declare their gates."""
    clinicq = ("patients", "sites", "queues", "tickets", "staff", "consent")
    assert not [
        key for key in DECIDED_IN_THE_HANDLER if any(f"/{m}" in key for m in clinicq)
    ]


def test_the_check_fails_on_a_deliberately_ungated_route_and_passes_once_it_is_gated() -> (
    None
):
    """A guard that cannot fail passes forever: prove this one fails, and is discriminating."""
    app = create_app()
    router = APIRouter(prefix="/api/v1/queues")

    @router.post("/{queue_id}/call-next")
    def call_next(queue_id: str, claims: dict = Depends(get_current_user)) -> None:
        """A call-next route that forgot its gate."""

    @router.post(
        "/{queue_id}/recall",
        dependencies=[Depends(require("queues.call", "update"))],
    )
    def recall(queue_id: str) -> None:
        """The same route, gated."""

    app.include_router(router)
    findings = _findings(app)
    assert "POST /api/v1/queues/{queue_id}/call-next" in findings
    assert "POST /api/v1/queues/{queue_id}/recall" not in findings
