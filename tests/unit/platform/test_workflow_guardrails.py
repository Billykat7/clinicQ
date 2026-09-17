"""The pipeline guardrails, asserted against the workflow files themselves.

Every rule here protects the monthly GitHub Actions allowance, the merge gate or a deploy in
flight, and every one of them is a single YAML line that a later PR can drop without anything
failing until the bill (or the outage) arrives. These tests are that missing failure.

Offline and dependency-free: the workflows are parsed, never run.

Note on `on:` — PyYAML reads YAML 1.1, where a bare `on` key is the boolean `True`. Use
`_triggers()` rather than `workflow["on"]`, which silently misses.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.
#
# ── ClinicQ's pipeline (Issue 9) ─────────────────────────────────────────────────────────
# The source project ran CI on tags only, with quality left entirely to the local gate. ClinicQ's
# M2 chose the other shape (docs/GITHUB/README.md, "Pipeline strategy"): `ci-local.sh` before the
# push, **CI on every pull request to main as the merge gate**, and deployment on tags only. Issue 9
# therefore rewrote exactly the assertions that encoded "CI never runs on a pull request" and "CI
# publishes the image"; every other guard below is the source project's, unchanged, and the new
# ones pin what the PR-time gate must keep true. The workflow list is ClinicQ's own.

import ast
import re
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
CI_LOCAL = REPO_ROOT / "scripts" / "ci-local.sh"
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
DEV_STACK_SERVICES = REPO_ROOT / "infra" / "docker" / "docker-compose.db.yml"
TESTS_DIR = REPO_ROOT / "tests"

#: The one script that migrates and seeds a database before a new image serves traffic.
DEPLOY_SEQUENCE_SCRIPT = "scripts/db/deploy-sequence.sh"


# GitHub's default job timeout. A job wedged at this ceiling burns 360 of the 2,000 free
# private-repo minutes in one run, which is the whole reason `timeout-minutes` is mandatory.
GITHUB_DEFAULT_TIMEOUT_MINUTES = 360

#: Issue 9's own ceiling for a CI job: a typical run takes under five minutes end to end, so a job
#: still going at fifteen is stuck, and stopping it there caps a wedged run's cost.
CI_JOB_TIMEOUT_CEILING_MINUTES = 15

#: The branch every pull request merges into, and the only one CI gates.
MAIN_BRANCH = "main"

#: The only ref that publishes an image (Issue 10).
VERSION_TAG_PATTERN = "v*.*.*"

#: What marks each image check in release.yml, which must all run before :latest moves.
RELEASE_IMAGE_CHECKS = {
    "size ceiling": "IMAGE_SIZE_CEILING_MB",
    "non-root user": "id -u",
    "version and commit at /health": ".git_sha == $sha",
}


class Workflow(StrEnum):
    """The workflow files, by filename."""

    CI = "ci.yml"
    RELEASE = "release.yml"
    DEPLOY = "deploy.yml"
    VULNERABILITY_SCAN = "vulnerability-scan.yml"


#: Workflow files a later issue creates. An entry comes out in the PR that adds the file, and
#: `test_every_workflow_file_is_covered` fails while a listed file exists, so none can linger.
NOT_YET_CREATED: dict[Workflow, str] = {
    Workflow.VULNERABILITY_SCAN: "Issue 97: dependency and secret scanning",
}


class Trigger(StrEnum):
    """The `on:` keys these tests reason about."""

    PUSH = "push"
    PULL_REQUEST = "pull_request"
    SCHEDULE = "schedule"
    WORKFLOW_DISPATCH = "workflow_dispatch"


class CiJob(StrEnum):
    """The jobs of `ci.yml` these tests reason about."""

    CHANGES = "changes"
    CONVENTIONS = "conventions"
    QUALITY = "quality"
    TEST = "test"
    REPORT = "report"
    DOCKER = "docker"
    GATE = "gate"


#: The one workflow with an unfiltered `pull_request` trigger: the merge gate. It skips its own
#: heavy jobs for a prose-only change (the `changes` job) instead of filtering by path, because a
#: required check that never starts leaves the pull request blocked for good.
MERGE_GATE = Workflow.CI

#: Workflows given a **path-filtered** `pull_request` trigger, and why each earns its minutes.
#: Anything not listed here and not the merge gate may not carry one at all.
PR_TRIGGERED: dict[Workflow, str] = {
    Workflow.VULNERABILITY_SCAN: "scans the inputs of a dependency or image vulnerability",
}

#: Every stage of `ci-local.sh`, and the CI job that runs it; `None` marks a local-only stage.
STAGE_JOBS: dict[str, CiJob | None] = {
    "quality": CiJob.QUALITY,
    "pip-audit": None,  # local-only by design (scripts/README.md); scheduled in CI by Issue 97
    "secrets": CiJob.QUALITY,
    "tests": CiJob.TEST,
    "coverage": CiJob.REPORT,
    "docker": CiJob.DOCKER,
    "compose": None,  # the optional --compose smoke test needs a Docker host of its own
}

#: The commands each mirrored stage runs, which must appear in both `ci-local.sh` and its CI job.
STAGE_COMMANDS: dict[str, tuple[str, ...]] = {
    "quality": (
        "ruff check .",
        "ruff format --check .",
        "mypy src/",
        "scripts/check-ruff-pin.sh",
    ),
    "secrets": (
        "gitleaks detect --source . --config .gitleaks.toml "
        "--baseline-path .gitleaks-baseline.json --redact --no-banner",
    ),
    # The distribution flags are per-shard in CI (`matrix.workers` / `matrix.dist`, defaulting to
    # ci-local.sh's own `-n auto --dist loadscope`); the test below checks those defaults.
    "tests": ("pytest", "--cov"),
    "coverage": ("coverage report",),
    "docker": ("infra/docker/Dockerfile",),
}

#: The scans `ci-local.sh` runs that CI must not, until Issue 97 schedules them deliberately.
LOCAL_ONLY_TOOLS = ("pip-audit", "trivy")

#: A step reference pinned to a full commit SHA: `owner/repo@<40 hex>`.
_PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def _load(workflow: Workflow) -> dict[str, Any]:
    """Parse one workflow file into a plain dict."""
    return yaml.safe_load((WORKFLOW_DIR / workflow).read_text(encoding="utf-8"))


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    """Return the `on:` mapping, tolerating YAML 1.1 folding `on` into `True`."""
    return workflow.get(True, workflow.get("on", {}))


def _jobs(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the `jobs:` mapping."""
    return workflow.get("jobs", {})


def _job_text(job: dict[str, Any]) -> str:
    """Every `run:` script and `with:` input of one job, as one searchable string."""
    parts: list[str] = []
    for step in job.get("steps", []):
        parts.append(str(step.get("run", "")))
        parts.extend(str(value) for value in step.get("with", {}).values())
    return "\n".join(parts)


# Fully commented-out workflow files (kept on disk for reference) are not active GitHub
# Actions definitions, so they are skipped in the structural guardrail checks. There are
# none right now, but the set is kept so a future disabled reference workflow is covered.
DISABLED_WORKFLOWS: frozenset[Workflow] = frozenset()


@pytest.fixture(scope="module")
def workflows() -> dict[Workflow, dict[str, Any]]:
    """Every active workflow file, parsed once."""
    return {
        name: _load(name)
        for name in Workflow
        if name not in DISABLED_WORKFLOWS and name not in NOT_YET_CREATED
    }


def test_every_workflow_file_is_covered() -> None:
    """A workflow added without a `Workflow` member would skip every check below."""
    on_disk = {path.name for path in WORKFLOW_DIR.glob("*.yml")}
    assert on_disk == {str(name) for name in Workflow if name not in NOT_YET_CREATED}


def test_disabled_workflows_are_fully_commented_out() -> None:
    """Kept-on-disk reference workflows must not register with GitHub Actions."""
    for name in DISABLED_WORKFLOWS:
        raw = (WORKFLOW_DIR / str(name)).read_text(encoding="utf-8")
        active = [
            line
            for line in raw.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert not active, f"{name} still has uncommented YAML"


def test_every_workflow_declares_a_concurrency_group(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """Without a group, superseded runs pile up instead of cancelling or queueing."""
    for name, workflow in workflows.items():
        assert workflow.get("concurrency", {}).get("group"), name


def test_nothing_runs_on_an_ordinary_push_and_only_the_gate_runs_on_every_pull_request(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """Minutes go to the merge gate and to releases, never to a branch push.

    **Rewritten by Issue 9.** The source project asserted that nothing at all ran on a pull
    request; ClinicQ's merge gate does, so the rule became: the gate (`ci.yml`) is the one workflow
    with an unfiltered `pull_request` trigger, any other pull-request workflow is path-filtered,
    and no workflow runs on a branch push, so there is no branch-versus-PR double run. The
    source project's one allowed branch push (`sync-issues.yml`) does not exist here, so that
    exception is gone.
    """
    for name, workflow in workflows.items():
        triggers = _triggers(workflow)
        pull_request = triggers.get(Trigger.PULL_REQUEST)
        if name is MERGE_GATE:
            assert pull_request is not None, (
                f"{name}: the merge gate needs pull_request"
            )
        elif name in PR_TRIGGERED:
            assert pull_request is not None, f"{name}: expected a pull_request trigger"
            assert pull_request.get("paths"), (
                f"{name}: a pull_request trigger must be path-filtered — an unfiltered one runs on "
                f"every PR, the cost the guardrails exist to avoid ({PR_TRIGGERED[name]})"
            )
            assert "branches" not in pull_request, (
                f"{name}: filter by path, not by branch"
            )
        else:
            assert pull_request is None, (
                f"{name}: no pull_request trigger — add it to PR_TRIGGERED with a reason, or leave "
                "the workflow tag-triggered"
            )

        push = triggers.get(Trigger.PUSH)
        if push is None:
            continue
        # Everything else may only be triggered by a semver tag.
        assert "branches" not in push, name
        assert push["tags"], name


def test_ci_runs_on_pull_requests_to_main_and_on_nothing_else_automatic(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """The gate runs where merges happen, and nowhere that would pay for the same commit twice.

    **Rewritten by Issue 9** from the source project's "CI never runs on a pull request". The
    trigger is not path-filtered: a docs-only change still reports the required check (its heavy
    jobs are skipped inside the run), and a push to `main` after the merge runs nothing.
    """
    triggers = _triggers(workflows[Workflow.CI])
    pull_request = triggers[Trigger.PULL_REQUEST]
    assert pull_request["branches"] == [MAIN_BRANCH]
    assert "paths" not in pull_request and "paths-ignore" not in pull_request, (
        "a path filter leaves the required check pending on a skipped pull request"
    )
    assert set(triggers) <= {Trigger.PULL_REQUEST, Trigger.WORKFLOW_DISPATCH}, (
        f"ci.yml gained an automatic trigger: {sorted(map(str, triggers))}"
    )


def test_the_scheduled_scan_runs_weekly_not_more_often(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """A daily scan would cost 4-5x for signal that changes on a weekly timescale.

    The scheduled run exists to catch an advisory published against a dependency that has *not*
    changed — the majority of real findings, and the only kind no change-triggered run can see.
    That is a weekly question, and the day-of-week field is what keeps the budget note honest.
    """
    schedule = _triggers(workflows[Workflow.VULNERABILITY_SCAN])[Trigger.SCHEDULE]
    assert len(schedule) == 1, "one scheduled run, not several"
    day_of_week = schedule[0]["cron"].split()[4]
    assert day_of_week not in {"*", "?"}, (
        f"cron {schedule[0]['cron']!r} runs every day; the recorded budget assumes weekly"
    )


def test_the_deploy_sequence_is_identical_in_both_workflows(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """`ci.yml` and `deploy.yml` must run the *same* deploy sequence.

    Otherwise "what a deploy does" has two definitions and the PR-time one can drift into checking
    something else. **Retargeted by Issue 9:** the source project compared inline `alembic upgrade
    head` bodies in `ci.yml` and `deploy-sequence.yml`; ClinicQ keeps the sequence in one script,
    so both workflows must call it, and the script must still run the migrations.
    """
    script = (REPO_ROOT / DEPLOY_SEQUENCE_SCRIPT).read_text(encoding="utf-8")
    assert "alembic upgrade head" in script

    def _calls_the_sequence(workflow: dict[str, Any]) -> bool:
        return any(
            DEPLOY_SEQUENCE_SCRIPT in str(step.get("run", ""))
            for job in _jobs(workflow).values()
            for step in job.get("steps", [])
        )

    assert _calls_the_sequence(workflows[Workflow.CI])
    assert _calls_the_sequence(workflows[Workflow.DEPLOY])


def test_runner_jobs_cap_their_own_runtime(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """A hung job must stop on its own; the 360-minute default is not a safety net."""
    for name, workflow in workflows.items():
        for job_name, job in _jobs(workflow).items():
            if "runs-on" not in job:
                continue
            timeout = job.get("timeout-minutes")
            assert timeout is not None, f"{name}:{job_name}"
            assert 0 < timeout < GITHUB_DEFAULT_TIMEOUT_MINUTES, f"{name}:{job_name}"


def test_ci_jobs_stay_well_inside_the_default_timeout(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """Issue 9: "every job has an explicit timeout well below the GitHub default"."""
    for job_name, job in _jobs(workflows[Workflow.CI]).items():
        assert job["timeout-minutes"] <= CI_JOB_TIMEOUT_CEILING_MINUTES, job_name


def test_reusable_workflow_callers_set_no_timeout(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """GitHub rejects `timeout-minutes` on a `uses:` job — the whole workflow fails to load."""
    for name, workflow in workflows.items():
        for job_name, job in _jobs(workflow).items():
            if "uses" in job:
                assert "timeout-minutes" not in job, f"{name}:{job_name}"


def test_deploys_are_never_cancelled_mid_flight(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """A half-applied deploy is worse than a slow one, so CD queues rather than cancels.

    The group must also stay ref-independent: the contended resource is the single
    platform slot, so two different tags have to serialise against each other.
    """
    concurrency = workflows[Workflow.DEPLOY]["concurrency"]
    assert concurrency["cancel-in-progress"] is False
    assert "github.ref" not in concurrency["group"]


def test_ci_cancels_only_superseded_pull_request_runs(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """Pushing twice in a minute cancels the first run; a manual run is never cut short.

    **Rewritten by Issue 9** from "CI never cancels a run that can publish an image": ClinicQ's CI
    cannot publish (next test), so what is left to protect is the other half, spending no minutes
    on a superseded push. The group is keyed on the pull request, so two PRs never cancel each
    other.
    """
    concurrency = workflows[Workflow.CI]["concurrency"]
    assert "github.event.pull_request.number" in concurrency["group"]
    cancel = concurrency["cancel-in-progress"]
    assert isinstance(cancel, str), cancel
    assert Trigger.PULL_REQUEST in cancel
    assert Trigger.WORKFLOW_DISPATCH not in cancel


def test_ci_can_never_publish_an_image(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """A cancellable run must not be able to write to GHCR: publishing is the release's job.

    Cancelling mid-push can leave `:latest` on a half-written manifest, which is why the source
    project kept image publishing out of reach of a cancellable run. Here that is structural: CI
    holds no `packages: write`, logs in to no registry and pushes nothing.
    """
    ci = workflows[Workflow.CI]
    scopes = [ci.get("permissions", {})] + [
        job.get("permissions", {}) for job in _jobs(ci).values()
    ]
    assert all(scope.get("packages") != "write" for scope in scopes)
    for job_name, job in _jobs(ci).items():
        for step in job.get("steps", []):
            assert "docker/login-action" not in str(step.get("uses", "")), job_name
            assert step.get("with", {}).get("push") in (None, False), job_name
            assert "docker push" not in str(step.get("run", "")), job_name


def test_uv_jobs_cache_uv_rather_than_pip(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """`uv pip install` reads ~/.cache/uv, so setup-python's `cache: pip` warms nothing.

    The two are mutually exclusive per job: caching pip beside a uv install is the silent
    no-op this guardrail exists to prevent, and caching uv beside a pip install is the
    same mistake mirrored.
    """
    for job_name, job in _jobs(workflows[Workflow.CI]).items():
        steps = job.get("steps", [])
        installs_with_uv = any(
            "uv pip install" in str(step.get("run", "")) for step in steps
        )
        caches_pip = any(
            str(step.get("uses", "")).startswith("actions/setup-python")
            and step.get("with", {}).get("cache") == "pip"
            for step in steps
        )
        caches_uv = any(
            str(step.get("uses", "")).startswith("actions/cache")
            and "~/.cache/uv" in str(step.get("with", {}).get("path", ""))
            for step in steps
        )
        assert caches_uv is installs_with_uv, job_name
        assert not (installs_with_uv and caches_pip), job_name


def _ci_shard_tokens(workflows: dict[Workflow, dict[str, Any]]) -> list[str]:
    """Every whitespace-separated token across the `test` job's matrix shard `paths` strings.

    Tokens are a mix of directory paths (`tests/unit/leases`) and pytest flags
    (`--ignore=tests/unit`); callers filter to the kind they care about.
    """
    matrix = _jobs(workflows[Workflow.CI])["test"]["strategy"]["matrix"]
    return [token for shard in matrix["include"] for token in shard["paths"].split()]


def _tests_in_file(path: str) -> set[tuple[str, str]]:
    """Every test pytest would collect from one file, as (file, function name) pairs.

    Read from the source rather than by importing it: this guard runs in the unit shard, which has
    no browser and no database, and must not need what the files it reads need.
    """
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
    bodies = [tree.body] + [
        node.body
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test")
    ]
    return {
        (path, child.name)
        for body in bodies
        for child in body
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
        and child.name.startswith("test")
    }


def _shard_tests(tokens: list[str]) -> set[tuple[str, str]]:
    """The tests one shard's `paths` collect, as (file, function name) pairs.

    Understands the four kinds of token the matrix uses: a directory, a file, one test
    (`file.py::name`), and the two exclusions — `--ignore=<path>` for a subtree or file, and
    `--deselect <file.py::name>` for a single test.
    """
    ignored = [
        token.removeprefix("--ignore=")
        for token in tokens
        if token.startswith("--ignore=")
    ]
    deselected: set[tuple[str, str]] = set()
    files: set[str] = set()
    tests: set[tuple[str, str]] = set()
    expect_deselect = False
    for token in tokens:
        if expect_deselect:
            path, _, name = token.partition("::")
            deselected.add((path, name))
            expect_deselect = False
            continue
        if token == "--deselect":
            expect_deselect = True
            continue
        if token.startswith("-"):
            continue
        if "::" in token:
            path, _, name = token.partition("::")
            tests.add((path, name))
            continue
        target = REPO_ROOT / token
        if target.is_dir():
            files |= {
                path.relative_to(REPO_ROOT).as_posix()
                for pattern in ("test_*.py", "*_test.py")
                for path in target.rglob(pattern)
                if "__pycache__" not in path.parts
            }
        else:
            files.add(token)
    kept = [
        path
        for path in files
        if not any(
            path == prefix or path.startswith(f"{prefix}/") for prefix in ignored
        )
    ]
    for path in kept:
        tests |= _tests_in_file(path)
    return tests - deselected


def _tests_on_disk() -> set[tuple[str, str]]:
    """Every test pytest would collect under `tests/`, as (file, function name) pairs."""
    files = {
        path.relative_to(REPO_ROOT).as_posix()
        for pattern in ("test_*.py", "*_test.py")
        for path in TESTS_DIR.rglob(pattern)
        if "__pycache__" not in path.parts
    }
    return {test for path in files for test in _tests_in_file(path)}


def test_every_test_runs_in_exactly_one_shard(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """The shards partition the suite: nothing is left out, nothing is run twice.

    The `test` job trades Actions minutes for wall clock by running its shards side by side, which
    only works if every file has exactly one owner. A test directory added without a shard to claim
    it would otherwise stop running the day it is created, silently and greenly. A file claimed by
    two shards is the cheaper mistake — it only costs minutes and doubles the coverage data — but it
    is still a mistake, and both are one edit of the matrix away.
    """
    matrix = _jobs(workflows[Workflow.CI])[CiJob.TEST]["strategy"]["matrix"]
    shards = {
        shard["name"]: _shard_tests(shard["paths"].split())
        for shard in matrix["include"]
    }
    on_disk = _tests_on_disk()

    claimed: set[tuple[str, str]] = set()
    twice: dict[tuple[str, str], list[str]] = {}
    for tests in shards.values():
        for test in tests & claimed:
            twice.setdefault(test, [n for n, t in shards.items() if test in t])
        claimed |= tests
    assert not twice, f"tests claimed by more than one shard: {twice}"

    missing = on_disk - claimed
    assert not missing, f"tests no shard runs: {sorted(missing)}"

    unknown = claimed - on_disk
    assert not unknown, f"shards name tests that do not exist: {sorted(unknown)}"


def test_no_shard_is_empty(workflows: dict[Workflow, dict[str, Any]]) -> None:
    """A shard whose paths collect nothing is a job that pays its overhead to run no tests."""
    matrix = _jobs(workflows[Workflow.CI])[CiJob.TEST]["strategy"]["matrix"]
    for shard in matrix["include"]:
        assert _shard_tests(shard["paths"].split()), shard["name"]


def test_ci_still_runs_the_top_level_flow_tests(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """The cross-domain flow files under tests/ are not in a domain dir, so a shard must claim them.

    That shard collects the whole `tests` tree with both layer subtrees ignored, which is exactly
    the top-level *_flow.py / contract / matrix files — and picks up new ones automatically. Assert
    the collector is present so those tests cannot fall out of CI when the matrix is next edited.
    """
    tokens = _ci_shard_tokens(workflows)
    assert "tests" in tokens, "no shard collects the top-level tests/ directory"
    assert "--ignore=tests/unit" in tokens
    assert "--ignore=tests/integration" in tokens


# ── Issue 9: what the PR-time gate must keep true ──────────────────────────────────────────


def _ci_local_stages() -> set[str]:
    """The stage names `ci-local.sh` declares with `stage "<name>"`."""
    return set(
        re.findall(r'^\s*stage "([\w-]+)"', CI_LOCAL.read_text(), flags=re.MULTILINE)
    )


def test_every_ci_local_stage_is_mirrored_or_declared_local_only() -> None:
    """A stage added to `ci-local.sh` must be placed in CI, or named local-only, on purpose."""
    assert _ci_local_stages() == set(STAGE_JOBS)


def test_ci_runs_the_same_commands_as_ci_local(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """The workflow is the same set of checks as `ci-local.sh`: each command appears in both."""
    ci_local = CI_LOCAL.read_text(encoding="utf-8")
    jobs = _jobs(workflows[Workflow.CI])
    for stage, commands in STAGE_COMMANDS.items():
        job = STAGE_JOBS[stage]
        assert job is not None, stage
        job_text = _job_text(jobs[job])
        for command in commands:
            assert command in ci_local, (
                f"ci-local.sh no longer runs {command!r} ({stage})"
            )
            assert command in job_text, (
                f"ci.yml:{job} does not run {command!r} ({stage})"
            )


def test_the_shards_default_to_the_local_gates_distribution(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """A shard that says nothing runs pytest exactly as `ci-local.sh` does, and only the browser
    shards say anything.

    The shards exist to spend Actions minutes on wall clock, not to run a different suite in a
    different way. `-n auto --dist loadscope` stays the default on both sides, so the only
    deviations are the browser shards, whose tests spend their time waiting on a page rather than
    on a CPU and therefore distribute by test instead of by module.
    """
    assert "-n auto --dist loadscope" in CI_LOCAL.read_text(encoding="utf-8")

    test_job = _jobs(workflows[Workflow.CI])[CiJob.TEST]
    job_text = _job_text(test_job)
    assert "-n ${{ matrix.workers || 'auto' }}" in job_text
    assert "--dist ${{ matrix.dist || 'loadscope' }}" in job_text

    for shard in test_job["strategy"]["matrix"]["include"]:
        if {"dist", "workers"} & set(shard):
            assert shard.get("browser"), (
                f"{shard['name']} runs pytest differently from the local gate without being a "
                "browser shard"
            )


def test_the_local_only_scans_stay_out_of_ci(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """pip-audit and Trivy are local-only by design until Issue 97 schedules them."""
    raw = (WORKFLOW_DIR / Workflow.CI).read_text(encoding="utf-8").lower()
    body = "\n".join(
        line for line in raw.splitlines() if not line.lstrip().startswith("#")
    )
    for tool in LOCAL_ONLY_TOOLS:
        assert tool not in body, f"ci.yml runs {tool}, which is local-only"


def test_ci_uses_the_gitleaks_version_the_hook_pins(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """The commit hook and CI scan with one gitleaks, or a finding appears on only one side."""
    hooks = yaml.safe_load(PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))
    (hook_rev,) = [
        repo["rev"] for repo in hooks["repos"] if repo["repo"].endswith("/gitleaks")
    ]
    versions = [
        step["env"]["GITLEAKS_VERSION"]
        for step in _jobs(workflows[Workflow.CI])[CiJob.QUALITY]["steps"]
        if "GITLEAKS_VERSION" in step.get("env", {})
    ]
    assert versions == [hook_rev.removeprefix("v")]


def test_ci_tests_run_against_postgis_and_redis_never_sqlite(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """The test job's database is the dev stack's PostGIS, and its tests cannot skip past it."""
    dev_stack = yaml.safe_load(DEV_STACK_SERVICES.read_text(encoding="utf-8"))[
        "services"
    ]
    test = _jobs(workflows[Workflow.CI])[CiJob.TEST]
    images = {name: service["image"] for name, service in test["services"].items()}
    assert sorted(images.values()) == sorted(
        [dev_stack["db"]["image"], dev_stack["redis"]["image"]]
    )

    env = test["env"]
    assert env["REQUIRE_POSTGRES_TESTS"] == "1"
    assert env["REQUIRE_REDIS_TESTS"] == "1"
    assert env["TEST_DATABASE_URL"].startswith("postgresql://")
    assert env["TEST_REDIS_URL"].startswith("redis://")
    urls = [
        str(value)
        for scope in [env] + [step.get("env", {}) for step in test["steps"]]
        for key, value in scope.items()
        if "DATABASE_URL" in key
    ]
    assert urls and all(url.startswith("postgresql://") for url in urls), urls


def test_the_merge_gate_needs_every_other_job(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """The required check waits for every job, and reports even when one of them failed."""
    jobs = _jobs(workflows[Workflow.CI])
    gate = jobs[CiJob.GATE]
    assert set(gate["needs"]) == set(jobs) - {CiJob.GATE}
    assert "always()" in gate["if"]


def test_every_action_is_pinned_to_a_commit(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """A tag can be moved to other code after review; a commit SHA cannot."""
    for name, workflow in workflows.items():
        for job_name, job in _jobs(workflow).items():
            references = [job["uses"]] if "uses" in job else []
            references += [
                step["uses"] for step in job.get("steps", []) if "uses" in step
            ]
            for reference in references:
                if reference.startswith(("./", "docker://")):
                    continue
                assert _PINNED_ACTION.match(reference), (
                    f"{name}:{job_name}: {reference}"
                )


def _paths_named_in(source: str) -> set[str]:
    """Every path a Python file spells out: string literals and `a / "b" / "c"` chains.

    Docstrings are left out; they describe files without reading them.
    """
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    named: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            parts: list[str] = []
            stack: list[ast.expr] = [node]
            while stack:
                current = stack.pop()
                if isinstance(current, ast.BinOp) and isinstance(current.op, ast.Div):
                    stack += [current.right, current.left]
                elif isinstance(current, ast.Constant) and isinstance(
                    current.value, str
                ):
                    parts.append(current.value)
            named.add("/".join(parts))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            named.add(node.value)
    return named


def test_the_prose_only_fast_path_skips_nothing_a_test_reads(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """A pull request touching only PROSE_PATHS skips the tests, so no test may read those paths.

    If this fails, a test has started reading one of them: drop that entry from PROSE_PATHS in
    ci.yml (the change then runs the full suite), rather than excluding the test from this check.
    """
    step = next(
        step
        for step in _jobs(workflows[Workflow.CI])[CiJob.CHANGES]["steps"]
        if "PROSE_PATHS" in step.get("env", {})
    )
    prose = step["env"]["PROSE_PATHS"].split()
    offenders = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        for named in _paths_named_in(path.read_text(encoding="utf-8")):
            for entry in prose:
                folder = entry.endswith("/")
                if (folder and named.startswith(entry.rstrip("/"))) or (
                    not folder and (named == entry or named.endswith(f"/{entry}"))
                ):
                    offenders.append(f"{path.relative_to(REPO_ROOT)} reads {named}")
    assert offenders == []


# ── Issue 10: the release ─────────────────────────────────────────────────────────────────────


def _release_steps(workflows: dict[Workflow, dict[str, Any]]) -> list[dict[str, Any]]:
    """The steps of release.yml's one job, in order."""
    (job,) = _jobs(workflows[Workflow.RELEASE]).values()
    return job["steps"]


def test_the_release_runs_on_version_tags_and_nothing_else(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """An image is published for a v*.*.* tag only: never a branch, a pull request or a schedule."""
    assert _triggers(workflows[Workflow.RELEASE]) == {
        Trigger.PUSH: {"tags": [VERSION_TAG_PATTERN]}
    }


def test_the_release_is_never_cancelled_mid_push(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """Cancelling mid-push can leave `:latest` on a half-written manifest, so releases queue.

    The group does not depend on the tag, so two tags pushed together publish in order.
    """
    concurrency = workflows[Workflow.RELEASE]["concurrency"]
    assert concurrency["cancel-in-progress"] is False
    assert "github.ref" not in concurrency["group"]


def test_the_release_moves_latest_only_after_the_image_passed_every_check(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """The build pushes `:<sha>` alone; `:<version>` and `:latest` point at it once it is checked.

    Tagging the checked digest (never rebuilding) is what makes the three tags one manifest, and
    what makes a rollback a redeploy of an older tag.
    """
    steps = _release_steps(workflows)
    runs = [str(step.get("run", "")) for step in steps]
    (build,) = [
        i
        for i, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("docker/build-push-action")
    ]
    assert steps[build]["with"]["tags"].endswith(":${{ github.sha }}")
    assert "latest" not in steps[build]["with"]["tags"]
    (promote,) = [i for i, run in enumerate(runs) if "imagetools create" in run]
    assert "latest" in runs[promote]
    for check, marker in RELEASE_IMAGE_CHECKS.items():
        (at,) = [i for i, run in enumerate(runs) if marker in run]
        assert build < at < promote, (
            f"the {check} check must run between build and :latest"
        )


def test_the_release_enforces_an_agreed_size_ceiling(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """A ceiling, stated once, that the size check fails against."""
    ceiling = workflows[Workflow.RELEASE]["env"]["IMAGE_SIZE_CEILING_MB"]
    assert int(ceiling) > 0
    assert any(
        "exit 1" in str(step.get("run", ""))
        and "IMAGE_SIZE_CEILING_MB" in str(step.get("run", ""))
        for step in _release_steps(workflows)
    )


def test_the_conventions_check_is_its_own_job_that_blocks_nothing_but_the_gate(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """Issue 13: a naming slip never stops the tests, and re-running the check alone works.

    A step with ``continue-on-error`` would leave its job green, so "Re-run failed jobs" would
    re-run only the gate, with the stale result, after the description was fixed.
    """
    jobs = _jobs(workflows[Workflow.CI])
    conventions = jobs[CiJob.CONVENTIONS]
    assert "needs" not in conventions
    assert CiJob.CONVENTIONS in jobs[CiJob.GATE]["needs"]
    (step,) = [
        step
        for step in conventions["steps"]
        if "check_pr_conventions.py" in str(step.get("run", ""))
    ]
    assert "continue-on-error" not in step
    assert "github.head_ref" not in str(step["run"]), (
        "the branch name must come via env"
    )
    for job_name, job in jobs.items():
        assert CiJob.CONVENTIONS not in job.get("needs", []) or job_name == CiJob.GATE


# ── Issue 11: the deploy ──────────────────────────────────────────────────────────────────────


def _deploy_steps(workflows: dict[Workflow, dict[str, Any]]) -> list[dict[str, Any]]:
    """The steps of deploy.yml's one job, in order."""
    (job,) = _jobs(workflows[Workflow.DEPLOY]).values()
    return job["steps"]


def test_nothing_deploys_itself_every_deploy_is_a_manual_run(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """A person chooses every deploy: the workflow has one trigger, and it is `Run workflow`.

    **Rewritten by the manual-only change.** Staging used to deploy itself from a successful
    Release (`workflow_run`), which made pushing a tag both a release and a deploy. It no longer
    does: release.yml publishes the image, and this workflow puts a chosen version on a chosen
    environment. No fallback may creep back in either — an `inputs.environment || 'staging'`
    default is the automatic staging deploy wearing a different hat, and it would silently deploy
    to staging if the choice were ever missing.
    """
    deploy = workflows[Workflow.DEPLOY]
    assert set(_triggers(deploy)) == {Trigger.WORKFLOW_DISPATCH}
    (job,) = _jobs(deploy).values()
    assert job["environment"]["name"] == "${{ inputs.environment }}"
    assert "if" not in job, "a manual run needs no condition to tell the events apart"
    raw = (WORKFLOW_DIR / Workflow.DEPLOY).read_text(encoding="utf-8")
    assert "workflow_run" not in raw, (
        "a leftover github.event.workflow_run.* expression is empty on a manual run"
    )


def test_the_deploy_run_names_the_product_and_the_version(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """`run-name` is what the Actions list shows: "Deploy ClinicQ 0.2.0", never a bare "Deploy".

    Release and Deploy runs sit in the same list, several of each per version; a run that does not
    say which product and which version it carries has to be opened to be identified.
    """
    assert (
        workflows[Workflow.DEPLOY]["run-name"] == "Deploy ClinicQ ${{ inputs.version }}"
    )


def test_migrations_run_before_the_new_image_serves_and_never_on_a_rollback(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """Migrate, then a candidate nothing routes to, then the swap; a rollback skips the migrations."""
    runs = [str(step.get("run", "")) for step in _deploy_steps(workflows)]
    (migrate,) = [i for i, run in enumerate(runs) if DEPLOY_SEQUENCE_SCRIPT in run]
    (candidate,) = [i for i, run in enumerate(runs) if "deploy.sh candidate" in run]
    (swap,) = [i for i, run in enumerate(runs) if "deploy.sh swap" in run]
    assert migrate < candidate < swap
    assert "env.ROLLBACK != 'true'" in _deploy_steps(workflows)[migrate]["if"]


def test_the_app_settings_reach_the_host_privately(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """APP_ENV holds every secret: it is written with umask 077 and never echoed to the log."""
    for step in _deploy_steps(workflows):
        if "secrets.APP_ENV" not in str(step.get("env", {})):
            continue
        run = str(step["run"])
        assert 'echo "$APP_ENV"' not in run and "cat .env" not in run
        if "$APP_ENV" in run and "printf" in run:
            assert "umask 077" in run


def test_the_deploy_runs_on_the_host_rather_than_reaching_it_over_ssh(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """Issue 230: the runner **is** the host, so no step may ssh, scp or handle a deploy key.

    ClinicQ runs on the BTK platform server, which hosts this repository's own self-hosted runner.
    Reaching that same machine over SSH meant four Environment secrets whose only job was to let the
    job travel to where it already was, and a deploy that reported "Not provisioned (missing
    DEPLOY_HOST DEPLOY_SSH_KEY DEPLOY_KNOWN_HOSTS APP_ENV DEPLOY_DIR)" until every one of them
    existed.
    """
    deploy = workflows[Workflow.DEPLOY]
    job = _jobs(deploy)["deploy"]
    labels = str(job["runs-on"])
    assert "self-hosted" in labels and "clinicq" in labels, labels

    banned = ("DEPLOY_HOST", "DEPLOY_SSH_KEY", "DEPLOY_KNOWN_HOSTS", "DEPLOY_USER")
    for step in job["steps"]:
        source = str(step.get("run", "")) + str(step.get("env", {}))
        for name in banned:
            assert name not in source, f"{step.get('name')}: {name}"
        for command in ("ssh ", "scp ", "ssh-keyscan", "known_hosts"):
            assert command not in str(step.get("run", "")), (
                f"{step.get('name')}: {command}"
            )


def test_a_deploy_needs_no_configuration_on_a_platform_host(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """Issue 230: the deploy directory and the settings both have a working default.

    ``/opt/btk/<slug>`` is the platform layout, and the app's ``.env`` lives in it; ``DEPLOY_DIR``
    and ``APP_ENV`` stay as overrides for a host laid out differently or a team that would rather
    keep the settings in GitHub.
    """
    steps = {str(step.get("name", "")): step for step in _deploy_steps(workflows)}
    target = steps["Where this environment lives"]
    assert "/opt/btk/clinicq" in str(target["run"])

    settings = steps["The app's settings"]
    run = str(settings["run"])
    # Either source is enough on its own; only having neither is an error.
    assert 'if [[ -n "$APP_ENV" ]]' in run
    assert 'elif [[ -s "$DEPLOY_DIR/.env" ]]' in run
