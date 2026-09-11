"""The pull-request conventions CI enforces (Issue 13), as rules rather than examples in a doc.

Essential logic: these decide whether a pull request can merge, so each rule is pinned by what it
accepts and what it refuses, using this repository's real branch names.
"""

import pytest

from scripts.check_pr_conventions import (
    Branch,
    BranchKind,
    check,
    check_body,
    check_commits,
    check_screenshots,
    parse_branch,
)

ISSUE_13 = Branch(BranchKind.ISSUE, "13")


@pytest.mark.parametrize(
    "name",
    [
        "Issue/13/team-workflow-templates-codeowners",
        "Issue/9/actions-ci",
        "Issue/7/bump-httpx2-httpcore2",
        "Release/v0.2.0",
    ],
)
def test_the_branch_names_the_team_uses_are_accepted(name: str) -> None:
    """Every branch this repository has merged, and the release-note branch."""
    branch, problems = parse_branch(name)
    assert branch is not None and problems == []


@pytest.mark.parametrize(
    "name",
    [
        "main",
        "feature/login",
        "issue/13/lower-case-prefix",
        "Issue/13/Upper-Case",
        "Issue/13/under_score",
        "Issue/0/zero",
        "Issue/13",
        "Issue/13/a-slug-with-far-too-many-words",
    ],
)
def test_other_branch_names_are_refused(name: str) -> None:
    """A name that breaks the pattern, or a slug longer than five words, is a problem."""
    _, problems = parse_branch(name)
    assert problems


def test_every_commit_needs_the_issue_prefix_and_a_summary() -> None:
    """``Issue 13: `` then something; another issue's number does not count."""
    subjects = [
        "Issue 13: Add CODEOWNERS",
        "Issue 13:",
        "Issue 12: Wrong issue",
        "add codeowners",
    ]
    problems = check_commits(ISSUE_13, subjects)
    assert len(problems) == 3
    assert "Issue 13: Add CODEOWNERS" not in " ".join(problems)


def test_the_description_must_close_the_branch_issue() -> None:
    """``Closes #13`` (or Fixes, Resolves), and not ``#130`` or another issue."""
    assert check_body(ISSUE_13, "…\n\nCloses #13\n") == []
    assert check_body(ISSUE_13, "fixes #13") == []
    # A follow-up PR to an issue an earlier PR closed (PRs #118, #119) refers to it instead.
    assert check_body(ISSUE_13, "Refs #13 (already closed by #124)") == []
    assert check_body(ISSUE_13, "Closes #130")
    assert check_body(ISSUE_13, "Closes #12")
    assert check_body(ISSUE_13, "")


def test_a_release_branch_closes_nothing_and_uses_its_own_prefix() -> None:
    """A release note closes no issue; its commits start ``Release v0.2.0: ``."""
    branch, _ = parse_branch("Release/v0.2.0")
    assert branch is not None
    assert check_body(branch, "") == []
    assert check_commits(branch, ["Release v0.2.0: Add the M2 release note"]) == []
    assert check_commits(branch, ["Issue 14: Add the M2 release note"])


def test_a_ui_change_needs_a_screenshot_and_other_changes_do_not() -> None:
    """Templates and the app's CSS/JS need one; vendored files and Python never do."""
    ui = ["src/templates/home.html", "src/static/css/admin.css"]
    assert check_screenshots(ui, "no picture here")
    assert (
        check_screenshots(ui, "![board](https://github.com/user-attachments/assets/1)")
        == []
    )
    assert check_screenshots(ui, '<img src="board.png">') == []
    assert check_screenshots(["src/static/vendor/htmx-2.0.0.min.js"], "") == []
    assert check_screenshots(["src/core/config.py", "docs/CICD/PIPELINES.md"], "") == []


def test_every_problem_is_reported_together() -> None:
    """A pull request learns everything wrong with it from one run."""
    problems = check(
        "Issue/13/team-workflow",
        ["Issue 13: Good", "wip"],
        "no closing line",
        ["src/templates/home.html"],
    )
    assert len(problems) == 3
