"""The dashboard frame over HTTP: navigation per role and per clinic, the switcher, sign-in (Issue 48).

What each acceptance criterion needs proving, and how this file proves it without reading HTML
(``docs/IDE/RULES/testing-strategy.mdc``): pages are asserted by **status code and redirect**, and what
a page offers by the ``clinic`` frame the server handed its template (``response.context``), which is
the data the rail and the tabs render and nothing else.

* A receptionist, a nurse and a clinic manager each get a different, correct set of screens.
* A screen a role may not open is **refused** (403), not merely missing from the menu; another clinic's
  screen is **not found** (404), the same as an id that does not exist.
* The menu follows the grants: changing one grant changes the menu, with no code or template edit.
* A staff member at two clinics switches without signing in again, and lands on the same screen
  scoped to the new clinic, or on its first screen if that one is not theirs there.
* A signed-out visit to a clinic page is sent to sign in with that page as ``next``, and the page
  opens once they have signed in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import delete
from starlette import status

from src.commons.enums import GrantScope, PermissionVerb, UserRole
from src.database.models import RolePermission
from src.web.dashboard.shell import SITE_COOKIE_NAME, ClinicShell


def _shell(response) -> ClinicShell:
    """The frame the server rendered the page with."""
    assert response.status_code == status.HTTP_200_OK, response.text[:200]
    shell = response.context["clinic"]
    assert isinstance(shell, ClinicShell)
    return shell


def _keys(shell: ClinicShell) -> list[str]:
    """The screens the frame offers, in the order it shows them."""
    return [link.key for link in shell.links]


def _follow(client, path: str):
    """GET ``path`` and follow local redirects, returning the final response."""
    response = client.get(path)
    for _ in range(5):
        if response.status_code != status.HTTP_302_FOUND:
            return response
        response = client.get(response.headers["location"])
    raise AssertionError("too many redirects")


def test_a_receptionist_a_nurse_and_a_manager_each_get_their_own_screens(
    dashboard: SimpleNamespace,
) -> None:
    """``/dashboard`` opens each person's clinic on their first screen, with their own menu."""
    expected = {
        "desk.a": (["board"], "board"),
        "nurse.a": (["room"], "room"),
        "manager.a": (["board", "clinic_settings"], "board"),
    }
    menus = {}
    for name, (keys, landing) in expected.items():
        client = dashboard.client(name)
        assert client.get("/dashboard").headers["location"] == dashboard.page(
            dashboard.site_a
        )
        opened = _follow(client, "/dashboard")
        shell = _shell(opened)
        assert _keys(shell) == keys, name
        assert urlsplit(str(opened.url)).path == dashboard.page(
            dashboard.site_a, landing
        )
        assert shell.site.id == dashboard.site_a
        menus[name] = tuple(keys)
    assert len(set(menus.values())) == 3


def test_the_frame_names_the_clinic_and_who_is_signed_in_with_their_role_there(
    dashboard: SimpleNamespace,
) -> None:
    """The persistent header's data: the clinic's name, the person, and their role at this clinic."""
    shell = _shell(
        dashboard.client("both").get(dashboard.page(dashboard.site_b, "board"))
    )
    assert shell.site.name == "Alex Clinic"
    assert shell.display_name.startswith("Both")
    assert shell.role_labels == ("clinic manager",)
    # And at the other clinic the same person is the front desk, not a manager.
    shell_a = _shell(
        dashboard.client("both").get(dashboard.page(dashboard.site_a, "board"))
    )
    assert shell_a.role_labels == ("receptionist",)


def test_a_screen_a_role_may_not_open_is_refused_not_just_left_out_of_the_menu(
    dashboard: SimpleNamespace,
) -> None:
    """Guessing the URL of a screen that is not yours answers 403 inside the shell."""
    site = dashboard.site_a
    refusals = {
        "desk.a": ("room", "settings"),
        "nurse.a": ("board", "settings"),
        "manager.a": ("room",),
    }
    for name, sections in refusals.items():
        client = dashboard.client(name)
        for section in sections:
            assert (
                client.get(dashboard.page(site, section)).status_code
                == status.HTTP_403_FORBIDDEN
            ), (name, section)
    # Refused by the grant, not by the page's position under "settings": a receptionist may read
    # the board's display mode (Issue 27), so that page still opens for them.
    assert (
        dashboard.client("desk.a")
        .get(dashboard.page(site, "settings/display"))
        .status_code
        == status.HTTP_200_OK
    )


def test_another_clinics_screens_are_not_found_like_an_id_that_does_not_exist(
    dashboard: SimpleNamespace,
) -> None:
    """Non-negotiable 3: a 404 for clinic B and for a made-up id, never a 403 that confirms B."""
    desk = dashboard.client("desk.a")
    unknown = "0199b0c0-0000-7000-8000-0000000dffff"
    for site_id in (dashboard.site_b, unknown):
        for section in ("", "board", "room", "settings"):
            assert (
                desk.get(dashboard.page(site_id, section)).status_code
                == status.HTTP_404_NOT_FOUND
            ), (site_id, section)
    # The operator works at no clinic, so the clinic pages are not theirs either.
    operator = dashboard.client("operator")
    assert (
        operator.get(dashboard.page(dashboard.site_a, "board")).status_code
        == status.HTTP_404_NOT_FOUND
    )
    assert operator.get("/dashboard").status_code == status.HTTP_200_OK


def test_switching_clinics_reopens_the_board_scoped_to_the_new_clinic_without_signing_in(
    dashboard: SimpleNamespace,
) -> None:
    """One sign-in; the switcher's link reloads the front desk with clinic B's queues."""
    client = dashboard.client("both")
    at_a = _shell(client.get(dashboard.page(dashboard.site_a, "board")))
    assert [
        queue.id
        for queue in client.get(dashboard.page(dashboard.site_a, "board")).context[
            "queues"
        ]
    ] == [
        dashboard.triage,
        dashboard.pharmacy,
    ]
    (to_b,) = at_a.other_sites
    assert to_b.id == dashboard.site_b
    assert parse_qs(urlsplit(to_b.href).query) == {"section": ["board"]}

    switched = client.get(to_b.href)
    assert switched.status_code == status.HTTP_302_FOUND
    assert switched.headers["location"] == dashboard.page(dashboard.site_b, "board")
    board_b = client.get(switched.headers["location"])
    shell_b = _shell(board_b)
    assert shell_b.site.id == dashboard.site_b
    assert [queue.id for queue in board_b.context["queues"]] == [dashboard.other_triage]
    # At clinic B this person is the manager, so the menu changes with the clinic.
    assert _keys(shell_b) == ["board", "clinic_settings"]
    assert _keys(at_a) == ["board"]
    # And /dashboard now reopens clinic B: the switch was remembered, not just followed.
    assert client.cookies.get(SITE_COOKIE_NAME) == dashboard.site_b
    assert client.get("/dashboard").headers["location"] == dashboard.page(
        dashboard.site_b
    )


def test_a_switch_to_a_screen_not_open_at_the_other_clinic_lands_on_its_first_screen(
    dashboard: SimpleNamespace,
) -> None:
    """From the settings at clinic B (manager) to clinic A (receptionist): the front desk, not a 403."""
    client = dashboard.client("both")
    settings_b = client.get(dashboard.page(dashboard.site_b, "settings/display"))
    (to_a,) = _shell(settings_b).other_sites
    assert parse_qs(urlsplit(to_a.href).query) == {"section": ["clinic_settings"]}
    assert client.get(to_a.href).headers["location"] == dashboard.page(
        dashboard.site_a, "board"
    )
    # A section that is not a screen at all is ignored the same way.
    assert client.get(dashboard.page(dashboard.site_a) + "?section=rbac").headers[
        "location"
    ] == dashboard.page(dashboard.site_a, "board")


def test_a_remembered_clinic_the_caller_does_not_work_at_is_ignored(
    dashboard: SimpleNamespace,
) -> None:
    """The cookie is a preference: clinic B's id in it does not send clinic A's receptionist there."""
    desk = dashboard.client("desk.a")
    desk.cookies.set(SITE_COOKIE_NAME, dashboard.site_b)
    assert desk.get("/dashboard").headers["location"] == dashboard.page(
        dashboard.site_a
    )


def test_the_menu_follows_a_grant_change_with_no_code_change(
    dashboard: SimpleNamespace,
) -> None:
    """Grant the receptionist role the room's resource: the room appears and opens. Remove it: gone."""
    desk = dashboard.client("desk.a")
    board = dashboard.page(dashboard.site_a, "board")
    assert _keys(_shell(desk.get(board))) == ["board"]

    with dashboard.session() as db:
        db.add(
            RolePermission(
                role=UserRole.RECEPTIONIST.value,
                resource="visits.notes",
                max_verb=PermissionVerb.UPDATE.value,
                scope=GrantScope.OWN.value,
                created_at=datetime.now(UTC),
            )
        )
        db.commit()
    assert _keys(_shell(desk.get(board))) == ["board", "room"]
    assert (
        desk.get(dashboard.page(dashboard.site_a, "room")).status_code
        == status.HTTP_200_OK
    )

    with dashboard.session() as db:
        db.execute(
            delete(RolePermission).where(
                RolePermission.role == UserRole.RECEPTIONIST.value,
                RolePermission.resource == "visits.notes",
            )
        )
        db.commit()
    assert _keys(_shell(desk.get(board))) == ["board"]
    assert (
        desk.get(dashboard.page(dashboard.site_a, "room")).status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_the_room_reads_only_the_queues_the_nurse_is_assigned_to(
    dashboard: SimpleNamespace,
) -> None:
    """Triage is Room 2's; the Pharmacy is at the same clinic and is not read for this page."""
    room = dashboard.client("nurse.a").get(dashboard.page(dashboard.site_a, "room"))
    _shell(room)
    assert [queue.id for queue in room.context["queues"]] == [dashboard.triage]


def test_a_signed_out_visit_goes_to_sign_in_and_the_page_opens_after_it(
    dashboard: SimpleNamespace,
) -> None:
    """The redirect carries the page (and its query) as ``next``; signing in then opens it."""
    board = dashboard.page(dashboard.site_a, "board")
    refused = dashboard.anonymous().get(f"{board}?queue=triage")
    assert refused.status_code == status.HTTP_302_FOUND
    location = urlsplit(refused.headers["location"])
    assert location.path == "/"
    query = parse_qs(location.query)
    assert query["next"] == [f"{board}?queue=triage"]
    assert query["openSignin"] == ["1"]

    desk = dashboard.client("desk.a")
    assert desk.get(query["next"][0]).status_code == status.HTTP_200_OK
