"""Every new clinic starts number-only, on every code path (Issue 27, non-negotiable 4).

    Every new site is created with ``display_mode = number_only``, and no code path may change
    that default.

A rule like that survives exactly as long as something checks it, and the check has to be of a
shape that catches the mistake *before* it ships — which a test of one creation path is not, since
the failure is always the **new** path somebody adds. So there are two halves here:

* a **source walk** over everything that constructs a ``Site``: naming ``display_mode`` (or
  ``display_show_comment``) at construction time is a finding, wherever it is, unless it is the one
  place allowed to — the enum's own default. That covers the API, onboarding, the seed and anything
  added tomorrow, without anyone remembering this file;
* **behavioural tests** of the paths that exist today, because a guard that only reads source could
  be satisfied by code that sets the field a line later.

The fixtures at the bottom prove the source walk can fail. A guard that cannot fail passes forever.

Reads source, never rendered output (``docs/IDE/RULES/testing-strategy.mdc``).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.commons.enums import (
    SITE_DEFAULT_DISPLAY_MODE,
    DisplayMode,
    SiteStatus,
)
from src.database.models.site import Site
from src.modules.queues.service import create_default_queues
from src.modules.sites.schemas import SiteIn
from src.modules.sites.service import create_site
from tests.factories import SiteFactory

_ROOT = Path(__file__).resolve().parents[3]
_SEARCHED = (_ROOT / "src", _ROOT / "scripts", _ROOT / "tests" / "factories.py")

#: The fields nobody may name when a clinic is constructed. ``display_mode`` is the
#: non-negotiable; ``display_show_comment`` is here for the same reason — a clinic that starts
#: showing the reason for a visit has had that decided for it by a developer.
_FORBIDDEN_AT_CREATION = frozenset({"display_mode", "display_show_comment"})

#: Where the default may be written, and nowhere else: the enum module that defines it, the model
#: that reads the constant, the migration that puts it in the column's server default, and this
#: guard. Each is the *definition* of the default rather than a choice made at a call site.
_MAY_NAME_THE_DEFAULT = (
    "src/commons/enums.py",
    "src/database/models/site.py",
    "alembic/versions/0010_site_display_settings.py",
)


def _site_constructions(tree: ast.Module) -> list[ast.Call]:
    """Every ``Site(...)`` call in a module's source."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Site"
    ]


def findings_in_source(source: str, *, path: str) -> list[str]:
    """Every place ``source`` decides a clinic's display settings as it creates it."""
    findings: list[str] = []
    for call in _site_constructions(ast.parse(source)):
        for keyword in call.keywords:
            if keyword.arg in _FORBIDDEN_AT_CREATION:
                findings.append(
                    f"{path}:{keyword.value.lineno} creates a Site with {keyword.arg}=…; "
                    "every clinic starts number-only (non-negotiable 4). Let the column's "
                    "default stand, and change it afterwards through "
                    "src.modules.sites.settings, which audits it."
                )
    return findings


def _sources() -> list[Path]:
    """Every application, script and factory file a clinic could be created in."""
    paths: list[Path] = []
    for root in _SEARCHED:
        if root.is_file():
            paths.append(root)
            continue
        paths += [
            path
            for path in sorted(root.rglob("*.py"))
            if "__pycache__" not in path.parts
        ]
    return paths


def test_no_code_path_creates_a_site_with_a_display_mode() -> None:
    """The standing rule. A new creation path that picks a display mode fails here."""
    findings: list[str] = []
    for path in _sources():
        relative = path.relative_to(_ROOT).as_posix()
        if relative in _MAY_NAME_THE_DEFAULT:
            continue
        findings += findings_in_source(path.read_text(encoding="utf-8"), path=relative)
    assert not findings, "clinics created with a display mode:\n" + "\n".join(findings)


def test_there_is_one_definition_of_the_default_and_both_layers_read_it() -> None:
    """ "The default" is a constant, not a member repeated in files that can drift apart.

    Comparing *against* ``DisplayMode.NUMBER_ONLY`` is fine and several modules do it — the board
    projection has to ask which mode it is in. What must have one definition is the value a new
    clinic is **created** with, which is why the model's column default and the migration's server
    default both read :data:`~src.commons.enums.SITE_DEFAULT_DISPLAY_MODE`.
    """
    assert SITE_DEFAULT_DISPLAY_MODE is DisplayMode.NUMBER_ONLY

    column = Site.__table__.columns["display_mode"]
    assert column.default is not None
    assert column.default.arg == SITE_DEFAULT_DISPLAY_MODE.value

    migration = (
        _ROOT / "alembic" / "versions" / "0010_site_display_settings.py"
    ).read_text(encoding="utf-8")
    assert "server_default=SITE_DEFAULT_DISPLAY_MODE.value" in migration
    assert '"full"' not in migration and '"name_lite"' not in migration


def test_the_column_is_defaulted_by_the_database_as_well_as_by_python() -> None:
    """A row inserted by a restore or a hand-written INSERT gets the safe value too.

    The Python default covers the ORM paths the source walk above reads; the server default covers
    the ones it cannot see. Both are needed, and this is the half a guard reading ``src/`` would
    otherwise miss entirely.
    """
    column = Site.__table__.columns["display_mode"]
    assert (
        column.server_default is None
    )  # the model declares it; the migration installs it
    migration = (
        _ROOT / "alembic" / "versions" / "0010_site_display_settings.py"
    ).read_text(encoding="utf-8")
    assert "nullable=False" in migration


# --- the paths that exist today, exercised rather than read -------------------------------------


def test_the_api_creation_path_starts_a_clinic_number_only(
    session_factory: sessionmaker[Session],
) -> None:
    """``create_site`` is what ``POST /api/v1/sites`` calls."""
    payload = SiteIn(
        name="Zola Clinic",
        slug="zola-clinic",
        sector="public",  # type: ignore[arg-type]
        location={"latitude": -26.26840, "longitude": 27.84720},  # type: ignore[arg-type]
        address_line="Zola North, Soweto",
        city="Johannesburg",
        province="Gauteng",  # type: ignore[arg-type]
    )
    with session_factory() as db:
        site = create_site(db, payload)
        db.commit()
        db.refresh(site)
        assert site.display_mode_enum is SITE_DEFAULT_DISPLAY_MODE
        assert site.display_show_comment is False


def test_the_factory_path_starts_a_clinic_number_only(
    session_factory: sessionmaker[Session],
) -> None:
    """The factory every other test builds on cannot quietly seed a different default."""
    with session_factory() as db:
        site = SiteFactory.create(db, status=SiteStatus.VERIFIED)
        db.commit()
        db.refresh(site)
        assert site.display_mode_enum is DisplayMode.NUMBER_ONLY


def test_the_onboarding_path_starts_a_clinic_number_only(
    session_factory: sessionmaker[Session],
) -> None:
    """Onboarding creates a clinic and its default queues; neither touches the display mode."""
    with session_factory() as db:
        site = SiteFactory.create(db, status=SiteStatus.PENDING_VERIFICATION)
        db.flush()
        create_default_queues(db, site.id)
        db.commit()
        db.refresh(site)
        assert site.display_mode_enum is DisplayMode.NUMBER_ONLY


def test_the_seed_path_starts_a_clinic_number_only() -> None:
    """The demo seed writes its clinics and chooses nothing about their boards.

    Checked at the level the seed works at — the columns it sets — rather than by running it
    against PostgreSQL, which ``tests/integration/database/test_seed_dev_data.py`` already does.
    """
    from scripts.db.seed_dev_data import seed_sites

    source = Path(
        __import__("scripts.db.seed_dev_data", fromlist=["x"]).__file__ or ""
    ).read_text(encoding="utf-8")
    assert seed_sites.__doc__ and "display_mode" in seed_sites.__doc__
    assert findings_in_source(source, path="scripts/db/seed_dev_data.py") == []


# --- the guard can fail -------------------------------------------------------------------------


_A_TEMPTING_SHORTCUT = """
from src.commons.enums import DisplayMode
from src.database.models import Site

def onboard(db, payload):
    site = Site(
        slug=payload.slug,
        name=payload.name,
        display_mode=DisplayMode.FULL.value,
    )
    db.add(site)
    return site
"""

_THE_SAME_WITHOUT_IT = """
from src.database.models import Site

def onboard(db, payload):
    site = Site(slug=payload.slug, name=payload.name)
    db.add(site)
    return site
"""


def test_the_guard_fails_on_a_creation_path_that_picks_a_display_mode() -> None:
    """The criterion, demonstrated: this is what the mistake looks like, and it is caught."""
    findings = findings_in_source(
        _A_TEMPTING_SHORTCUT, path="src/modules/sites/onboarding.py"
    )
    assert len(findings) == 1
    assert "display_mode" in findings[0]
    assert "non-negotiable 4" in findings[0]


def test_the_guard_passes_the_same_path_once_it_leaves_the_default_alone() -> None:
    """And it is discriminating, not simply always red."""
    assert (
        findings_in_source(_THE_SAME_WITHOUT_IT, path="src/modules/sites/onboarding.py")
        == []
    )


@pytest.mark.parametrize("field", sorted(_FORBIDDEN_AT_CREATION))
def test_both_privacy_fields_are_covered_not_only_the_mode(field: str) -> None:
    """``display_show_comment`` is the other half of what a board may say about a person."""
    source = f"""
from src.database.models import Site
def make(db):
    return Site(slug="x", name="X", {field}=True)
"""
    assert findings_in_source(source, path="src/anywhere.py")
