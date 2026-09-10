"""The local Docker stack says one thing in every place it is written (Issue 2, M1).

Decision 4 settled the PostgreSQL version at 18, and the dev stack's connection details are now
written in three places: the compose files, `.env.example` and, from Issue 9, the CI workflow's
service containers. Each is one line a later PR can change without anything failing until a
teammate's database silently disagrees with CI. These tests are that missing failure:

* exactly one PostgreSQL image is in play across the compose files and CI workflows, and it is 18;
* the database lives in a named volume at the path PostgreSQL 18 images keep their data under;
* db and redis carry health checks, and the API container waits for both to pass;
* db and redis are published on loopback only;
* `.env.example` points at the compose services, and the API container uses the same URLs with
  only the host swapped for the service name.

Offline and dependency-free: files are parsed, never run.
"""

import re
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKER_DIR = REPO_ROOT / "infra" / "docker"
DEV_STACK = DOCKER_DIR / "docker-compose.yml"
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

#: Decision 4 in docs/GITHUB/ISSUES/README.md. Changing it is a team decision, not a line edit.
POSTGRES_MAJOR = "18"

#: Where PostgreSQL 18+ images declare their volume (PGDATA is /var/lib/postgresql/18/docker).
POSTGRES_18_VOLUME_PATH = "/var/lib/postgresql"

#: The host every published backing-service port must be bound to.
LOOPBACK = "127.0.0.1"

#: The port PostgreSQL listens on inside its container.
POSTGRES_CONTAINER_PORT = 5432

#: A Compose `${NAME:-default}` / `${NAME-default}` / `${NAME}` reference.
_INTERPOLATION = re.compile(r"\$\{(\w+)(?::?-([^}]*))?\}")

#: An image that runs PostgreSQL: `postgres:*`, `postgis/postgis:*` or a custom `*postgres*` build.
_POSTGRES_IMAGE = re.compile(r"postgres|postgis")


class Service(StrEnum):
    """The compose services these tests reason about."""

    DB = "db"
    REDIS = "redis"
    APP = "app"


class EnvKey(StrEnum):
    """The `.env.example` and container settings these tests reason about."""

    DATABASE_URL = "DATABASE_URL"
    REDIS_URL = "REDIS_URL"
    POSTGRES_USER = "POSTGRES_USER"
    POSTGRES_PASSWORD = "POSTGRES_PASSWORD"
    POSTGRES_DB = "POSTGRES_DB"


class HealthCondition(StrEnum):
    """`depends_on` conditions."""

    SERVICE_HEALTHY = "service_healthy"


def _load(path: Path) -> dict[str, Any]:
    """Parse one YAML file into a plain dict."""
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _resolve(value: str) -> str:
    """Resolve Compose interpolation to the defaults a shell with nothing exported would get."""
    return _INTERPOLATION.sub(lambda m: m.group(2) or "", value).replace("$$", "$")


def _stack() -> dict[str, dict[str, Any]]:
    """Return every service of the dev stack, following `include:` into the included files."""
    compose = _load(DEV_STACK)
    services: dict[str, dict[str, Any]] = {}
    for entry in compose.get("include", []):
        included = entry if isinstance(entry, str) else entry["path"]
        services |= _load(DOCKER_DIR / included).get("services", {})
    return services | compose.get("services", {})


def _environment(service: dict[str, Any]) -> dict[str, str]:
    """Return a service's `environment:` as a resolved dict, from either the map or list form."""
    env = service.get("environment", {})
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    return {key: _resolve(str(value)) for key, value in env.items()}


def _published(service: dict[str, Any]) -> list[tuple[str, int, int]]:
    """Return a service's short-syntax ports as resolved (host_ip, host_port, container_port).

    ``host_ip`` is empty when the mapping names no address, which Docker reads as every interface.
    """
    published = []
    for port in service.get("ports", []):
        *address, host_port, container_port = _resolve(str(port)).split(":")
        published.append(("".join(address), int(host_port), int(container_port)))
    return published


def _env_example() -> dict[str, str]:
    """Parse `.env.example` into a dict, skipping comments and blank lines."""
    entries = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            entries[key.strip()] = value.strip()
    return entries


def _postgres_images() -> dict[str, str]:
    """Return every PostgreSQL image in the compose files and CI workflows, keyed by where it is."""
    found: dict[str, str] = {}
    for path in sorted(DOCKER_DIR.glob("docker-compose*.yml")):
        for name, service in (_load(path).get("services") or {}).items():
            found[f"{path.name}:{name}"] = service.get("image", "")
    for path in sorted(WORKFLOW_DIR.glob("*.y*ml")):
        for job_name, job in (_load(path).get("jobs") or {}).items():
            for name, container in (job.get("services") or {}).items():
                image = (
                    container
                    if isinstance(container, str)
                    else container.get("image", "")
                )
                found[f"{path.name}:{job_name}.{name}"] = image
    return {
        where: image for where, image in found.items() if _POSTGRES_IMAGE.search(image)
    }


def test_one_postgresql_image_is_in_play_and_it_is_decision_4s_version() -> None:
    """Two PostgreSQL versions means a query that passes locally can fail in CI, or the reverse."""
    images = _postgres_images()
    assert images, "no PostgreSQL image found in infra/docker/docker-compose*.yml"
    assert len(set(images.values())) == 1, (
        f"more than one PostgreSQL image in play: {images}"
    )
    tag = next(iter(images.values())).rsplit(":", 1)[1]
    major = tag.split("-")[0].split(".")[0]
    assert major == POSTGRES_MAJOR, (
        f"PostgreSQL {major} is in play but decision 4 chose {POSTGRES_MAJOR}: {images}"
    )


def test_the_database_volume_is_named_and_at_the_postgres_18_path() -> None:
    """The PostgreSQL 18 entrypoint refuses to start with a mount at the pre-18 path."""
    db = _stack()[Service.DB]
    mounts = [volume.split(":")[:2] for volume in db.get("volumes", [])]
    named = [source for source, target in mounts if target == POSTGRES_18_VOLUME_PATH]
    assert named, f"db must mount a volume at {POSTGRES_18_VOLUME_PATH}, found {mounts}"
    assert not named[0].startswith((".", "/")), (
        "the db volume must be named, not a bind mount"
    )


def test_the_api_waits_for_a_healthy_database_and_redis() -> None:
    """Without health checks the API starts against a database that is still initialising."""
    stack = _stack()
    for service in (Service.DB, Service.REDIS):
        assert stack[service].get("healthcheck", {}).get("test"), (
            f"{service} has no health check"
        )
        condition = stack[Service.APP]["depends_on"][service]["condition"]
        assert condition == HealthCondition.SERVICE_HEALTHY, (
            f"app must wait for {service} to be healthy, not {condition!r}"
        )


def test_backing_services_are_published_on_loopback_only() -> None:
    """The database password is a development default and Redis has none."""
    stack = _stack()
    for service in (Service.DB, Service.REDIS):
        for host_ip, host_port, _ in _published(stack[service]):
            assert host_ip == LOOPBACK, (
                f"{service} publishes {host_port} on {host_ip or 'every interface'}"
            )


def test_env_example_points_at_the_compose_services() -> None:
    """`cp .env.example .env` must reach the stack `make db-up` starts, with no other edits."""
    stack = _stack()
    db_env = _environment(stack[Service.DB])
    ((_, db_port, _),) = _published(stack[Service.DB])
    ((_, redis_port, _),) = _published(stack[Service.REDIS])
    example = _env_example()

    database = urlsplit(example[EnvKey.DATABASE_URL])
    assert (database.username, database.password, database.path) == (
        db_env[EnvKey.POSTGRES_USER],
        db_env[EnvKey.POSTGRES_PASSWORD],
        f"/{db_env[EnvKey.POSTGRES_DB]}",
    ), "DATABASE_URL credentials or database name differ from the db service's defaults"
    assert (database.hostname, database.port) == ("localhost", db_port)

    redis = urlsplit(example[EnvKey.REDIS_URL])
    assert (redis.hostname, redis.port) == ("localhost", redis_port)


def test_the_api_container_uses_the_same_urls_with_the_service_name_as_host() -> None:
    """One URL shape for a host-run and a containerised API; only the host differs."""
    stack = _stack()
    example = _env_example()
    container = _environment(stack[Service.APP])

    def swap_host(url: SplitResult, host: str, port: int) -> str:
        """Return ``url`` with its host and port replaced."""
        userinfo = url.netloc.rpartition("@")[0]
        netloc = f"{userinfo}@{host}:{port}" if userinfo else f"{host}:{port}"
        return url._replace(netloc=netloc).geturl()

    database = urlsplit(example[EnvKey.DATABASE_URL])
    assert container[EnvKey.DATABASE_URL] == swap_host(
        database, Service.DB, POSTGRES_CONTAINER_PORT
    )
    ((_, _, redis_container_port),) = _published(stack[Service.REDIS])
    redis = urlsplit(example[EnvKey.REDIS_URL])
    assert container[EnvKey.REDIS_URL] == swap_host(
        redis, Service.REDIS, redis_container_port
    )
