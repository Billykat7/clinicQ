"""``deploy/env/`` holds configuration and never a credential.

The split this guards: the ~161 settings that are not secret are committed, reviewed and diffable;
the ~21 that are live encrypted (SOPS + age) in the private ``Billykat7/infra`` repository and are
merged in on the host at deploy time. ``deploy/env/README.md`` explains why.

That split is only worth anything if the committed half cannot quietly acquire a secret. gitleaks
catches a value that *looks* like a credential; these tests catch the case it cannot, which is a
real value in a key the classifier calls a secret -- ``DATABASE_URL``, whose password is inside a
URL that looks like any other, is the one that matters.
"""

import re
import subprocess
from pathlib import Path

import pytest

from src.core.config import setting_env_names, setting_is_secret

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_ENV_DIR = REPO_ROOT / "deploy" / "env"

#: A ``KEY=value`` line; a comment or a blank line is neither.
SETTING_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

#: Keys in these files that are not app settings on purpose: infra/docker/docker-compose.prod.yml
#: interpolates them, and scripts/check_config.py lists them as warnings for the same reason.
COMPOSE_KEYS = frozenset({"IMAGE", "APP_PORT", "DOMAIN", "GATEWAY_COMPOSE_DIR"})

ENVIRONMENTS = ("staging", "production")


def _keys(path: Path) -> dict[str, str]:
    """The ``KEY=value`` pairs in one env file, in order."""
    pairs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = SETTING_LINE.match(line)
        if match:
            pairs[match.group(1)] = match.group(2)
    return pairs


@pytest.fixture(params=ENVIRONMENTS)
def environment(request: pytest.FixtureRequest) -> str:
    """Each environment that has a committed file."""
    return str(request.param)


def test_the_file_exists_and_says_which_environment_it_is(environment: str) -> None:
    """A file named for an environment sets that environment, so neither can be read for the other."""
    path = DEPLOY_ENV_DIR / f"{environment}.env"
    assert path.is_file(), f"{path} is missing"
    assert _keys(path).get("ENVIRONMENT") == environment


def test_no_key_in_the_committed_half_is_a_secret(environment: str) -> None:
    """The whole point. A credential here would be published the moment it is committed.

    ``setting_is_secret`` is the one classifier, shared with ``.env.example``'s generator, so this
    cannot drift from what the rest of the repository calls a secret. If a new setting trips this,
    it belongs in the encrypted file in ``Billykat7/infra``, not here -- see the runbook's
    *Adding or rotating a secret*.
    """
    offenders = [
        key
        for key in _keys(DEPLOY_ENV_DIR / f"{environment}.env")
        if setting_is_secret(key)
    ]
    assert not offenders, (
        f"deploy/env/{environment}.env is committed to a public repository and names "
        f"credential-bearing settings: {', '.join(sorted(offenders))}"
    )


def test_every_key_is_a_setting_the_app_reads_or_a_compose_key(
    environment: str,
) -> None:
    """A typo here is silent: the app would use the default and nobody would see why.

    ``check_config.py`` reports the same thing as a warning at deploy time. This is the same check
    a commit earlier, where it is cheap.
    """
    known = {
        name for names in setting_env_names().values() for name in names
    } | COMPOSE_KEYS
    unknown = [
        key for key in _keys(DEPLOY_ENV_DIR / f"{environment}.env") if key not in known
    ]
    assert not unknown, (
        f"deploy/env/{environment}.env sets keys the app does not read: {', '.join(sorted(unknown))}"
    )


def test_the_two_environments_do_not_collide_on_one_host(environment: str) -> None:
    """Staging and production share the platform host, so their ports and host names must differ.

    deploy.sh smoke-tests a candidate on ``APP_PORT + 1000``, so the ports must differ by more than
    nothing and must not land on each other's candidate port either.
    """
    ports = {
        name: int(_keys(DEPLOY_ENV_DIR / f"{name}.env")["APP_PORT"])
        for name in ENVIRONMENTS
    }
    domains = {
        name: _keys(DEPLOY_ENV_DIR / f"{name}.env")["DOMAIN"] for name in ENVIRONMENTS
    }
    assert len(set(ports.values())) == len(ports), f"the same APP_PORT twice: {ports}"
    assert len(set(domains.values())) == len(domains), (
        f"the same DOMAIN twice: {domains}"
    )
    candidates = {port + 1000 for port in ports.values()}
    assert not candidates & set(ports.values()), (
        f"one environment's candidate port is the other's live port: {ports}"
    )


def test_the_committed_files_are_the_only_env_files_ever_tracked() -> None:
    """``deploy/env/*.env`` is the one exception to "no env file is in git" (ENVIRONMENTS.md).

    Every other ``.env*`` is ignored, and a new tracked one is the shape a leak takes.
    """
    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "*.env", ".env*"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    allowed = {".env.example", "scripts/cd/setup.env.example"} | {
        f"deploy/env/{name}.env" for name in ENVIRONMENTS
    }
    unexpected = sorted(set(filter(None, tracked)) - allowed)
    assert not unexpected, f"env files are tracked that should not be: {unexpected}"
