"""Post a deploy's result to the team channel (Issues 11 and 14).

One message per deploy, naming the environment, the version and commit, the result, the link to
that version's release notes and the link to the run, so nobody has to open Actions to learn what
is running where. The channel is a Slack or Discord incoming webhook, whose URL is the secret
``TEAM_WEBHOOK_URL`` of the GitHub Environment being deployed; without it, the message goes to
the run's log only and the deploy does not fail.

Usage (in .github/workflows/deploy.yml)::

    python scripts/cd/notify_deploy.py --environment staging --version 0.2.0 --sha 0e8d517… \\
        --result success --run-url https://github.com/…/actions/runs/1

Only the standard library: it runs on the runner before any dependency is installed.
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from enum import StrEnum

REPOSITORY_URL = "https://github.com/Billykat7/clinicQ"

#: A release version: 0.2.0, or a pre-release such as 0.0.2-test.
VERSION = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?P<pre>-[\w.]+)?$"
)

#: Seconds to wait for the channel; a slow chat service must not hold up a deploy.
TIMEOUT_SECONDS = 10


class Result(StrEnum):
    """How a deploy ended, as the workflow reports it."""

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"  # the environment is not provisioned yet


class WebhookKind(StrEnum):
    """The chat services whose incoming webhooks this can post to."""

    SLACK = "slack"
    DISCORD = "discord"


#: What each result looks like at the start of the message.
HEADLINE = {
    Result.SUCCESS: "✅ deployed",
    Result.FAILURE: "❌ deploy FAILED (the previous version keeps serving)",
    Result.CANCELLED: "⏹ deploy cancelled",
    Result.SKIPPED: "⏸ not deployed: this environment is not provisioned yet",
}


def release_notes_url(version: str) -> str:
    """The release note for a version (docs/GITHUB/RELEASES), or its tag for a pre-release."""
    match = VERSION.match(version)
    if match is None or match["pre"]:
        return f"{REPOSITORY_URL}/tree/v{version}"
    name = f"RELEASE_v{match['major']}_{match['minor']}_{match['patch']}.md"
    return f"{REPOSITORY_URL}/blob/v{version}/docs/GITHUB/RELEASES/{name}"


def message(
    environment: str, version: str, sha: str, result: Result, run_url: str
) -> str:
    """The one message the team channel gets for a deploy."""
    commit = f" ({sha[:7]})" if sha else ""
    return (
        f"{HEADLINE[result]}: ClinicQ {version}{commit} → {environment}\n"
        f"Release notes: {release_notes_url(version)}\n"
        f"Run: {run_url}"
    )


def payload(kind: WebhookKind, text: str) -> dict[str, str]:
    """The JSON body each service expects."""
    return {"content": text} if kind is WebhookKind.DISCORD else {"text": text}


def main(argv: list[str] | None = None) -> int:
    """Print the message, and post it when the webhook secret is configured."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--environment", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sha", default="")
    parser.add_argument("--result", required=True, type=Result)
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args(argv)

    text = message(args.environment, args.version, args.sha, args.result, args.run_url)
    print(text)
    url = os.environ.get("TEAM_WEBHOOK_URL", "").strip()
    if not url:
        print(
            "::notice::TEAM_WEBHOOK_URL is not set for this environment: posted nowhere"
        )
        return 0
    kind = WebhookKind(
        os.environ.get("TEAM_WEBHOOK_KIND", WebhookKind.SLACK).strip().lower()
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(payload(kind, text)).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # A chat outage is reported, never a reason to fail the deploy.
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            print(f"posted to the team channel ({kind}): HTTP {response.status}")
    except OSError as exc:
        print(f"::warning::could not post to the team channel: {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
