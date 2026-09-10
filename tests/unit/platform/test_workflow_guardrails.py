"""The pipeline guardrails from Issue 15, asserted against the workflow files themselves.

Every rule here protects the monthly GitHub Actions allowance or a deploy in flight, and
every one of them is a single YAML line that a later PR can drop without anything failing
until the bill (or the outage) arrives. These tests are that missing failure.

Offline and dependency-free: the workflows are parsed, never run.

Note on `on:` — PyYAML reads YAML 1.1, where a bare `on` key is the boolean `True`. Use
`_triggers()` rather than `workflow["on"]`, which silently misses.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW_DIR = Path(__file__).resolve().parents[3] / ".github" / "workflows"


# GitHub's default job timeout. A job wedged at this ceiling burns 360 of the 2,000 free
# private-repo minutes in one run, which is the whole reason `timeout-minutes` is mandatory.
GITHUB_DEFAULT_TIMEOUT_MINUTES = 360


class Workflow(StrEnum):
    """The six workflow files, by filename."""

    CI = "ci.yml"
    CD = "cd.yml"
    SMOKE = "smoke.yml"
    VULNERABILITY_SCAN = "vulnerability-scan.yml"
    DEPLOY_SEQUENCE = "deploy-sequence.yml"
    SYNC_ISSUES = "sync-issues.yml"


class Trigger(StrEnum):
    """The `on:` keys these tests reason about."""

    PUSH = "push"
    PULL_REQUEST = "pull_request"
    SCHEDULE = "schedule"
    WORKFLOW_DISPATCH = "workflow_dispatch"


#: The workflows Issue 180 gave a **path-filtered** `pull_request` trigger, and why each earns its
#: minutes. Anything not listed may not carry one at all — Issue 15's rule is unchanged for
#: `ci.yml`, which remains the expensive workflow.
PR_TRIGGERED: dict[Workflow, str] = {
    Workflow.VULNERABILITY_SCAN: "scans the inputs of a dependency or image vulnerability",
    Workflow.DEPLOY_SEQUENCE: "runs alembic upgrade head when a migration or seed changes",
}


def _load(workflow: Workflow) -> dict[str, Any]:
    """Parse one workflow file into a plain dict."""
    return yaml.safe_load((WORKFLOW_DIR / workflow).read_text(encoding="utf-8"))


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    """Return the `on:` mapping, tolerating YAML 1.1 folding `on` into `True`."""
    return workflow.get(True, workflow.get("on", {}))


def _jobs(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the `jobs:` mapping."""
    return workflow.get("jobs", {})


# Fully commented-out workflow files (kept on disk for reference) are not active GitHub
# Actions definitions, so they are skipped in the structural guardrail checks. There are
# none right now — vulnerability-scan.yml is an on-demand (workflow_dispatch, keyword-gated)
# active workflow — but the set is kept so a future disabled reference workflow is covered.
DISABLED_WORKFLOWS: frozenset[Workflow] = frozenset()


@pytest.fixture(scope="module")
def workflows() -> dict[Workflow, dict[str, Any]]:
    """Every active workflow file, parsed once."""
    return {name: _load(name) for name in Workflow if name not in DISABLED_WORKFLOWS}


def test_every_workflow_file_is_covered() -> None:
    """A workflow added without a `Workflow` member would skip every check below."""
    on_disk = {path.name for path in WORKFLOW_DIR.glob("*.yml")}
    assert on_disk == {str(name) for name in Workflow}


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


def test_nothing_runs_unfiltered_on_an_ordinary_push_or_pull_request(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """Quality is a local gate (`ci-local.sh`); Actions minutes are spent on releases.

    This is also what makes a docs-only PR free, and why there is no branch-vs-PR double
    run: the branch trigger that would duplicate a `pull_request` run does not exist.
    `sync-issues.yml` is the one allowed branch push, and it is path-filtered to the
    issue specs.

    **Issue 180 (M30) narrowed this rule rather than relaxing it.** Two workflows now carry a
    `pull_request` trigger, because "nothing runs on a PR" also meant nothing scanned and nothing
    executed a migration outside a release — which is how migrations 0066/0067 reached a
    developer's laptop unexecuted and how the code behind v1.2.1-v1.2.4 went unscanned. Both are
    **path-filtered**; the guard is that they stay filtered, and that no *other* workflow grows a
    `pull_request` trigger at all.
    """
    for name, workflow in workflows.items():
        triggers = _triggers(workflow)
        pull_request = triggers.get(Trigger.PULL_REQUEST)
        if name in PR_TRIGGERED:
            assert pull_request is not None, f"{name}: expected a pull_request trigger"
            assert pull_request.get("paths"), (
                f"{name}: a pull_request trigger must be path-filtered — an unfiltered one runs on "
                f"every PR, the cost Issue 15's guardrails exist to avoid ({PR_TRIGGERED[name]})"
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
        if name is Workflow.SYNC_ISSUES:
            assert push["paths"], name
            continue
        # Everything else may only be triggered by a semver tag.
        assert "branches" not in push, name
        assert push["tags"], name


def test_ci_itself_never_runs_on_a_pull_request(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """The expensive workflow stays tag-only, asserted separately so it cannot relax by accident.

    `ci.yml` runs eight sharded test jobs plus quality plus a PostgreSQL drift job. Giving it a
    `pull_request` trigger — even a path-filtered one — is the change Issue 180's non-goals name
    explicitly, so it gets its own assertion rather than depending on someone reading a dict.
    """
    assert Trigger.PULL_REQUEST not in _triggers(workflows[Workflow.CI])


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
    """`ci.yml` and `deploy-sequence.yml` must run the *same* commands.

    Otherwise "what a deploy does" has two definitions and the PR-time one can drift into checking
    something else, which is exactly the failure this issue is correcting. Compared on the shell
    body rather than the whole job, because the two legitimately differ in what they check out.
    """

    def _sequence(workflow: dict[str, Any]) -> str:
        for job in _jobs(workflow).values():
            for step in job.get("steps", []):
                if "alembic upgrade head" in str(step.get("run", "")):
                    return str(step["run"])
        raise AssertionError("no step runs `alembic upgrade head`")

    assert _sequence(workflows[Workflow.CI]) == _sequence(
        workflows[Workflow.DEPLOY_SEQUENCE]
    )


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
    concurrency = workflows[Workflow.CD]["concurrency"]
    assert concurrency["cancel-in-progress"] is False
    assert "github.ref" not in concurrency["group"]


def test_ci_never_cancels_a_run_that_can_publish_an_image(
    workflows: dict[Workflow, dict[str, Any]],
) -> None:
    """Cancelling mid-push to GHCR can leave `:latest` on a half-written manifest.

    So `cancel-in-progress` has to be an expression that excludes tag pushes and manual
    releases — a bare `true` would cancel the publish, and a bare `false` would waste
    minutes on superseded `quality` / `test` dispatches.
    """
    cancel = workflows[Workflow.CI]["concurrency"]["cancel-in-progress"]
    assert isinstance(cancel, str), cancel
    assert Trigger.WORKFLOW_DISPATCH in cancel
    assert "release" in cancel


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
