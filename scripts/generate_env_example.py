"""Write ``.env.example`` from the ``Settings`` class itself (Issue 12).

Every setting the app reads appears once, in the order ``src/core/config.py`` declares it, with its
field description as the comment and its default:

* as a **commented** ``# NAME=default`` line, so a copied ``.env`` leaves the code's default in
  charge (and follows it when it changes) until someone uncomments the line on purpose;
* ``# NAME=`` for a setting whose default is "none", which an empty value would not mean;
* active, with a local-development value, only for :data:`LOCAL_VALUES`: the connection URLs that
  must point at the compose stack for ``cp .env.example .env && make run`` to work unedited.

Secrets are blank or development-only; the header says where real values live.

Usage::

    python scripts/generate_env_example.py           # rewrite .env.example
    python scripts/generate_env_example.py --check   # exit 1, with a diff, if it is out of date

``make env-example`` runs the first; ``tests/unit/platform/test_env_example.py`` runs the check.
"""

import argparse
import difflib
import re
import sys
import textwrap
from enum import Enum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic.fields import FieldInfo  # noqa: E402 - after the sys.path bootstrap

from src.core.config import Settings, setting_env_names  # noqa: E402

ENV_EXAMPLE = REPO_ROOT / ".env.example"

#: The compose stack's connection details (infra/docker/docker-compose.db.yml defaults), which a
#: fresh clone needs in place of the class defaults. test_dev_stack_compose.py holds them equal.
LOCAL_VALUES: dict[str, str] = {
    "DATABASE_URL": "postgresql://btk_user:change-me@localhost:5432/btk",
    "REDIS_URL": "redis://localhost:6379/0",
}

#: A setting line, active or commented: what the drift test and this script count as documented.
SETTING_LINE = re.compile(r"^(?:# )?([A-Z][A-Z0-9_]*)=")

#: Words in an env var name that mark it as a credential.
SECRET_MARKERS = ("SECRET", "PASSWORD", "TOKEN", "_KEY", "KEYS")

#: Credential-looking names that are public by design (handed to the browser).
PUBLIC_KEY_SUFFIXES = ("_PUBLISHABLE_KEY", "_PUBLIC_KEY")

#: Comment width: the file is read in a terminal and in review diffs.
WIDTH = 100

HEADER = """\
# BK ClinicQ settings: every one the app reads, generated from src/core/config.py (Issue 12).
#
#   cp .env.example .env    then `make run`: a local development app against the compose stack
#                           (`make db-up`), with no other edit.
#
# Generated: change the Field in src/core/config.py, then run `make env-example`.
# tests/unit/platform/test_env_example.py fails while this file and the Settings class disagree.
#
# Each setting is listed with its default on a commented `# NAME=value` line, so a copied .env
# keeps following the code's defaults; uncomment a line to override one. `# NAME=` has no default.
# Only the connection URLs are active, pointing at the compose stack. Secrets here are blank or
# development-only, and the app refuses the development ones outside development. Real values live
# in the host's .env and in GitHub Environments, never in this repository
# (docs/CICD/ENVIRONMENTS.md). Check an env file without starting the app:
#   python scripts/check_config.py .env --environment production"""

#: ``(env: NAME)`` and ``(env: NAME; ALIAS accepted as alias)`` notes, redundant beside the name.
_ENV_NOTE = re.compile(r"\s*\(env: [^)]*\)")


def is_secret(env_name: str) -> bool:
    """Whether a setting holds a credential, by its name (``JWT_SECRET``, ``SMTP_PASSWORD``…)."""
    return any(
        marker in env_name for marker in SECRET_MARKERS
    ) and not env_name.endswith(PUBLIC_KEY_SUFFIXES)


def env_value(value: object) -> str:
    """Spell a default the way an env file does; "none" is an empty value."""
    match value:
        case None:
            return ""
        case bool():
            return "true" if value else "false"
        case Enum():
            return str(value.value)
        case list() | tuple():
            return ",".join(str(item) for item in value)
        case _:
            return str(value)


def describe(field: FieldInfo, names: tuple[str, ...]) -> list[str]:
    """The comment lines for one setting: its description, and any other name it answers to."""
    text = _ENV_NOTE.sub("", field.description or "").strip()
    if not text.endswith((".", "?", "!")):
        text += "."
    if len(names) > 1:
        text += f" Also read as {', '.join(names[1:])}."
    if is_secret(names[0]):
        text += " Secret: never commit a real value."
    lines = [f"# {line}" for line in textwrap.wrap(text, width=WIDTH - 2)]
    # A description line shaped like `# NAME=` would be counted as a setting of its own.
    assert not any(SETTING_LINE.match(line) for line in lines), names[0]
    return lines


def render() -> str:
    """Return the whole ``.env.example`` text for the current ``Settings`` class."""
    names = setting_env_names()
    blocks = [HEADER]
    for field_name, field in Settings.model_fields.items():
        env_name = names[field_name][0]
        if env_name in LOCAL_VALUES:
            setting = f"{env_name}={LOCAL_VALUES[env_name]}"
        else:
            default = env_value(field.get_default(call_default_factory=True))
            setting = f"# {env_name}={default}"
        blocks.append("\n".join([*describe(field, names[field_name]), setting]))
    return "\n\n".join(blocks) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Rewrite ``.env.example``, or with ``--check`` report whether it is up to date."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 with a diff if .env.example is out of date",
    )
    args = parser.parse_args(argv)
    expected = render()
    if not args.check:
        ENV_EXAMPLE.write_text(expected, encoding="utf-8")
        print(
            f"wrote {ENV_EXAMPLE.relative_to(REPO_ROOT)}: "
            f"{len(Settings.model_fields)} settings"
        )
        return 0
    actual = ENV_EXAMPLE.read_text(encoding="utf-8") if ENV_EXAMPLE.exists() else ""
    if actual == expected:
        print(".env.example is up to date")
        return 0
    sys.stdout.writelines(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            ".env.example (committed)",
            ".env.example (from Settings)",
        )
    )
    print("\n.env.example is out of date: run `make env-example`", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
