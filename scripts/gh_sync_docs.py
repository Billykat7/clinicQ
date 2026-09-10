#!/usr/bin/env python3
"""Sync docs/GITHUB milestones + issues to GitHub (create and update).

Milestones
  docs/GITHUB/MILESTONES/M<n>_*.md  ->  GitHub milestone "Milestone <n> — …"
  Creates missing milestones; updates title + description from the doc.

Issues
  docs/GITHUB/ISSUES/M<n>/ISSUE_<k>_*.md  ->  GitHub issue titled "Issue <k> — …"
  Matches by leading ``Issue <k>`` in the title (spec ID), not exact title.
  Updates title, body, and milestone on the canonical issue; closes duplicate
  titles for the same spec ID; creates when none exist.

Usage:
    scripts/gh_sync_docs.py
    scripts/gh_sync_docs.py --dry-run
    scripts/gh_sync_docs.py --milestones-only
    scripts/gh_sync_docs.py --issues-only
    scripts/gh_sync_docs.py --milestone M5
"""

from __future__ import annotations

import argparse
import functools
import json
import posixpath
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MILESTONES_DIR = REPO_ROOT / "docs" / "GITHUB" / "MILESTONES"
ISSUES_DIR = REPO_ROOT / "docs" / "GITHUB" / "ISSUES"

ISSUE_NUM_RE = re.compile(r"ISSUE_(\d+)_", re.IGNORECASE)
MILESTONE_FILE_RE = re.compile(r"^M(\d+)_", re.IGNORECASE)
ISSUE_TITLE_NUM_RE = re.compile(r"^Issue\s+(\d+)\b", re.IGNORECASE)
MILESTONE_TITLE_NUM_RE = re.compile(r"Milestone\s+(\d+)\b", re.IGNORECASE)

# The branch that relative doc links are rewritten to point at. ``main`` because an issue outlives
# any feature branch: a link to ``Issue/<N>/…`` would rot the day that branch is merged and
# deleted. ``--ref`` overrides it, e.g. to preview links before the docs have reached ``main``.
DOCS_REF = "main"

# A markdown link target: ``](target)``. Only relative targets are rewritten (see below).
MD_LINK_TARGET_RE = re.compile(r"\]\(([^)\s]+)\)")


def run(cmd: list[str], *, input_text: str | None = None) -> str:
    """Run a command; raise SystemExit on failure."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=input_text,
    )
    if result.returncode != 0:
        sys.stderr.write(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr}\n"
        )
        raise SystemExit(result.returncode)
    return result.stdout.strip()


def ensure_gh() -> None:
    """Require authenticated gh CLI."""
    try:
        run(["gh", "auth", "status"])
    except FileNotFoundError:
        # `from None`: the traceback adds nothing to a one-line CLI prerequisite message.
        raise SystemExit(
            "`gh` CLI not found. Install it: https://cli.github.com"
        ) from None


def parse_heading_doc(path: Path) -> tuple[str, str]:
    """Return (title, body) from first ``# `` heading."""
    lines = path.read_text(encoding="utf-8").splitlines()
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            title = line[2:].strip()
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip()
    return title, body


def milestone_body_to_description(body: str, *, max_len: int = 65000) -> str:
    """Use milestone markdown as GitHub description (truncate if needed)."""
    text = body.strip()
    if len(text) > max_len:
        text = text[: max_len - 20] + "\n\n…(truncated)"
    return text


@functools.cache
def repo_slug() -> str:
    """``owner/repo`` of the repository ``gh`` is pointed at, looked up once per run."""
    return run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"]
    )


def absolutize_links(body: str, folder: str) -> str:
    """Rewrite every relative markdown link to an absolute GitHub URL on :data:`DOCS_REF`.

    The specs link to each other and to the docs with paths relative to their own folder
    (``../M4/ISSUE_25_….md``, ``../../../guideline.md``). That is right in the repository and
    wrong on GitHub, where an issue or milestone page resolves them against its own URL and every
    one of them 404s. Absolute links, external links and bare ``#anchor`` links are left alone.
    """

    def rewrite(m: re.Match[str]) -> str:
        target = m.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            return m.group(0)
        path, _, anchor = target.partition("#")
        resolved = posixpath.normpath(posixpath.join(folder, path))
        kind = "tree" if path.endswith("/") else "blob"
        url = f"https://github.com/{repo_slug()}/{kind}/{DOCS_REF}/{resolved}"
        return f"]({url}#{anchor})" if anchor else f"]({url})"

    return MD_LINK_TARGET_RE.sub(rewrite, body)


def issue_body_from_doc(body: str, folder: str) -> str:
    """Turn a local issue spec's markdown into the body GitHub should carry.

    Three transformations:

    * **Strip the trailing ``Closes #N``.** It is right in the spec file, which is also the PR
      description template, and pointless in the issue itself, where it is a link to itself.
    * **Make links absolute** (:func:`absolutize_links`), so the *Depends on*, *Unblocks* and
      reference links work on the issue page.
    * **Prepend a source-of-truth note**, because the next sync overwrites the body: a change
      made on GitHub is lost unless it is made in the spec file.

    Issues are created in spec order, so on this repository GitHub issue ``#N`` is spec
    ``Issue N``. (An earlier version of this note, carried over from the project the kernel came
    from, said the opposite and pointed at a ``PROMPTS.md`` mapping file that does not exist here.)
    """
    lines = body.strip().splitlines()
    while lines and (not lines[-1].strip() or lines[-1].strip().startswith("Closes #")):
        lines.pop()
    note = (
        f"> **Source of truth:** this issue is generated from its spec in `{folder}` by "
        "`scripts/gh_sync_docs.py`. Edit the spec and re-sync rather than editing this issue: the "
        "next sync overwrites the body."
    )
    return note + "\n\n" + absolutize_links("\n".join(lines).strip(), folder) + "\n"


def fetch_gh_milestones() -> dict[int, dict]:
    """Map milestone number (from title) -> API row including number (id)."""
    raw = run(
        [
            "gh",
            "api",
            "repos/:owner/:repo/milestones?state=all&per_page=100",
            "--paginate",
        ]
    )
    out: dict[int, dict] = {}
    for ms in json.loads(raw or "[]"):
        m = MILESTONE_TITLE_NUM_RE.search(ms["title"])
        if m:
            out[int(m.group(1))] = ms
    return out


def collect_milestone_docs(only: str | None) -> list[tuple[int, Path]]:
    """Return sorted (milestone_num, path)."""
    items: list[tuple[int, Path]] = []
    for path in sorted(MILESTONES_DIR.glob("M*_*.md")):
        m = MILESTONE_FILE_RE.match(path.name)
        if not m:
            continue
        num = int(m.group(1))
        if only and f"M{num}".lower() != only.lower():
            continue
        items.append((num, path))
    # By number, not filename: ``sorted()`` on the paths puts M10–M14 before M1, and GitHub
    # numbers milestones in creation order, permanently.
    items.sort(key=lambda item: item[0])
    return items


def sync_milestones(*, dry_run: bool, only: str | None) -> tuple[int, int]:
    """Create/update GitHub milestones from docs. Returns (created, updated)."""
    existing = fetch_gh_milestones()
    created = updated = 0
    for num, path in collect_milestone_docs(only):
        title, body = parse_heading_doc(path)
        if not title:
            sys.stderr.write(f"! no title in {path.name}\n")
            continue
        desc = milestone_body_to_description(
            absolutize_links(body, "docs/GITHUB/MILESTONES/")
        )
        row = existing.get(num)
        payload = json.dumps({"title": title, "description": desc, "state": "open"})
        if row is None:
            if dry_run:
                print(f"+ would create milestone: {title}")
            else:
                run(
                    ["gh", "api", "repos/:owner/:repo/milestones", "--input", "-"],
                    input_text=payload,
                )
                print(f"+ created milestone: {title}")
            created += 1
            continue
        needs = row["title"] != title or (row.get("description") or "") != desc
        if not needs:
            print(f"= milestone ok: {title}")
            continue
        if dry_run:
            print(f"~ would update milestone #{row['number']}: {title}")
        else:
            run(
                [
                    "gh",
                    "api",
                    "-X",
                    "PATCH",
                    f"repos/:owner/:repo/milestones/{row['number']}",
                    "--input",
                    "-",
                ],
                input_text=json.dumps({"title": title, "description": desc}),
            )
            print(f"~ updated milestone #{row['number']}: {title}")
        updated += 1
    return created, updated


def fetch_all_issues() -> list[dict]:
    """All issues with number, title, milestone, state."""
    raw = run(
        [
            "gh",
            "issue",
            "list",
            "--state",
            "all",
            "--limit",
            "1000",
            "--json",
            "number,title,state,milestone,url",
        ]
    )
    return json.loads(raw or "[]")


def issues_by_spec_id(issues: list[dict]) -> dict[int, list[dict]]:
    """Group GitHub issues by leading Issue N spec id."""
    by: dict[int, list[dict]] = {}
    for issue in issues:
        m = ISSUE_TITLE_NUM_RE.match(issue["title"] or "")
        if not m:
            continue
        by.setdefault(int(m.group(1)), []).append(issue)
    for lst in by.values():
        lst.sort(key=lambda i: i["number"])
    return by


def collect_issue_specs(only: str | None) -> list[tuple[int, int, Path]]:
    """Return (milestone_num, issue_num, path) sorted by issue_num."""
    specs: list[tuple[int, int, Path]] = []
    for folder in sorted(ISSUES_DIR.iterdir()):
        if not folder.is_dir():
            continue
        m = re.fullmatch(r"M(\d+)", folder.name, re.IGNORECASE)
        if not m:
            continue
        ms_num = int(m.group(1))
        if only and folder.name.lower() != only.lower():
            continue
        for path in folder.glob("ISSUE_*.md"):
            im = ISSUE_NUM_RE.search(path.name)
            issue_num = int(im.group(1)) if im else 0
            specs.append((ms_num, issue_num, path))
    specs.sort(key=lambda t: t[1])
    return specs


def pick_canonical(matches: list[dict], *, ms_title: str) -> tuple[dict, list[dict]]:
    """Prefer open issue on the target milestone, else oldest open, else oldest."""
    open_on_ms = [
        i
        for i in matches
        if i["state"] == "OPEN"
        and i.get("milestone")
        and i["milestone"].get("title") == ms_title
    ]
    open_any = [i for i in matches if i["state"] == "OPEN"]
    if open_on_ms:
        canon = open_on_ms[0]
    elif open_any:
        canon = open_any[0]
    else:
        canon = matches[0]
    dupes = [i for i in matches if i["number"] != canon["number"]]
    return canon, dupes


def sync_issues(*, dry_run: bool, only: str | None) -> tuple[int, int, int, int]:
    """Create/update issues. Returns (created, updated, closed_dupes, skipped)."""
    milestones = fetch_gh_milestones()
    by_spec = issues_by_spec_id(fetch_all_issues())

    created = updated = closed = skipped = 0
    for ms_num, issue_num, path in collect_issue_specs(only):
        title, body = parse_heading_doc(path)
        if not title:
            sys.stderr.write(f"! no title in {path.name}\n")
            continue
        # Both the create and the update path below post this body verbatim.
        body = issue_body_from_doc(body, f"docs/GITHUB/ISSUES/{path.parent.name}/")
        ms_row = milestones.get(ms_num)
        if ms_row is None:
            sys.stderr.write(
                f"! milestone {ms_num} missing on GitHub for {path.name}\n"
            )
            skipped += 1
            continue
        ms_title = ms_row["title"]
        ms_gh_number = ms_row["number"]

        matches = by_spec.get(issue_num, [])
        if not matches:
            if dry_run:
                print(f"+ would create: {title}")
            else:
                # create only works with open milestones by name; reopen ms briefly if needed
                was_closed = ms_row.get("state") == "closed"
                if was_closed:
                    run(
                        [
                            "gh",
                            "api",
                            "-X",
                            "PATCH",
                            f"repos/:owner/:repo/milestones/{ms_gh_number}",
                            "--input",
                            "-",
                        ],
                        input_text=json.dumps({"state": "open"}),
                    )
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", suffix=".md", delete=False
                ) as tmp:
                    tmp.write(body)
                    tmp_path = tmp.name
                try:
                    url = run(
                        [
                            "gh",
                            "issue",
                            "create",
                            "--title",
                            title,
                            "--body-file",
                            tmp_path,
                            "--milestone",
                            ms_title,
                        ]
                    )
                finally:
                    Path(tmp_path).unlink(missing_ok=True)
                    if was_closed:
                        run(
                            [
                                "gh",
                                "api",
                                "-X",
                                "PATCH",
                                f"repos/:owner/:repo/milestones/{ms_gh_number}",
                                "--input",
                                "-",
                            ],
                            input_text=json.dumps({"state": "closed"}),
                        )
                print(f"+ created {url}  {title}")
            created += 1
            continue

        canon, dupes = pick_canonical(matches, ms_title=ms_title)
        if dry_run:
            print(
                f"~ would update #{canon['number']} -> {title} "
                f"(milestone={ms_title}, dupes={len(dupes)}, "
                f"keep_state={canon['state']})"
            )
            updated += 1
        else:
            # PATCH by milestone *number* so closed milestones (M1/M2) still work.
            # Do not reopen completed issues.
            payload = {
                "title": title,
                "body": body,
                "milestone": ms_gh_number,
            }
            run(
                [
                    "gh",
                    "api",
                    "-X",
                    "PATCH",
                    f"repos/:owner/:repo/issues/{canon['number']}",
                    "--input",
                    "-",
                ],
                input_text=json.dumps(payload),
            )
            print(f"~ updated #{canon['number']}  {title}  [{canon['state']}]")
            updated += 1

        for dupe in dupes:
            if dupe["state"] == "CLOSED":
                continue
            comment = (
                f"Superseded by #{canon['number']} — same spec "
                f"**Issue {issue_num}** after docs sync (title/body updated on "
                f"the canonical issue)."
            )
            if dry_run:
                print(f"  x would close duplicate #{dupe['number']} ({dupe['title']})")
            else:
                run(
                    [
                        "gh",
                        "issue",
                        "comment",
                        str(dupe["number"]),
                        "--body",
                        comment,
                    ]
                )
                run(
                    [
                        "gh",
                        "issue",
                        "close",
                        str(dupe["number"]),
                        "--reason",
                        "not planned",
                    ]
                )
                print(f"  x closed duplicate #{dupe['number']}")
            closed += 1

    return created, updated, closed, skipped


def main() -> int:
    """CLI entrypoint."""
    global DOCS_REF
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--milestones-only", action="store_true")
    parser.add_argument("--issues-only", action="store_true")
    parser.add_argument(
        "--milestone",
        help="limit to one folder/number, e.g. M5",
    )
    parser.add_argument(
        "--ref",
        default=DOCS_REF,
        help=f"branch that doc links point at (default: {DOCS_REF})",
    )
    args = parser.parse_args()
    DOCS_REF = args.ref
    ensure_gh()

    if not args.issues_only:
        if not MILESTONES_DIR.is_dir():
            raise SystemExit(f"missing {MILESTONES_DIR}")
        c, u = sync_milestones(dry_run=args.dry_run, only=args.milestone)
        print(f"\nMilestones: created/would={c} updated/would={u}")

    if not args.milestones_only:
        if not ISSUES_DIR.is_dir():
            raise SystemExit(f"missing {ISSUES_DIR}")
        c, u, x, s = sync_issues(dry_run=args.dry_run, only=args.milestone)
        print(
            f"\nIssues: created/would={c} updated/would={u} "
            f"closed-dupes/would={x} skipped={s}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
