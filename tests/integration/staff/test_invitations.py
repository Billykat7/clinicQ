"""A clinic invites its own staff, and can switch an account off at once (Issue 22).

Every acceptance criterion, over real HTTP:

* a manager invites a receptionist, and the invitation stops working after 72 hours;
* an expired, revoked or already-used link cannot make an account — and the four refusals are
  indistinguishable from outside;
* deactivating someone revokes their session **immediately**: the test makes a request with an
  access token that is still perfectly valid and still gets 401;
* a deactivated account's audit rows stay intact and attributable;
* a staff member can change their own password and never their own role;
* only a clinic manager or a platform admin may invite, and only for their own clinic.

Per ``docs/IDE/RULES/testing-strategy.mdc`` these assert JSON, status codes and DB state, never HTML,
and build isolated ``Settings`` so a developer's ``.env`` cannot change the outcome.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import (
    AppEnvironment,
    AssignmentScopeType,
    AuditAction,
    AuditEntityType,
    UserRole,
)
from src.commons.time import APP_TIMEZONE, now_sast
from src.core import email_send, refresh_token_policy, security
from src.core.config import Settings, get_settings
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.core.security import create_access_token, hash_password
from src.core.site_scope import CROSS_SITE_REASON_HEADER
from src.database.models import (
    AuditEvent,
    Base,
    RefreshToken,
    StaffInvitation,
    User,
    UserRoleAssignment,
)
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app
from src.modules.staff import invitations as invitation_service
from tests.factories import FACTORY_STAFF_PASSWORD, StaffFactory

_SECRET = "invitations-test-secret-min-32-characters!"
_SITE_A = "0199b0c0-0000-7000-8000-0000000000aa"
_SITE_B = "0199b0c0-0000-7000-8000-0000000000bb"
_INVITED = "newcomer@clinicq.example"
_CHOSEN_PASSWORD = "a-password-they-chose"


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """Two clinics with a manager and a receptionist each, plus a platform admin.

    Invitation emails are captured rather than sent, so the test reads the link the same way the
    person invited would — from the message, never from a response body.
    """
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret=_SECRET,
        auth_password_login_enabled=True,
        smtp_host="",
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with factory() as db:
        sync_rbac_catalog(db)
        for name, role, site in (
            ("manager.a", UserRole.CLINIC_MANAGER, _SITE_A),
            ("manager.b", UserRole.CLINIC_MANAGER, _SITE_B),
            ("desk.a", UserRole.RECEPTIONIST, _SITE_A),
            # The operator ClinicQ puts on Clinic A's account: the platform role, held *there*.
            ("operator.a", UserRole.PLATFORM_ADMIN, _SITE_A),
        ):
            StaffFactory.create(
                db, email=f"{name}@clinicq.example", role=role, site_id=site
            )
        StaffFactory.create(
            db, email="operator@clinicq.example", role=UserRole.PLATFORM_ADMIN
        )
        db.commit()

    def _db() -> Generator[Session]:
        with factory() as db:
            yield db

    for module in (security, refresh_token_policy, email_send):
        monkeypatch.setattr(module, "get_settings", lambda: settings)

    sent: list[dict[str, str]] = []

    def _capture(
        to_email: str, invite_link: str, role: str, expire_hours: int | None = None
    ) -> None:
        sent.append({"to": to_email, "link": invite_link, "role": role})

    monkeypatch.setattr(email_send, "send_staff_invitation_email", _capture)
    monkeypatch.setattr(
        invitation_service, "send_staff_invitation_email", _capture, raising=False
    )

    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: settings

    def _client(email: str) -> TestClient:
        client = TestClient(app)
        signed_in = client.post(
            "/api/v1/auth/password/login",
            json={"email": email, "password": FACTORY_STAFF_PASSWORD},
        )
        assert signed_in.status_code == status.HTTP_200_OK, signed_in.text
        return client

    yield SimpleNamespace(
        app=app, session=factory, client=_client, settings=settings, sent=sent
    )
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _csrf(client: TestClient, settings: Settings) -> dict[str, str]:
    """The double-submit header for this client's session (read fresh: it rotates)."""
    return {"X-CSRF-Token": client.cookies.get(settings.csrf_cookie_name) or ""}


def _invite(
    ctx: SimpleNamespace,
    client: TestClient,
    *,
    site: str = _SITE_A,
    email: str = _INVITED,
    role: UserRole = UserRole.RECEPTIONIST,
    phone: str | None = None,
):  # type: ignore[no-untyped-def]
    """Issue one invitation as ``client``."""
    body: dict[str, object] = {"email": email, "role": role.value}
    if phone is not None:
        body["phone"] = phone
    return client.post(
        f"/api/v1/sites/{site}/staff/invitations",
        json=body,
        headers=_csrf(client, ctx.settings),
    )


def _token_from_link(link: str) -> str:
    """The token out of an invitation link, the way the person invited gets it."""
    return link.split("token=", 1)[1]


def _accept(ctx: SimpleNamespace, token: str, **overrides: object):  # type: ignore[no-untyped-def]
    """Spend an invitation."""
    body = {"token": token, "password": _CHOSEN_PASSWORD, **overrides}
    return TestClient(ctx.app).post("/api/v1/staff/invitations/accept", json=body)


def _user(db: Session, email: str) -> User | None:
    """The account at ``email``, if there is one."""
    return db.execute(select(User).where(User.email == email)).scalar_one_or_none()


# --- inviting ---------------------------------------------------------------------------


def test_a_manager_invites_a_receptionist_and_the_link_goes_to_them(
    ctx: SimpleNamespace,
) -> None:
    """The invitation is recorded for the manager's own clinic, and the link is sent, not returned."""
    invited = _invite(ctx, ctx.client("manager.a@clinicq.example"))
    assert invited.status_code == status.HTTP_201_CREATED, invited.text
    body = invited.json()
    assert body["email"] == _INVITED
    assert body["role"] == UserRole.RECEPTIONIST.value
    assert body["site_id"] == _SITE_A
    assert body["state"] == "pending"
    # The link is a credential: it reaches the person invited, and no response body.
    assert "token" not in invited.text
    assert len(ctx.sent) == 1 and ctx.sent[0]["to"] == _INVITED
    assert "/invite?token=" in ctx.sent[0]["link"]

    with ctx.session() as db:
        deadline = db.execute(select(StaffInvitation.expires_at)).scalar_one()
    # 72 hours, the issue's number, from the settings default. Measured against business time
    # rather than against ``created_at``, whose SQLite server default is UTC while the deadline
    # this code writes is Africa/Johannesburg — comparing the two would measure the offset.
    left = deadline.replace(tzinfo=deadline.tzinfo or APP_TIMEZONE) - now_sast()
    assert timedelta(hours=71) < left < timedelta(hours=73)


def test_the_invitation_is_audited_with_the_clinic_and_never_the_link(
    ctx: SimpleNamespace,
) -> None:
    """The trail says who invited whom, into what. The link is not in it."""
    _invite(ctx, ctx.client("manager.a@clinicq.example"))
    with ctx.session() as db:
        row = db.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == AuditEntityType.STAFF_INVITATION.value
            )
        ).scalar_one()
        action, actor, site_id, context = (
            row.action,
            row.actor,
            row.site_id,
            row.context or "",
        )
    assert action == AuditAction.CREATE.value
    assert actor == "manager.a@clinicq.example"
    assert site_id == _SITE_A
    assert _INVITED in context and UserRole.RECEPTIONIST.value in context
    assert "token" not in context


def test_a_receptionist_cannot_invite_anyone(ctx: SimpleNamespace) -> None:
    """``sites.staff:read`` is not ``create``: the front desk reads colleagues, it does not hire."""
    refused = _invite(ctx, ctx.client("desk.a@clinicq.example"))
    assert refused.status_code == status.HTTP_403_FORBIDDEN


def test_a_manager_cannot_invite_into_another_clinic(ctx: SimpleNamespace) -> None:
    """The site guard answers first: Clinic B is a 404 for Clinic A's manager (Issue 19)."""
    refused = _invite(ctx, ctx.client("manager.a@clinicq.example"), site=_SITE_B)
    assert refused.status_code == status.HTTP_404_NOT_FOUND
    assert ctx.sent == []


def test_nobody_hands_out_a_role_they_do_not_hold(ctx: SimpleNamespace) -> None:
    """A clinic manager cannot mint a platform admin; a platform admin at the clinic can.

    The discriminating half matters: without it "403 for everyone" would pass this test.
    """
    refused = _invite(
        ctx, ctx.client("manager.a@clinicq.example"), role=UserRole.PLATFORM_ADMIN
    )
    assert refused.status_code == status.HTTP_403_FORBIDDEN
    assert ctx.sent == []

    allowed = _invite(
        ctx,
        ctx.client("operator.a@clinicq.example"),
        role=UserRole.PLATFORM_ADMIN,
        email="operator2@clinicq.example",
    )
    assert allowed.status_code == status.HTTP_201_CREATED, allowed.text


def test_a_platform_admin_invites_only_where_they_hold_their_role(
    ctx: SimpleNamespace,
) -> None:
    """Issue 19's rule holds here too: a cross-site *write* is refused, reason header or not.

    So "only a clinic manager or platform admin can issue invitations **for their site**" is true
    in both halves — the role decides what, the assignment decides where. Bootstrapping the first
    manager of a brand-new clinic therefore belongs with site onboarding (Issue 23), not here.
    """
    unassigned = ctx.client("operator@clinicq.example")
    assert _invite(ctx, unassigned).status_code == status.HTTP_404_NOT_FOUND
    with_a_reason = unassigned.post(
        f"/api/v1/sites/{_SITE_A}/staff/invitations",
        json={"email": _INVITED, "role": UserRole.RECEPTIONIST.value},
        headers={
            **_csrf(unassigned, ctx.settings),
            CROSS_SITE_REASON_HEADER: "onboarding call",
        },
    )
    assert with_a_reason.status_code == status.HTTP_404_NOT_FOUND
    assert ctx.sent == []


def test_a_patient_is_not_a_role_a_clinic_invites_people_into(
    ctx: SimpleNamespace,
) -> None:
    """The vocabulary is closed: staff roles only, so an invitation cannot make a patient account."""
    refused = _invite(
        ctx, ctx.client("manager.a@clinicq.example"), role=UserRole.PATIENT
    )
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_someone_who_already_works_here_is_not_invited_twice(
    ctx: SimpleNamespace,
) -> None:
    """A colleague is already here; the answer says so rather than issuing a second link."""
    refused = _invite(
        ctx, ctx.client("manager.a@clinicq.example"), email="desk.a@clinicq.example"
    )
    assert refused.status_code == status.HTTP_409_CONFLICT


# --- accepting --------------------------------------------------------------------------


def test_accepting_makes_an_account_holding_the_role_at_that_clinic_only(
    ctx: SimpleNamespace,
) -> None:
    """The role is held **at the site**, never unscoped: one clinic, not all of them (Issue 15)."""
    _invite(ctx, ctx.client("manager.a@clinicq.example"))
    accepted = _accept(
        ctx,
        _token_from_link(ctx.sent[0]["link"]),
        first_name="Naledi",
        last_name="Dube",
    )
    assert accepted.status_code == status.HTTP_200_OK, accepted.text
    assert accepted.json()["site_id"] == _SITE_A

    with ctx.session() as db:
        user = _user(db, _INVITED)
        assert user is not None and user.is_verified and user.is_active
        assert user.first_name == "Naledi"
        assignments = (
            db.execute(
                select(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id)
            )
            .scalars()
            .all()
        )
        shapes = [(a.role, a.scope_type, a.scope_id) for a in assignments]
    assert shapes == [
        (UserRole.RECEPTIONIST.value, AssignmentScopeType.SITE.value, _SITE_A)
    ]

    # And the account works: the password they chose signs them in.
    signed_in = TestClient(ctx.app).post(
        "/api/v1/auth/password/login",
        json={"email": _INVITED, "password": _CHOSEN_PASSWORD},
    )
    assert signed_in.status_code == status.HTTP_200_OK, signed_in.text


def test_an_invitation_cannot_be_spent_twice(ctx: SimpleNamespace) -> None:
    """Single use. The second attempt gets the same answer as a link that never existed."""
    _invite(ctx, ctx.client("manager.a@clinicq.example"))
    token = _token_from_link(ctx.sent[0]["link"])
    assert _accept(ctx, token).status_code == status.HTTP_200_OK

    replayed = _accept(ctx, token)
    assert replayed.status_code == status.HTTP_400_BAD_REQUEST
    assert replayed.json()["detail"] == invitation_service.NOT_USABLE
    nonsense = _accept(ctx, "not-a-token-at-all")
    assert nonsense.json()["detail"] == replayed.json()["detail"]

    with ctx.session() as db:
        assignments = (
            db.execute(
                select(UserRoleAssignment)
                .join(User, User.id == UserRoleAssignment.user_id)
                .where(User.email == _INVITED)
            )
            .scalars()
            .all()
        )
    assert len(assignments) == 1  # the replay granted nothing


def test_an_expired_invitation_cannot_activate_an_account(ctx: SimpleNamespace) -> None:
    """Past 72 hours the link is dead — proven by moving the row's deadline, not the clock."""
    _invite(ctx, ctx.client("manager.a@clinicq.example"))
    token = _token_from_link(ctx.sent[0]["link"])
    with ctx.session() as db:
        invitation = db.execute(select(StaffInvitation)).scalar_one()
        invitation.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

    refused = _accept(ctx, token)
    assert refused.status_code == status.HTTP_400_BAD_REQUEST
    assert refused.json()["detail"] == invitation_service.NOT_USABLE
    with ctx.session() as db:
        assert _user(db, _INVITED) is None


def test_a_revoked_invitation_cannot_activate_an_account(ctx: SimpleNamespace) -> None:
    """A manager who invited the wrong person can take it back before it is used."""
    manager = ctx.client("manager.a@clinicq.example")
    invitation_id = _invite(ctx, manager).json()["id"]
    token = _token_from_link(ctx.sent[0]["link"])

    revoked = manager.delete(
        f"/api/v1/sites/{_SITE_A}/staff/invitations/{invitation_id}",
        headers=_csrf(manager, ctx.settings),
    )
    assert revoked.status_code == status.HTTP_200_OK, revoked.text
    assert revoked.json()["state"] == "revoked"

    assert _accept(ctx, token).status_code == status.HTTP_400_BAD_REQUEST
    with ctx.session() as db:
        assert _user(db, _INVITED) is None


def test_the_preview_says_what_the_invitation_is_for_and_nothing_more(
    ctx: SimpleNamespace,
) -> None:
    """The acceptance page can name the role and the clinic before anyone has signed in."""
    _invite(ctx, ctx.client("manager.a@clinicq.example"))
    token = _token_from_link(ctx.sent[0]["link"])
    preview = TestClient(ctx.app).get(
        "/api/v1/staff/invitations/preview", params={"token": token}
    )
    assert preview.status_code == status.HTTP_200_OK, preview.text
    assert preview.json() == {
        "email": _INVITED,
        "role": UserRole.RECEPTIONIST.value,
        # The words the page shows come from the server, so "nurse_doctor" never reaches a reader.
        "role_label": "receptionist",
        "site_id": _SITE_A,
        "expires_at": preview.json()["expires_at"],
    }


def test_someone_who_already_has_an_account_keeps_their_own_password(
    ctx: SimpleNamespace,
) -> None:
    """A nurse who works at Clinic B accepts Clinic A's invitation: a second role, not a new login."""
    manager_b = ctx.client("manager.b@clinicq.example")
    assert (
        _invite(
            ctx,
            manager_b,
            site=_SITE_B,
            email="desk.a@clinicq.example",
            role=UserRole.NURSE_DOCTOR,
        ).status_code
        == status.HTTP_201_CREATED
    )
    accepted = _accept(ctx, _token_from_link(ctx.sent[0]["link"]))
    assert accepted.status_code == status.HTTP_200_OK, accepted.text

    # Their original password still works; the one in the acceptance body did nothing.
    fresh = TestClient(ctx.app)
    assert (
        fresh.post(
            "/api/v1/auth/password/login",
            json={
                "email": "desk.a@clinicq.example",
                "password": FACTORY_STAFF_PASSWORD,
            },
        ).status_code
        == status.HTTP_200_OK
    )
    assert (
        TestClient(ctx.app)
        .post(
            "/api/v1/auth/password/login",
            json={"email": "desk.a@clinicq.example", "password": _CHOSEN_PASSWORD},
        )
        .status_code
        == status.HTTP_401_UNAUTHORIZED
    )
    with ctx.session() as db:
        user = _user(db, "desk.a@clinicq.example")
        assert user is not None
        held = {
            (a.role, a.scope_id)
            for a in db.execute(
                select(UserRoleAssignment).where(UserRoleAssignment.user_id == user.id)
            )
            .scalars()
            .all()
        }
    assert held == {
        (UserRole.RECEPTIONIST.value, _SITE_A),
        (UserRole.NURSE_DOCTOR.value, _SITE_B),
    }


# --- deactivation -----------------------------------------------------------------------


def test_deactivating_stops_a_still_valid_access_token_on_the_very_next_request(
    ctx: SimpleNamespace,
) -> None:
    """The criterion, at its hardest point: the token is minted here and has not expired."""
    desk = ctx.client("desk.a@clinicq.example")
    with ctx.session() as db:
        member = _user(db, "desk.a@clinicq.example")
        assert member is not None
        member_id = str(member.id)
    live_token = create_access_token(
        sub="desk.a@clinicq.example", email="desk.a@clinicq.example", uid=member_id
    )
    bearer = {"Authorization": f"Bearer {live_token}"}
    assert (
        TestClient(ctx.app).get("/api/v1/auth/me", headers=bearer).status_code
        == status.HTTP_200_OK
    )

    manager = ctx.client("manager.a@clinicq.example")
    switched_off = manager.put(
        f"/api/v1/sites/{_SITE_A}/staff/{member_id}/active",
        json={"is_active": False},
        headers=_csrf(manager, ctx.settings),
    )
    assert switched_off.status_code == status.HTTP_200_OK, switched_off.text
    assert switched_off.json()["is_active"] is False

    # Same token, still signed, still unexpired — and refused.
    assert (
        TestClient(ctx.app).get("/api/v1/auth/me", headers=bearer).status_code
        == status.HTTP_401_UNAUTHORIZED
    )

    # Their sessions are revoked by the deactivation itself, not by their next attempt to use
    # one: read the rows before anything touches the refresh endpoint, or this assertion would
    # pass on a deactivation that revoked nothing.
    with ctx.session() as db:
        live = (
            db.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == member_id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
    assert live == []

    # And so a new access token cannot be minted from the cookie they still hold.
    refreshed = desk.post("/api/v1/auth/refresh", headers=_csrf(desk, ctx.settings))
    assert refreshed.status_code == status.HTTP_401_UNAUTHORIZED, refreshed.text


def test_a_deactivated_persons_audit_rows_stay_intact_and_attributable(
    ctx: SimpleNamespace,
) -> None:
    """Switching an account off is not deleting it: what they did is still on the record."""
    desk = ctx.client("desk.a@clinicq.example")
    assert desk.get(f"/api/v1/sites/{_SITE_A}/staff").status_code == status.HTTP_200_OK
    with ctx.session() as db:
        member = _user(db, "desk.a@clinicq.example")
        assert member is not None
        member_id = str(member.id)
        before = (
            db.execute(
                select(AuditEvent).where(AuditEvent.actor == "desk.a@clinicq.example")
            )
            .scalars()
            .all()
        )
        rows_before = [(row.id, row.actor, row.action) for row in before]

    manager = ctx.client("manager.a@clinicq.example")
    manager.put(
        f"/api/v1/sites/{_SITE_A}/staff/{member_id}/active",
        json={"is_active": False},
        headers=_csrf(manager, ctx.settings),
    )

    with ctx.session() as db:
        after = (
            db.execute(
                select(AuditEvent).where(AuditEvent.actor == "desk.a@clinicq.example")
            )
            .scalars()
            .all()
        )
        rows_after = [(row.id, row.actor, row.action) for row in after]
        deactivation = db.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == AuditEntityType.USER.value,
                AuditEvent.entity_id == member_id,
            )
        ).scalar_one()
        # The account row is still there to attribute them to.
        still_there = _user(db, "desk.a@clinicq.example")
        assert still_there is not None and not still_there.is_active
    assert rows_after == rows_before
    assert deactivation.actor == "manager.a@clinicq.example"
    assert "sessions revoked" in (deactivation.context or "")


def test_reactivating_gives_the_account_back(ctx: SimpleNamespace) -> None:
    """The flag goes both ways — and the guard is discriminating rather than always-off."""
    with ctx.session() as db:
        member = _user(db, "desk.a@clinicq.example")
        assert member is not None
        member_id = str(member.id)
    manager = ctx.client("manager.a@clinicq.example")
    for active in (False, True):
        response = manager.put(
            f"/api/v1/sites/{_SITE_A}/staff/{member_id}/active",
            json={"is_active": active},
            headers=_csrf(manager, ctx.settings),
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json()["is_active"] is active

    assert (
        TestClient(ctx.app)
        .post(
            "/api/v1/auth/password/login",
            json={
                "email": "desk.a@clinicq.example",
                "password": FACTORY_STAFF_PASSWORD,
            },
        )
        .status_code
        == status.HTTP_200_OK
    )


def test_nobody_switches_off_their_own_account(ctx: SimpleNamespace) -> None:
    """A manager who locks themselves out needs the platform team, which is the loop being removed."""
    manager = ctx.client("manager.a@clinicq.example")
    with ctx.session() as db:
        me = _user(db, "manager.a@clinicq.example")
        assert me is not None
        my_id = str(me.id)
    refused = manager.put(
        f"/api/v1/sites/{_SITE_A}/staff/{my_id}/active",
        json={"is_active": False},
        headers=_csrf(manager, ctx.settings),
    )
    assert refused.status_code == status.HTTP_400_BAD_REQUEST


def test_a_manager_cannot_deactivate_someone_at_another_clinic(
    ctx: SimpleNamespace,
) -> None:
    """Another clinic's staff member is a 404, the same as an id that names nobody."""
    with ctx.session() as db:
        stranger = _user(db, "manager.b@clinicq.example")
        assert stranger is not None
        stranger_id = str(stranger.id)
    manager = ctx.client("manager.a@clinicq.example")
    refused = manager.put(
        f"/api/v1/sites/{_SITE_A}/staff/{stranger_id}/active",
        json={"is_active": False},
        headers=_csrf(manager, ctx.settings),
    )
    assert refused.status_code == status.HTTP_404_NOT_FOUND


# --- your own account, and only your own -------------------------------------------------


def test_a_staff_member_changes_their_own_password_but_never_their_own_role(
    ctx: SimpleNamespace,
) -> None:
    """Both halves of the criterion, on the server: the password changes, the role cannot."""
    desk = ctx.client("desk.a@clinicq.example")
    changed = desk.post(
        "/api/v1/auth/me/password",
        json={
            "current_password": FACTORY_STAFF_PASSWORD,
            "new_password": "a-brand-new-password",
        },
        headers=_csrf(desk, ctx.settings),
    )
    assert changed.status_code == status.HTTP_200_OK, changed.text

    # The profile endpoint has no role field, and smuggling one in changes nothing.
    smuggled = desk.patch(
        "/api/v1/auth/me",
        json={"first_name": "Desk", "role": UserRole.PLATFORM_ADMIN.value},
        headers=_csrf(desk, ctx.settings),
    )
    assert smuggled.status_code == status.HTTP_200_OK, smuggled.text
    with ctx.session() as db:
        member = _user(db, "desk.a@clinicq.example")
        assert member is not None
        assert member.role == UserRole.RECEPTIONIST.value
        roles = {
            row.role
            for row in db.execute(
                select(UserRoleAssignment).where(
                    UserRoleAssignment.user_id == member.id
                )
            )
            .scalars()
            .all()
        }
    assert roles == {UserRole.RECEPTIONIST.value}

    # And the RBAC assignment API, which *can* change a role, is not theirs to call.
    refused = desk.post(
        "/api/v1/admin/rbac/users/whoever/assignments",
        json={"role": UserRole.PLATFORM_ADMIN.value},
        headers=_csrf(desk, ctx.settings),
    )
    assert refused.status_code == status.HTTP_403_FORBIDDEN


def test_the_invitation_list_shows_each_ones_state(ctx: SimpleNamespace) -> None:
    """A manager sees what is outstanding: pending, accepted, revoked and expired, derived."""
    manager = ctx.client("manager.a@clinicq.example")
    _invite(ctx, manager)
    _invite(ctx, manager, email="second@clinicq.example")
    _accept(ctx, _token_from_link(ctx.sent[0]["link"]))

    listed = manager.get(f"/api/v1/sites/{_SITE_A}/staff/invitations")
    assert listed.status_code == status.HTTP_200_OK, listed.text
    states = {item["email"]: item["state"] for item in listed.json()["items"]}
    assert states == {_INVITED: "accepted", "second@clinicq.example": "pending"}
    assert all("token" not in item for item in listed.text.split())


def test_an_invitation_by_sms_keeps_the_link_off_the_ledger(
    ctx: SimpleNamespace,
) -> None:
    """The link is a credential, so the delivery row records the send and never the link (Issue 17)."""
    from src.database.models import Notification

    invited = _invite(ctx, ctx.client("manager.a@clinicq.example"), phone="0821234567")
    assert invited.status_code == status.HTTP_201_CREATED, invited.text
    with ctx.session() as db:
        row = db.execute(select(Notification)).scalar_one()
        payload, recipient = row.payload, row.recipient
    assert recipient == "+27821234567"
    assert "token" not in str(payload)
    assert str(payload.get("link")) != ctx.sent[0]["link"]


def test_a_signed_out_visitor_cannot_invite_or_deactivate(ctx: SimpleNamespace) -> None:
    """The gates are on the routes, not on the page that renders the buttons."""
    anonymous = TestClient(ctx.app)
    assert anonymous.post(
        f"/api/v1/sites/{_SITE_A}/staff/invitations",
        json={"email": _INVITED, "role": UserRole.RECEPTIONIST.value},
    ).status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}
    assert anonymous.put(
        f"/api/v1/sites/{_SITE_A}/staff/whoever/active", json={"is_active": False}
    ).status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}


def test_the_password_a_newcomer_chooses_is_stored_hashed(ctx: SimpleNamespace) -> None:
    """A credential is never readable from the row that holds it (Issue 15)."""
    _invite(ctx, ctx.client("manager.a@clinicq.example"))
    _accept(ctx, _token_from_link(ctx.sent[0]["link"]))
    with ctx.session() as db:
        stored = db.execute(
            select(User.password).where(User.email == _INVITED)
        ).scalar_one()
    assert stored is not None
    assert _CHOSEN_PASSWORD not in stored
    assert stored.startswith("$2")  # bcrypt, not the password and not a digest of it
    assert stored != hash_password(_CHOSEN_PASSWORD)  # salted: two hashes never match
