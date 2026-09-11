#!/usr/bin/env python3
"""Apply the deployment environments in .github/environments/*.json to the repository (Issue 11).

Each file names an environment, the GitHub logins that must approve a deploy into it, and the
branches whose workflows may deploy into it. The environment is created or updated in place (its
secrets and variables are never touched), and its branch policies are made to match the file.

`gh` must be installed and authenticated with admin rights on the repository.

Usage:
    scripts/gh_sync_environments.py              # apply every file
    scripts/gh_sync_environments.py --dry-run    # say what would change, change nothing
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
ENVIRONMENTS_DIR = REPO_ROOT / ".github" / "environments"


def gh_api(*args: str, body: dict[str, Any] | None = None) -> Any:
    """Call `gh api`, sending ``body`` as JSON when given; return the parsed response or None."""
    command = ["gh", "api", *args] + (["--input", "-"] if body is not None else [])
    result = subprocess.run(
        command,
        input=json.dumps(body) if body is not None else None,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"gh api {' '.join(args)} failed:\n{result.stderr}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def desired_state(spec: dict[str, Any]) -> tuple[list[int], list[str]]:
    """The reviewer user ids and branch names a file asks for."""
    reviewers = [gh_api(f"users/{login}")["id"] for login in spec["reviewers"]]
    return reviewers, sorted(spec["deploy_from_branches"])


def current_state(repo: str, name: str) -> tuple[list[int], list[str]] | None:
    """The reviewer ids and branch policies GitHub has, or None if the environment is missing."""
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/environments/{name}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    environment = json.loads(result.stdout)
    reviewers = [
        reviewer["reviewer"]["id"]
        for rule in environment.get("protection_rules", [])
        if rule["type"] == "required_reviewers"
        for reviewer in rule.get("reviewers", [])
    ]
    policy = environment.get("deployment_branch_policy") or {}
    if not policy.get("custom_branch_policies"):
        # No custom policy: every branch may deploy, and the policies endpoint answers 404.
        return sorted(reviewers), []
    policies = gh_api(f"repos/{repo}/environments/{name}/deployment-branch-policies")
    branches = sorted(policy["name"] for policy in policies["branch_policies"])
    return sorted(reviewers), branches


def main() -> int:
    """Create or update each environment file's environment."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="change nothing")
    args = parser.parse_args()
    repo = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    for path in sorted(ENVIRONMENTS_DIR.glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        name = spec["name"]
        reviewers, branches = desired_state(spec)
        current = current_state(repo, name)
        if current == (sorted(reviewers), branches):
            print(f"unchanged     {name}")
            continue
        print(
            f"{'would apply' if args.dry_run else 'apply'}   {name}: reviewers "
            f"{spec['reviewers'] or 'none'}, deploys from {branches} (was {current})"
        )
        if args.dry_run:
            continue
        gh_api(
            "-X",
            "PUT",
            f"repos/{repo}/environments/{name}",
            body={
                "reviewers": [{"type": "User", "id": user_id} for user_id in reviewers],
                "prevent_self_review": False,
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                },
            },
        )
        existing = gh_api(
            f"repos/{repo}/environments/{name}/deployment-branch-policies"
        )
        for policy in existing["branch_policies"]:
            if policy["name"] not in branches:
                gh_api(
                    "-X",
                    "DELETE",
                    f"repos/{repo}/environments/{name}/deployment-branch-policies/{policy['id']}",
                )
        have = {policy["name"] for policy in existing["branch_policies"]}
        for branch in branches:
            if branch not in have:
                gh_api(
                    "-X",
                    "POST",
                    f"repos/{repo}/environments/{name}/deployment-branch-policies",
                    body={"name": branch, "type": "branch"},
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
