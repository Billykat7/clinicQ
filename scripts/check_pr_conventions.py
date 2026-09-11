"""Check a pull request against the team's conventions (Issue 13).

The conventions in CONTRIBUTING.md, checked on every pull request by CI's ``changes`` job, so that
they hold when people are busy rather than only when they remember:

* the branch is ``Issue/<N>/<short-slug>`` (a release note's is ``Release/v<X.Y.Z>``);
* every commit on it starts ``Issue <N>: `` (``Release v<X.Y.Z>: ``); merge commits are skipped;
* the description closes the issue: ``Closes #<N>`` (``Fixes`` and ``Resolves`` count too), or
  says ``Refs #<N>`` when it follows up an issue an earlier pull request closed;
* a change to a template, stylesheet or script under ``src/`` shows a screenshot.

A failure here fails the required **CI gate** check, but the tests still run: CI records the
outcome instead of stopping at it.

Usage (in CI; ``GH_TOKEN`` must be able to read the pull request)::

    python scripts/check_pr_conventions.py --repo Billykat7/clinicQ --pr 124 --branch Issue/13/x

The pull request's description, commits and files are read live, so fixing the description and
re-running the job is enough; nothing has to be pushed again.
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum

#: ``Issue/<N>/<slug>``: a positive issue number and a lower-case, hyphenated slug.
ISSUE_BRANCH = re.compile(
    r"^Issue/(?P<number>[1-9]\d*)/(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)$"
)

#: ``Release/v<X.Y.Z>``: the branch that carries a milestone's release note.
RELEASE_BRANCH = re.compile(r"^Release/v(?P<version>\d+\.\d+\.\d+)$")

#: "Short and sweet" (.cursor/rules/issue-branch-commits.mdc): two to four words, five at most.
MAX_SLUG_WORDS = 5

#: Files whose change a reviewer has to see: templates and the app's own CSS and JS.
UI_PATHS = re.compile(r"^src/(templates|static)/")

#: Third-party assets are committed verbatim, never designed here.
VENDORED = re.compile(r"^src/static/vendor/")

#: Markdown or HTML images, including GitHub's uploaded attachments.
SCREENSHOT = re.compile(r"!\[[^\]]*\]\([^)]+\)|<img\s|/user-attachments/")


class BranchKind(StrEnum):
    """The two kinds of branch a pull request into main may come from."""

    ISSUE = "issue"
    RELEASE = "release"


@dataclass(frozen=True)
class Branch:
    """A branch name, understood: its kind and what its commits must start with."""

    kind: BranchKind
    reference: str  # the issue number, or the version

    @property
    def commit_prefix(self) -> str:
        """``Issue 13: `` or ``Release v0.2.0: ``."""
        if self.kind is BranchKind.ISSUE:
            return f"Issue {self.reference}: "
        return f"Release v{self.reference}: "


def parse_branch(name: str) -> tuple[Branch | None, list[str]]:
    """Return the branch and no problems, or ``None`` and why the name is not allowed."""
    if match := RELEASE_BRANCH.match(name):
        return Branch(BranchKind.RELEASE, match["version"]), []
    match = ISSUE_BRANCH.match(name)
    if match is None:
        return None, [
            f"branch {name!r} is not Issue/<N>/<short-slug> (lower case, words joined by "
            "hyphens), or Release/v<X.Y.Z> for a release note"
        ]
    words = len(match["slug"].split("-"))
    if words > MAX_SLUG_WORDS:
        return Branch(BranchKind.ISSUE, match["number"]), [
            f"branch slug {match['slug']!r} has {words} words; keep it to {MAX_SLUG_WORDS} or fewer"
        ]
    return Branch(BranchKind.ISSUE, match["number"]), []


def check_commits(branch: Branch, subjects: list[str]) -> list[str]:
    """Every commit subject starts with the branch's prefix, followed by a summary."""
    prefix = branch.commit_prefix
    return [
        f"commit {subject!r} does not start with {prefix!r}"
        for subject in subjects
        if not subject.startswith(prefix) or not subject[len(prefix) :].strip()
    ]


def check_body(branch: Branch, body: str) -> list[str]:
    """An issue branch's description closes its issue, or refers to it as a follow-up."""
    if branch.kind is not BranchKind.ISSUE:
        return []
    closes = re.compile(
        rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?) #{branch.reference}\b",
        re.I,
    )
    if closes.search(body or ""):
        return []
    return [
        f"the description does not say `Closes #{branch.reference}` (or `Refs "
        f"#{branch.reference}` for a follow-up to an issue already closed)"
    ]


def check_screenshots(changed: list[str], body: str) -> list[str]:
    """A change to a template, stylesheet or script shows the reviewer what it looks like."""
    ui = [path for path in changed if UI_PATHS.match(path) and not VENDORED.match(path)]
    if not ui or SCREENSHOT.search(body or ""):
        return []
    shown = ", ".join(ui[:3]) + (" …" if len(ui) > 3 else "")
    return [f"UI files changed ({shown}) but the description has no screenshot"]


def check(
    branch_name: str, subjects: list[str], body: str, changed: list[str]
) -> list[str]:
    """Every convention problem with one pull request, in one list."""
    branch, problems = parse_branch(branch_name)
    if branch is not None:
        problems += check_commits(branch, subjects)
        problems += check_body(branch, body)
    problems += check_screenshots(changed, body)
    return problems


def _gh_pages(path: str) -> list[dict[str, object]]:
    """Every item of a paginated GitHub API list."""
    output = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", path],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [item for page in json.loads(output) for item in page]


def main(argv: list[str] | None = None) -> int:
    """Fetch the pull request, check it, print each problem as a CI error, exit 1 on any."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--pr", required=True, type=int, help="pull request number")
    parser.add_argument(
        "--branch", required=True, help="the pull request's head branch"
    )
    args = parser.parse_args(argv)

    pull = json.loads(
        subprocess.run(
            ["gh", "api", f"repos/{args.repo}/pulls/{args.pr}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    commits = _gh_pages(f"repos/{args.repo}/pulls/{args.pr}/commits?per_page=100")
    subjects = [
        str(commit["commit"]["message"]).splitlines()[0]  # type: ignore[index]
        for commit in commits
        if len(commit["parents"]) == 1  # type: ignore[arg-type]
    ]
    changed = [
        str(entry["filename"])
        for entry in _gh_pages(f"repos/{args.repo}/pulls/{args.pr}/files?per_page=100")
    ]
    problems = check(args.branch, subjects, str(pull.get("body") or ""), changed)
    for problem in problems:
        print(f"::error title=Convention::{problem}")
    print(
        f"{len(subjects)} commit(s), {len(changed)} file(s): "
        + ("follows the conventions" if not problems else f"{len(problems)} problem(s)")
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
