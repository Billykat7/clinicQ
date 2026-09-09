"""M30's exit criteria, re-derived from shipped code (Issue #182).

Issue #182 asks for the milestone's exit criteria to be re-verified **independently** — from what
actually shipped, not read back from the four PR descriptions that claimed them. A PR description is
an argument; this file is the part of that argument a machine can check, and it deliberately
re-derives each fact from the tree rather than asserting that some other test exists.

Where a criterion cannot be checked from code — "no page renders unstyled", "the pen test was
authorized" — it is **absent here and stated as unverifiable-from-code** in
`docs/SECURITY/PENTEST-2026-08-29.md` §7, rather than ticked. That distinction is the point of an
independent pass: a criterion nobody can check mechanically should look different from one anybody
can, not the same shade of green.

Each test below names the milestone criterion it re-derives.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

import ast
import re
from pathlib import Path

import yaml

from src.commons.enums import RateLimitBackendKind
from src.core.config import Settings
from src.core.security_headers import build_content_security_policy

REPO_ROOT = Path(__file__).resolve().parents[3]


SECURITY_DOCS = REPO_ROOT / "docs" / "SECURITY"


WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def test_no_residual_risk_is_left_unowned() -> None:
    """§7's promise: every entry is restated, retired, or handed to a **named** issue.

    "Unowned" is the failure mode M30 exists to correct — the M17 model listed five residuals and
    left them to age. A row that says neither what happened to it nor who has it is that failure
    returning, so each is required to carry a disposition word or an issue link.
    """
    text = (SECURITY_DOCS / "THREAT-MODEL.md").read_text(encoding="utf-8")
    section = text.split("## 7.", 1)[1].split("\n## ", 1)[0]
    rows = [
        line
        for line in section.splitlines()
        if line.startswith("| **R") and line.count("|") >= 4
    ]
    assert len(rows) >= 5, (
        f"expected at least the five M17 residuals, found {len(rows)}"
    )
    unowned = [
        row.split("|")[1].strip()
        for row in rows
        # "Accepted" counts: R6 and R8 were *identified* by this revision rather than carried
        # forward, so there was nothing to restate — accepting one with its controls and its
        # residual stated is a disposition, not a silence.
        if not re.search(
            r"(closed|retired|restated|accepted|handed to|issue #\d+)", row, re.I
        )
    ]
    assert unowned == [], f"residual risk(s) with no disposition: {unowned}"


def test_exactly_one_client_ip_resolver_exists_in_the_tree() -> None:
    """Re-derived by AST-walking `src/`, independently of the guard Issue #179 shipped.

    Two checks of the same fact from different files is not redundancy here: this one would also
    fail if that guard were deleted, which is the failure a re-verification pass exists to catch.
    """
    canonical = REPO_ROOT / "src" / "core" / "client_ip.py"
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        if path == canonical:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            name = node.name.lstrip("_").lower()
            if "client_ip" in name and not name.startswith("get_client_ip"):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.name}")
    assert offenders == [], offenders


def test_proxy_trust_is_off_by_default_in_shipped_settings() -> None:
    """The non-goal Issue #179 named: this must not have quietly become ``True``."""
    defaults = Settings(_env_file=None, jwt_secret="x" * 40)  # type: ignore[arg-type]
    assert defaults.trust_proxy_headers is False


def test_the_shipped_default_limiter_is_unchanged_from_pre_m30() -> None:
    """ "Unchanged for a single-worker run" is a claim about the *default*, so read the default."""
    from src.core.rate_limit import _build_backend
    from src.core.rate_limit_backend import InProcessRateLimitBackend

    defaults = Settings(_env_file=None, jwt_secret="x" * 40)  # type: ignore[arg-type]
    assert defaults.rate_limit_backend is RateLimitBackendKind.MEMORY
    assert isinstance(_build_backend(defaults), InProcessRateLimitBackend)


def test_the_shared_backend_degrades_rather_than_denying() -> None:
    """Re-derived by driving a backend whose store raises, rather than trusting a comment.

    The rule: a limiter must never be the reason the site is down. A shared store is a dependency
    on the hot path of every sign-in, and its outage is far likelier than the abuse it prevents.
    """
    from src.core.rate_limit_backend import SharedRateLimitBackend

    class _Down:
        def pipeline(self, transaction: bool = True) -> object:
            raise RuntimeError("connection refused")

    backend = SharedRateLimitBackend(_Down())
    assert backend.hit("probe", window_seconds=60) == 1, (
        "an outage must not deny the request"
    )
    assert backend.is_degraded


def _workflow(name: str) -> dict:
    """Parse one workflow (PyYAML folds a bare ``on`` key into ``True``)."""
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    """Return the ``on:`` mapping."""
    return workflow.get(True, workflow.get("on", {}))


def test_scanning_runs_on_a_schedule_and_on_the_files_that_can_break_it() -> None:
    """Re-derived from the workflow file: both triggers present, and the PR one path-filtered."""
    triggers = _triggers(_workflow("vulnerability-scan.yml"))
    assert triggers.get("schedule"), "no scheduled scan"
    assert triggers["pull_request"].get("paths"), "the PR trigger is not path-filtered"
    assert "requirements.txt" in triggers["pull_request"]["paths"]


def test_all_three_scanners_are_present_and_blocking() -> None:
    """A scan that cannot fail is a report, not a gate — so check for the jobs *and* the exit code."""
    workflow = _workflow("vulnerability-scan.yml")
    assert {"secret-scan", "dependency-scan", "container-scan"} <= set(workflow["jobs"])
    trivy = next(
        step
        for step in workflow["jobs"]["container-scan"]["steps"]
        if "trivy-action" in str(step.get("uses", ""))
    )
    assert trivy["with"]["exit-code"] == "1"


def test_the_deploy_sequence_runs_on_migration_changes() -> None:
    """The M29 incident's guard: migrations 0066/0067 first ran on a laptop."""
    triggers = _triggers(_workflow("deploy-sequence.yml"))
    paths = triggers["pull_request"]["paths"]
    assert "alembic/**" in paths
    assert "scripts/db/**" in paths


def test_ci_itself_is_still_tag_only() -> None:
    """The guardrail Issue 180 had to work *inside*, re-derived rather than taken on trust."""
    triggers = _triggers(_workflow("ci.yml"))
    assert "pull_request" not in triggers
    assert triggers["push"]["tags"]
    assert "branches" not in triggers["push"]


def test_no_directive_in_the_shipped_policy_allows_unsafe_inline() -> None:
    """Re-derived from the policy builder the middleware calls."""
    assert "'unsafe-inline'" not in build_content_security_policy("PROBE")


def test_style_src_can_still_admit_the_one_style_block_in_the_tree() -> None:
    """The half that is easy to get wrong, and did go wrong once during Issue #181.

    `'self'` covers same-origin *stylesheets*, not inline `<style>` elements — so a policy without
    a nonce source silently blocks the login modal's block and the page renders unstyled. Checked
    here as "the nonce reaches `style-src`", independently of Issue #181's own header test.
    """
    csp = build_content_security_policy("PROBE")
    style_src = next(
        part.strip() for part in csp.split(";") if part.strip().startswith("style-src")
    )
    assert "'nonce-PROBE'" in style_src, style_src


def test_no_template_still_carries_an_inline_style_attribute() -> None:
    """Re-derived by scanning the templates, not by asserting that Issue #181's guard exists."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in sorted((REPO_ROOT / "src" / "templates").rglob("*.html"))
        if re.search(r"\sstyle\s*=\s*[\"']", path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], offenders
