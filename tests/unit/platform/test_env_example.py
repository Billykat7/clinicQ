"""``.env.example`` and the ``Settings`` class say the same thing (Issue 12).

The example file is generated from the class (``make env-example``), and these tests are what keep
it that way: a setting added to ``src/core/config.py`` and not to the file, or left in the file
after it is gone from the class, fails the build with its name. The file must also stay usable as
it is (``cp .env.example .env`` boots a development app) and stay the only env file in git.

Offline: files are read and settings built in-process; nothing is started.
"""

import subprocess
from pathlib import Path

import pytest

from scripts.check_config import check
from scripts.generate_env_example import ENV_EXAMPLE, SETTING_LINE, render
from src.commons.enums import AppEnvironment
from src.core.config import setting_env_names

REPO_ROOT = Path(__file__).resolve().parents[3]

#: The env files git may track: templates, never a filled-in file.
TRACKED_ENV_FILES = {".env.example", "scripts/cd/setup.env.example"}


def _documented() -> list[str]:
    """Every setting name ``.env.example`` lists, active or commented, in file order."""
    return [
        match.group(1)
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if (match := SETTING_LINE.match(line))
    ]


def test_every_setting_is_in_env_example_and_nothing_else_is() -> None:
    """The acceptance criterion: one set of names, whichever side changed."""
    documented = _documented()
    settings = {names[0] for names in setting_env_names().values()}
    missing = sorted(settings - set(documented))
    extra = sorted(set(documented) - settings)
    assert not missing, (
        f"settings missing from .env.example (run make env-example): {missing}"
    )
    assert not extra, f".env.example lists names Settings does not read: {extra}"
    assert len(documented) == len(set(documented)), "a setting is listed twice"


def test_env_example_is_exactly_what_the_generator_writes() -> None:
    """Descriptions and defaults come from the class too, so an edit there must reach the file."""
    assert ENV_EXAMPLE.read_text(encoding="utf-8") == render(), (
        ".env.example is out of date: run `make env-example`"
    )


def test_a_copy_of_env_example_boots_a_development_app() -> None:
    """``cp .env.example .env && make run``: no problems, as development, with no other edit."""
    report = check(ENV_EXAMPLE)
    assert report.ok, report.problems
    assert report.environment is AppEnvironment.DEVELOPMENT


def test_env_example_carries_no_secret_a_deployment_could_use() -> None:
    """Checked as production, the file is refused: its only secret is the development JWT one."""
    report = check(ENV_EXAMPLE, AppEnvironment.PRODUCTION)
    assert [name for name, _ in report.problems] == ["JWT_SECRET"]


def test_no_filled_in_env_file_is_tracked() -> None:
    """Only templates are in git; a real .env committed once stays in history for good."""
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "*.env", "*.env.*", ".env*", "**/.env*"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except OSError, subprocess.CalledProcessError:
        pytest.skip("not a git checkout")
    assert set(tracked) <= TRACKED_ENV_FILES, sorted(set(tracked) - TRACKED_ENV_FILES)
