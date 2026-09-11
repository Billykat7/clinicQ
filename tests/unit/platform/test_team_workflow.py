"""The team's rules of the road, as the files GitHub reads (Issue 13).

CODEOWNERS, the pull-request and issue templates, the labels and the rulesets are each one file a
later PR can quietly break: a path left without an owner, an owner nobody can find, a template that
stops asking for the closing line, a ruleset that starts letting someone past the CI gate. These
tests are that failure. Offline: files are parsed, nothing is sent to GitHub.
"""

import json
import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
GITHUB_DIR = REPO_ROOT / ".github"
CODEOWNERS = GITHUB_DIR / "CODEOWNERS"
WORKLOAD_SPLIT = REPO_ROOT / "docs" / "TEAM" / "WORKLOAD_SPLIT.md"
LABELS = REPO_ROOT / "docs" / "GITHUB" / "LABELS" / "labels.yml"
PR_TEMPLATE = GITHUB_DIR / "pull_request_template.md"
DOCUMENTED_PR_TEMPLATE = REPO_ROOT / "docs" / "GITHUB" / "PR" / "PR_TEMPLATE.md"
ISSUE_FORMS = GITHUB_DIR / "ISSUE_TEMPLATE"
RULESETS = GITHUB_DIR / "rulesets"
CI_WORKFLOW = GITHUB_DIR / "workflows" / "ci.yml"

#: Folders that must each have an owner of their own, not only the catch-all.
OWNED_ROOTS = ("/.github/", "/infra/", "/scripts/", "/tests/", "/alembic/")

#: The issue forms the template chooser offers.
ISSUE_FORM_NAMES = {"feature.yml", "bug.yml", "spike.yml"}

#: What the pull-request template must ask for (the issue's scope line).
PR_TEMPLATE_SECTIONS = ("## Scope", "## Testing", "## Screenshots", "Closes #")

#: GitHub's built-in Admin repository role, and the GitHub Actions app.
ADMIN_ROLE_ID = 5
GITHUB_ACTIONS_APP_ID = 15368


def _rules() -> list[tuple[str, list[str]]]:
    """(pattern, owners) for every CODEOWNERS rule, in file order."""
    rules = []
    for line in CODEOWNERS.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            pattern, *owners = line.split()
            rules.append((pattern, owners))
    return rules


def _team_handles() -> set[str]:
    """The GitHub handles filled in WORKLOAD_SPLIT §1's role table."""
    handles = set()
    for line in WORKLOAD_SPLIT.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 4 and re.fullmatch(r"[A-F]", cells[0]) and cells[2]:
            handles.add(cells[2].lstrip("@"))
    return handles


def _ruleset(name: str) -> dict[str, Any]:
    """One ruleset file, parsed."""
    return json.loads((RULESETS / name).read_text(encoding="utf-8"))


def _rule(ruleset: dict[str, Any], rule_type: str) -> dict[str, Any]:
    """The parameters of one rule of a ruleset (empty for a rule that has none)."""
    (rule,) = [rule for rule in ruleset["rules"] if rule["type"] == rule_type]
    return rule.get("parameters", {})


def test_every_codeowners_path_has_exactly_one_owner_from_the_team_table() -> None:
    """One owner per path, and each is a handle §1 names, so a review always reaches someone."""
    handles = _team_handles()
    assert handles, "WORKLOAD_SPLIT §1 names no GitHub handle"
    for pattern, owners in _rules():
        assert len(owners) == 1, f"{pattern}: {owners}"
        assert owners[0].startswith("@"), f"{pattern}: {owners[0]}"
        assert owners[0].lstrip("@") in handles, f"{pattern}: {owners[0]} is not in §1"


def test_every_package_and_top_level_folder_has_its_own_owner() -> None:
    """A new package would otherwise fall to the catch-all silently."""
    patterns = {pattern for pattern, _ in _rules()}
    assert _rules()[0][0] == "*", "the catch-all must come first: the last match wins"
    packages = {
        f"/src/{path.name}/"
        for path in (REPO_ROOT / "src").iterdir()
        if path.is_dir() and (path / "__init__.py").exists()
    }
    missing = sorted((packages | set(OWNED_ROOTS)) - patterns)
    assert not missing, f"no CODEOWNERS rule of its own: {missing}"


def test_the_pull_request_template_github_shows_is_the_documented_one() -> None:
    """One template in two places: what GitHub pre-fills and what CONTRIBUTING.md points to."""
    github = PR_TEMPLATE.read_text(encoding="utf-8")
    assert github == DOCUMENTED_PR_TEMPLATE.read_text(encoding="utf-8")
    for section in PR_TEMPLATE_SECTIONS:
        assert section in github, section


def test_new_issues_open_from_a_form_whose_labels_exist() -> None:
    """No blank issues; each form's labels are ones labels.yml defines (and so GitHub has)."""
    config = yaml.safe_load((ISSUE_FORMS / "config.yml").read_text(encoding="utf-8"))
    assert config["blank_issues_enabled"] is False
    forms = {path.name for path in ISSUE_FORMS.glob("*.yml")} - {"config.yml"}
    assert forms == ISSUE_FORM_NAMES
    defined = {label["name"] for label in yaml.safe_load(LABELS.read_text("utf-8"))}
    for name in forms:
        form = yaml.safe_load((ISSUE_FORMS / name).read_text(encoding="utf-8"))
        assert set(form["labels"]) <= defined, f"{name}: {form['labels']}"
        assert any(
            field.get("validations", {}).get("required") for field in form["body"]
        ), f"{name} requires nothing"


def test_labels_yml_is_a_valid_single_source() -> None:
    """Unique names and six-digit colours, as gh_sync_labels.py sends them."""
    labels = yaml.safe_load(LABELS.read_text(encoding="utf-8"))
    names = [label["name"] for label in labels]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"[0-9a-fA-F]{6}", label["color"]) for label in labels)


def test_nobody_bypasses_the_ci_gate_or_pushes_to_main_directly() -> None:
    """The first ruleset holds for administrators too, and requires the check CI really reports."""
    gate = _ruleset("main-ci-gate.json")
    assert gate["bypass_actors"] == []
    assert gate["enforcement"] == "active"
    assert gate["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    _rule(gate, "deletion")
    _rule(gate, "non_fast_forward")
    _rule(gate, "pull_request")
    (check,) = _rule(gate, "required_status_checks")["required_status_checks"]
    ci_gate_job = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"][
        "gate"
    ]
    assert check == {
        "context": ci_gate_job["name"],
        "integration_id": GITHUB_ACTIONS_APP_ID,
    }


def test_merging_needs_one_code_owner_approval_unless_an_admin_merges() -> None:
    """The review rule: one approval from the code owner, and a bypass that never allows a push."""
    review = _ruleset("main-review.json")
    parameters = _rule(review, "pull_request")
    assert parameters["required_approving_review_count"] == 1
    assert parameters["require_code_owner_review"] is True
    assert parameters["dismiss_stale_reviews_on_push"] is True
    assert review["bypass_actors"] == [
        {
            "actor_id": ADMIN_ROLE_ID,
            "actor_type": "RepositoryRole",
            "bypass_mode": "pull_request",
        }
    ]


def test_the_ruleset_sync_ignores_github_defaults_but_not_a_changed_rule() -> None:
    """`make gh-sync-rulesets` must say "unchanged" after a sync, and notice a real change."""
    from scripts.gh_sync_rulesets import differences

    desired = _ruleset("main-review.json")
    as_github_returns = json.loads(json.dumps(desired))
    as_github_returns |= {"id": 22946886, "source": "Billykat7/clinicQ"}
    as_github_returns["rules"][0]["parameters"] |= {
        "required_reviewers": [],
        "require_extra_approval_for_unattributed_changes": True,
    }
    assert differences(desired, as_github_returns) == []

    as_github_returns["rules"][0]["parameters"]["required_approving_review_count"] = 0
    assert differences(desired, as_github_returns) == ["rules"]
