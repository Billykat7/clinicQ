"""Check an env file against the app's settings without starting the app (Issue 12).

Reports **every** problem in one pass, not the first: a deploy learns everything wrong with its
configuration from one run, instead of fixing a value, redeploying and meeting the next one.

* values the app cannot read at all (``BCRYPT_ROUNDS=99``, ``OTP_LENGTH=six``), all of them;
* values it can read but must not run with in that environment: the same list the app's boot
  guard raises (``Settings.configuration_problems``), so this script cannot disagree with it;
* keys that are not a setting (a typo, or a compose/deploy key such as ``IMAGE``), as warnings.

Only the file is read; the process environment is ignored, so the result describes the file.
Values are never printed, only setting names: the file is expected to hold secrets.

Usage::

    python scripts/check_config.py                      # ./.env
    python scripts/check_config.py /opt/btk/clinicq/.env
    python scripts/check_config.py .env.staging --environment staging

Exit status: 0 no problems (warnings allowed), 1 at least one problem, 2 the file does not exist.
"""

import argparse
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import dotenv_values  # noqa: E402 - after the sys.path bootstrap
from pydantic import ValidationError  # noqa: E402
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource  # noqa: E402

from src.commons.enums import AppEnvironment  # noqa: E402
from src.core.config import Settings, setting_env_names  # noqa: E402

#: How many times the file is re-read with its unreadable values dropped. Each pass removes every
#: field error pydantic reported, so one retry is normally enough; the cap only guards a loop.
MAX_PASSES = 3


class ExitCode(StrEnum):
    """What the process tells its caller (as the value of ``int(code)``)."""

    OK = "0"
    PROBLEMS = "1"
    NO_FILE = "2"


class _FileOnlySettings(Settings):
    """``Settings`` fed only by the keyword arguments given: no process environment, no .env.

    Its boot guard collects rather than raises, so the problems can be listed together.
    """

    raise_on_configuration_problems: ClassVar[bool] = False

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Read the init arguments only."""
        return (init_settings,)


@dataclass
class Report:
    """What checking one file found."""

    path: Path
    environment: AppEnvironment
    problems: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the app would start with this file."""
        return not self.problems


def _accepted_names() -> tuple[dict[str, str], dict[str, str]]:
    """Map every env var name the settings accept to its keyword argument and its canonical name.

    A plain field takes its field name as the keyword; an aliased one takes the alias itself.
    """
    keyword: dict[str, str] = {}
    canonical: dict[str, str] = {}
    for field_name, names in setting_env_names().items():
        aliased = Settings.model_fields[field_name].validation_alias is not None
        for name in names:
            keyword[name] = name if aliased else field_name
            canonical[name] = names[0]
    return keyword, canonical


def check(path: Path, environment: AppEnvironment | None = None) -> Report:
    """Check one env file; ``environment`` checks it as if it named that environment."""
    keyword, canonical = _accepted_names()
    field_env_name = {
        field_name: names[0] for field_name, names in setting_env_names().items()
    }
    raw = {key.strip(): value for key, value in dotenv_values(path).items()}

    values: dict[str, Any] = {}
    unknown: list[str] = []
    for key, value in raw.items():
        name = key.upper()
        if name in keyword:
            values[keyword[name]] = "" if value is None else value
        else:
            unknown.append(key)
    if environment is not None:
        values["environment"] = environment.value

    report = Report(path=path, environment=AppEnvironment.DEVELOPMENT)
    if unknown:
        report.warnings.append(
            (
                f"{len(unknown)} key(s)",
                "not settings the app reads (typos, or compose and deploy keys?): "
                + ", ".join(sorted(unknown)),
            )
        )

    settings: Settings | None = None
    for _ in range(MAX_PASSES):
        try:
            settings = _FileOnlySettings(**values)
            break
        except ValidationError as exc:
            dropped = False
            for error in exc.errors(include_input=False, include_url=False):
                loc = str(error["loc"][0]) if error["loc"] else ""
                name = field_env_name.get(
                    loc, canonical.get(loc.upper(), loc or "(settings)")
                )
                report.problems.append((name, error["msg"]))
                # Fall back to the default for the unreadable value, so the rest still gets checked.
                if loc and values.pop(loc, None) is not None:
                    dropped = True
            if not dropped:
                break

    if settings is None:
        return report
    report.environment = settings.environment
    report.problems += [
        (problem.setting, problem.message)
        for problem in settings.configuration_problems()
    ]
    if environment is None and "ENVIRONMENT" not in {k.upper() for k in raw}:
        report.warnings.append(
            (
                "ENVIRONMENT",
                "not set, so the file is checked as development, where the staging and "
                "production rules do not apply (pass --environment to check it as one)",
            )
        )
    if settings.is_development:
        # Outside development these are problems (above); here they are only surprising.
        report.warnings += [
            (
                part,
                f"ignored: DATABASE_URL is set and names a different {part[3:].lower()}",
            )
            for part in settings._ignored_database_parts
        ]
    return report


def render(report: Report) -> str:
    """The human-readable report: one line per finding, then a verdict."""
    lines = [f"check_config: {report.path} (as {report.environment.value})"]
    lines += [f"  ✗ {name}: {message}" for name, message in report.problems]
    lines += [f"  ! {name}: {message}" for name, message in report.warnings]
    problems, warnings = len(report.problems), len(report.warnings)
    if report.ok:
        lines.append(f"OK: the app would start with this file ({warnings} warning(s)).")
    else:
        lines.append(
            f"{problems} problem(s), {warnings} warning(s): the app would refuse to start "
            "with this file."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Check the file named on the command line and print the report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "env_file", nargs="?", default=".env", type=Path, help="default: ./.env"
    )
    parser.add_argument(
        "--environment",
        choices=[environment.value for environment in AppEnvironment],
        help="check the file as this environment, whatever ENVIRONMENT it names",
    )
    args = parser.parse_args(argv)
    if not args.env_file.is_file():
        print(f"check_config: {args.env_file} does not exist", file=sys.stderr)
        return int(ExitCode.NO_FILE)
    environment = AppEnvironment(args.environment) if args.environment else None
    report = check(args.env_file, environment)
    print(render(report))
    return int(ExitCode.OK if report.ok else ExitCode.PROBLEMS)


if __name__ == "__main__":
    sys.exit(main())
