"""The platform admin's clinics console: ``/admin/clinics`` (Issue 222).

Every verb a clinic needs has been in the sites API since Issue 23 and none of them had a screen.
These assert the screen's **decisions** — which tab, which clinics, which buttons — as data on the
rendered context rather than by reading HTML (``docs/IDE/RULES/testing-strategy.mdc``). What the
buttons then do is the API's, and is tested in ``test_site_profile_api.py``; the browser
walk-through is in the pull request.
"""

from __future__ import annotations

from types import SimpleNamespace

from starlette import status

from src.commons.enums import SiteSector, SiteStatus
from src.database.models import Site

_CONSOLE = "/admin/clinics"


def _open(clinics: SimpleNamespace, who: str, path: str = f"{_CONSOLE}/all"):  # type: ignore[no-untyped-def]
    """Open the console as ``who``, following the redirect the bare URL gives."""
    return clinics.client(who).get(path, follow_redirects=False)


# --- the tabs are URLs -------------------------------------------------------------------------


def test_the_bare_console_url_redirects_to_its_default_tab(
    clinics: SimpleNamespace,
) -> None:
    """A tab is a real URL, so a refresh or a deep link lands on the right view."""
    landed = _open(clinics, "operator@clinicq.example", _CONSOLE)

    assert landed.status_code == status.HTTP_302_FOUND
    assert landed.headers["location"] == f"{_CONSOLE}/all"


def test_a_tab_nobody_has_heard_of_lands_on_the_default_rather_than_404ing(
    clinics: SimpleNamespace,
) -> None:
    """The console's own rule, and the verification console's: an unknown section redirects."""
    landed = _open(clinics, "operator@clinicq.example", f"{_CONSOLE}/nonsense")

    assert landed.status_code == status.HTTP_302_FOUND
    assert landed.headers["location"] == f"{_CONSOLE}/all"


def test_each_tab_shows_exactly_the_clinics_with_that_listing(
    clinics: SimpleNamespace,
) -> None:
    """``all`` is every clinic; each other tab is one ``SiteStatus``."""
    with clinics.session() as db:
        site = db.get(Site, clinics.site_b)
        assert site is not None
        site.status = SiteStatus.DRAFT.value
        db.commit()

    everything = _open(clinics, "operator@clinicq.example")
    drafts = _open(clinics, "operator@clinicq.example", f"{_CONSOLE}/draft")
    verified = _open(clinics, "operator@clinicq.example", f"{_CONSOLE}/verified")

    assert {row.id for row in everything.context["listing"].items} == {  # type: ignore[attr-defined]
        clinics.site_a,
        clinics.site_b,
    }
    assert [row.id for row in drafts.context["listing"].items] == [clinics.site_b]  # type: ignore[attr-defined]
    assert [row.id for row in verified.context["listing"].items] == [clinics.site_a]  # type: ignore[attr-defined]


# --- the filter is part of the URL -------------------------------------------------------------


def test_the_filter_narrows_by_name_and_by_type_and_stays_in_the_url(
    clinics: SimpleNamespace,
) -> None:
    """A real ``GET`` form, so the filtered state is bookmarkable."""
    with clinics.session() as db:
        a, b = db.get(Site, clinics.site_a), db.get(Site, clinics.site_b)
        assert a is not None and b is not None
        a.name, a.sector = "Zola Community Clinic", SiteSector.PUBLIC.value
        b.name, b.sector = "Kenilworth Medicross", SiteSector.PRIVATE.value
        db.commit()

    by_name = _open(clinics, "operator@clinicq.example", f"{_CONSOLE}/all?q=zola")
    by_type = _open(
        clinics, "operator@clinicq.example", f"{_CONSOLE}/all?sector=private"
    )

    assert [row.id for row in by_name.context["listing"].items] == [clinics.site_a]  # type: ignore[attr-defined]
    assert by_name.context["query"] == "zola"  # type: ignore[attr-defined]
    assert [row.id for row in by_type.context["listing"].items] == [clinics.site_b]  # type: ignore[attr-defined]
    assert by_type.context["sector"] == "private"  # type: ignore[attr-defined]


def test_a_sector_that_is_not_one_is_ignored_rather_than_refused(
    clinics: SimpleNamespace,
) -> None:
    """A hand-edited URL shows the unfiltered list; it never 500s or empties the console."""
    page = _open(clinics, "operator@clinicq.example", f"{_CONSOLE}/all?sector=banana")

    assert page.status_code == status.HTTP_200_OK
    assert len(page.context["listing"].items) == 2  # type: ignore[attr-defined]


# --- who may open it, and what it offers -------------------------------------------------------


def test_the_operator_is_offered_every_action(clinics: SimpleNamespace) -> None:
    """``platform_admin`` holds ``sites:delete`` at ``business``, which covers create and update.

    Asserted through the gate the template actually calls, so the buttons and this test cannot
    disagree: ``can('sites', verb, 'business')``.
    """
    page = _open(clinics, "operator@clinicq.example")
    gate = page.context["nav"]  # type: ignore[attr-defined]

    assert page.status_code == status.HTTP_200_OK
    for verb in ("create", "update", "delete"):
        assert gate.can("sites", verb, "business") is True, verb


def test_a_clinic_manager_is_refused_the_console_outright(
    clinics: SimpleNamespace,
) -> None:
    """It is the cross-clinic directory. A manager runs their clinic from its own dashboard."""
    refused = _open(clinics, "manager.a@clinicq.example")

    assert refused.status_code == status.HTTP_403_FORBIDDEN


def test_the_front_desk_and_the_nurse_are_refused_too(
    clinics: SimpleNamespace,
) -> None:
    for who in ("desk.a@clinicq.example", "nurse.a@clinicq.example"):
        assert _open(clinics, who).status_code == status.HTTP_403_FORBIDDEN, who


def test_nobody_signed_in_is_sent_to_sign_in_rather_than_shown_the_directory(
    clinics: SimpleNamespace,
) -> None:
    """The directory is not public: it names every clinic's contact person."""
    from fastapi.testclient import TestClient

    anonymous = TestClient(clinics.app).get(f"{_CONSOLE}/all", follow_redirects=False)

    assert anonymous.status_code == status.HTTP_302_FOUND
    assert anonymous.headers["location"].startswith("/signin?next=")


# --- what it renders with --------------------------------------------------------------------


def test_the_page_carries_the_choices_its_forms_need(clinics: SimpleNamespace) -> None:
    """The sector and province options come from the enums, never from a list typed into a template."""
    page = _open(clinics, "operator@clinicq.example")

    assert page.context["sectors"] == [member.value for member in SiteSector]  # type: ignore[attr-defined]
    assert len(page.context["provinces"]) == 9  # type: ignore[attr-defined]
    assert "Gauteng" in page.context["provinces"]  # type: ignore[attr-defined]


def test_an_archived_clinic_is_not_in_the_console(clinics: SimpleNamespace) -> None:
    """The soft delete takes it out of every listing, this one included."""
    operator = clinics.client("operator@clinicq.example")
    removed = operator.delete(f"/api/v1/sites/{clinics.site_b}")
    assert removed.status_code == status.HTTP_204_NO_CONTENT

    page = _open(clinics, "operator@clinicq.example")

    assert [row.id for row in page.context["listing"].items] == [clinics.site_a]  # type: ignore[attr-defined]
