#!/usr/bin/env python3
"""Create GitHub issues from the markdown specs in docs/GITHUB/ISSUES/M*.

Each file `docs/GITHUB/ISSUES/M<n>/ISSUE_<k>_<slug>.md` becomes one GitHub issue:

  * Title      -> the first `# ` heading in the file.
  * Body       -> everything after that heading line.
  * Milestone  -> GitHub milestone whose title starts with "Milestone <n>",
                  derived from the `M<n>` folder name.

The script is idempotent: an issue is only created if no existing issue (any
state) already has the same title, so it is safe to re-run after adding new
specs. It talks to GitHub through the `gh` CLI, which must be installed and
authenticated (`gh auth status`).

Usage:
    scripts/gh_sync_issues.py                 # create all missing issues
    scripts/gh_sync_issues.py --milestone M2  # only M2 specs
    scripts/gh_sync_issues.py --dry-run       # show what would happen
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ISSUES_DIR = REPO_ROOT / "docs" / "GITHUB" / "ISSUES"

ISSUE_NUM_RE = re.compile(r"ISSUE_(\d+)_", re.IGNORECASE)
MILESTONE_NUM_RE = re.compile(r"Milestone\s+(\d+)", re.IGNORECASE)


def run(cmd: list[str], *, capture: bool = True) -> str:
    """Run a command, raising with a helpful message on failure."""
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr}\n"
        )
        raise SystemExit(result.returncode)
    return result.stdout.strip() if capture else ""


def ensure_gh() -> None:
    try:
        run(["gh", "auth", "status"])
    except FileNotFoundError:
        # `from None`: the traceback adds nothing to a one-line CLI prerequisite message.
        raise SystemExit(
            "`gh` CLI not found. Install it: https://cli.github.com"
        ) from None


def fetch_milestones() -> dict[int, str]:
    """Return {milestone_number_from_title: milestone_title}."""
    raw = run(["gh", "api", "repos/:owner/:repo/milestones?state=all", "--paginate"])
    milestones: dict[int, str] = {}
    for ms in json.loads(raw):
        m = MILESTONE_NUM_RE.search(ms["title"])
        if m:
            milestones[int(m.group(1))] = ms["title"]
    return milestones


def fetch_existing_titles() -> set[str]:
    raw = run(
        ["gh", "issue", "list", "--state", "all", "--limit", "1000", "--json", "title"]
    )
    return {row["title"] for row in json.loads(raw)}


def parse_spec(path: Path) -> tuple[str, str]:
    """Return (title, body) from an issue markdown file, ready to post to GitHub.

    The body is transformed by :func:`gh_sync_docs.issue_body_from_doc` — the trailing local
    ``Closes #N`` is stripped and the numbering note prepended — because this repo's local issue
    sequence is not GitHub's, and a raw post would cross-reference unrelated items. The transform
    is shared with ``gh_sync_docs.py`` rather than duplicated so the two entrypoints can never
    drift into producing different bodies for the same spec.
    """
    from gh_sync_docs import issue_body_from_doc

    lines = path.read_text(encoding="utf-8").splitlines()
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            title = line[2:].strip()
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip()
    return title, issue_body_from_doc(body, f"docs/GITHUB/ISSUES/{path.parent.name}/")


def milestone_from_folder(folder: str) -> int | None:
    m = re.fullmatch(r"M(\d+)", folder, re.IGNORECASE)
    return int(m.group(1)) if m else None


def collect_specs(only: str | None) -> list[tuple[int, int, Path]]:
    """Return sorted (milestone_num, issue_num, path) for spec files."""
    specs: list[tuple[int, int, Path]] = []
    for folder in sorted(ISSUES_DIR.iterdir()):
        if not folder.is_dir():
            continue
        ms_num = milestone_from_folder(folder.name)
        if ms_num is None:
            continue
        if only and folder.name.lower() != only.lower():
            continue
        for path in folder.glob("ISSUE_*.md"):
            m = ISSUE_NUM_RE.search(path.name)
            issue_num = int(m.group(1)) if m else 0
            specs.append((ms_num, issue_num, path))
    specs.sort(key=lambda t: t[1])
    return specs


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--milestone", help="only sync one folder, e.g. M2")
    parser.add_argument(
        "--dry-run", action="store_true", help="print actions without creating issues"
    )
    args = parser.parse_args()

    ensure_gh()

    if not ISSUES_DIR.is_dir():
        raise SystemExit(f"issues dir not found: {ISSUES_DIR}")

    milestones = fetch_milestones()
    existing = fetch_existing_titles()
    specs = collect_specs(args.milestone)

    created = skipped = missing_ms = 0
    for ms_num, issue_num, path in specs:
        title, body = parse_spec(path)
        if not title:
            sys.stderr.write(f"! no '# ' title in {path.name}, skipping\n")
            continue
        if title in existing:
            print(f"= exists   #{issue_num:>2} {title}")
            skipped += 1
            continue
        ms_title = milestones.get(ms_num)
        if ms_title is None:
            sys.stderr.write(
                f"! milestone {ms_num} not found on GitHub for {path.name}\n"
            )
            missing_ms += 1
            continue

        if args.dry_run:
            print(f"+ would create (M{ms_num} -> '{ms_title}') {title}")
            created += 1
            continue

        url = run(
            [
                "gh",
                "issue",
                "create",
                "--title",
                title,
                "--body",
                body,
                "--milestone",
                ms_title,
            ]
        )
        print(f"+ created  {url}  {title}")
        existing.add(title)
        created += 1

    print(
        f"\nDone. created/would-create={created} "
        f"skipped(existing)={skipped} missing-milestone={missing_ms}"
    )
    return 1 if missing_ms else 0


if __name__ == "__main__":
    raise SystemExit(main())
