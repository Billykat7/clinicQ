#!/usr/bin/env python3
"""Write each milestone's progress into the docs, from GitHub's own issue states.

A milestone's completion is a fact about GitHub, not about a document, so this reads it from there
(``gh issue list``, which counts issues and never pull requests) and writes it into the three places
a reader looks:

* ``docs/GITHUB/MILESTONES/M<n>_*.md`` — a **Progress** row in the header table;
* ``README.md`` — the *Delivery at a glance* table's **Progress** column;
* ``docs/GITHUB/README.md`` — the *Milestone summary* table's **Status** column, and the roll-up
  line under it.

The bar is ten emoji cells, green for done, so it renders as a green progress bar everywhere
markdown is read — GitHub, an editor, a terminal preview — with no image host involved.

Run it in the pull request that closes an issue (``make milestone-progress``, CONTRIBUTING.md), so
the milestone's state is never staler than the work. ``--check`` fails when the docs disagree with
GitHub, and ``--close-completed`` also closes the GitHub milestone once its last issue is closed.

**A pull request writes the bars as they will be once it merges.** Its own issue is still open
while it is in review, so ``--assume-closed 32`` counts issue 32 as closed. A stacked pull request
names every issue its branch closes, including the ones in the branches below it
(``--assume-closed 32,35,36``), because merging it means they have all merged. Every other number
is still GitHub's. An assumed issue that is in no milestone is an error, so a typo cannot quietly
change nothing. The same flag works with ``--check``.

Usage:
    python scripts/update_milestone_progress.py [--check] [--close-completed]
        [--assume-closed N[,N...]] [--repo owner/name]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MILESTONE_DIR = ROOT / "docs" / "GITHUB" / "MILESTONES"
README = ROOT / "README.md"
#: The milestone index: its *Milestone summary* table has a Status column this script owns.
INDEX = ROOT / "docs" / "GITHUB" / "README.md"

#: Ten cells, because a percentage rounds to tenths cleanly and a wider bar wraps in a table.
BAR_CELLS = 10
FILLED, EMPTY = "🟩", "⬜"

#: The header-table row this script owns, and the *Delivery at a glance* table it appends a column to.
PROGRESS_ROW = re.compile(r"^\| \*\*Progress\*\* \|.*\|$", re.MULTILINE)
STATUS_ROW = re.compile(r"^\| \*\*Status\*\* \|.*\|$", re.MULTILINE)
DELIVERY_HEADER = "| | Milestone | Issues | Sprints | Tag |"
DELIVERY_ROW_START = "| | Milestone | Issues |"
DELIVERY_DIVIDER = "|---|-----------|--------|---------|-----|"

#: The *Milestone summary* table in the index, and how its rows name their milestone (``**[M3: …``).
INDEX_ROW_START = "| Milestone | Focus | Issues |"
INDEX_MILESTONE = re.compile(r"\[M(?P<number>\d+):")
#: The generated roll-up under that table, and the hand-written total it sits below.
INDEX_SUMMARY_PREFIX = "**Progress:**"
INDEX_TOTAL_PREFIX = "**Total:"


@dataclass(frozen=True)
class Milestone:
    """One milestone's issue counts, as GitHub has them."""

    number: int
    title: str
    closed: int
    total: int

    @property
    def percent(self) -> int:
        """Whole-number completion; 0 when a milestone has no issues yet."""
        return round(100 * self.closed / self.total) if self.total else 0

    @property
    def done(self) -> bool:
        """Every issue closed, and there was at least one."""
        return bool(self.total) and self.closed == self.total

    @property
    def bar(self) -> str:
        """``🟩🟩🟩⬜⬜⬜⬜⬜⬜⬜`` — filled cells rounded to the nearest tenth."""
        filled = round(BAR_CELLS * self.closed / self.total) if self.total else 0
        return FILLED * filled + EMPTY * (BAR_CELLS - filled)

    @property
    def cell(self) -> str:
        """What both tables show: the bar, the percentage, and the count behind it."""
        return f"{self.bar} **{self.percent}%** ({self.closed}/{self.total} issues)"

    @property
    def state(self) -> str:
        """``📋 planned`` until an issue closes, ``🚧 in progress``, then ``✅ done``."""
        if self.done:
            return "✅ done"
        return "🚧 in progress" if self.closed else "📋 planned"

    @property
    def status_cell(self) -> str:
        """The index's Status column: the same bar, the percentage, and where it stands."""
        return f"{self.bar} **{self.percent}%** {self.state}"


def _gh(*args: str) -> str:
    """Run ``gh`` and return stdout, failing loudly: a wrong count is worse than no count."""
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=False, cwd=ROOT
    )
    if result.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout


def _repo_path(repo: str | None, suffix: str = "") -> str:
    """``repos/owner/name<suffix>``; ``gh api`` fills the placeholders from the checkout."""
    base = f"repos/{repo}" if repo else "repos/{owner}/{repo}"
    return base + suffix


def count_closed(issues: list[dict[str, object]], assume_closed: frozenset[int]) -> int:
    """Issues closed on GitHub, plus the open ones this pull request will close when it merges."""
    return sum(
        1
        for issue in issues
        if issue["state"] == "CLOSED" or issue["number"] in assume_closed
    )


def parse_issue_numbers(value: str) -> frozenset[int]:
    """``"32,35 36"`` → ``{32, 35, 36}``; ``#`` prefixes are allowed. Anything else is an error."""
    numbers: set[int] = set()
    for part in re.split(r"[,\s]+", value.strip()):
        if not part:
            continue
        digits = part.removeprefix("#")
        if not digits.isdigit():
            raise argparse.ArgumentTypeError(f"not an issue number: {part!r}")
        numbers.add(int(digits))
    return frozenset(numbers)


def read_milestones(
    repo: str | None, assume_closed: frozenset[int] = frozenset()
) -> tuple[list[Milestone], frozenset[int]]:
    """Every milestone with its issue counts, and which assumed issues were found in one.

    Pull requests are not issues and are not counted. An issue in ``assume_closed`` counts as closed
    whatever GitHub says (see the module docstring).
    """
    scope = ["--repo", repo] if repo else []
    listed = json.loads(
        _gh("api", _repo_path(repo, "/milestones?state=all&per_page=100"))
    )
    milestones: list[Milestone] = []
    found: set[int] = set()
    for entry in listed:
        title = str(entry["title"])
        issues = json.loads(
            _gh(
                "issue",
                "list",
                *scope,
                "--milestone",
                title,
                "--state",
                "all",
                "--limit",
                "200",
                "--json",
                "number,state",
            )
        )
        found.update(i["number"] for i in issues if i["number"] in assume_closed)
        closed = count_closed(issues, assume_closed)
        milestones.append(Milestone(int(entry["number"]), title, closed, len(issues)))
    return sorted(milestones, key=lambda m: m.number), frozenset(found)


def milestone_doc(number: int) -> Path | None:
    """``docs/GITHUB/MILESTONES/M3_identity_auth_rbac.md`` for milestone 3, when it exists."""
    matches = sorted(MILESTONE_DIR.glob(f"M{number}_*.md"))
    return matches[0] if matches else None


def apply_to_doc(path: Path, milestone: Milestone) -> str:
    """The document's text with its Progress row written (added under Status if absent)."""
    text = path.read_text(encoding="utf-8")
    row = f"| **Progress** | {milestone.cell} |"
    if PROGRESS_ROW.search(text):
        return PROGRESS_ROW.sub(lambda _: row, text, count=1)
    status = STATUS_ROW.search(text)
    if not status:
        raise SystemExit(f"{path.name} has no **Status** row to put Progress under")
    return text[: status.end()] + "\n" + row + text[status.end() :]


def apply_to_readme(milestones: dict[int, Milestone]) -> str:
    """The README's delivery table with a Progress column, one row per milestone.

    Only that one table is touched: it is found by its header line, and rewriting stops at the first
    line that is not part of it. Rows are rebuilt from their first five cells, so running this twice
    does not add the column twice.
    """
    text = README.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        head = next(
            i for i, line in enumerate(lines) if line.startswith(DELIVERY_ROW_START)
        )
    except StopIteration:
        raise SystemExit(
            "README.md has no 'Delivery at a glance' table to update"
        ) from None

    overall = Milestone(
        0,
        "all",
        sum(m.closed for m in milestones.values()),
        sum(m.total for m in milestones.values()),
    )
    rebuilt = [DELIVERY_HEADER + " Progress |", DELIVERY_DIVIDER + "----------|"]
    for line in lines[head + 2 :]:
        if not line.startswith("|"):
            break
        cells = line.split("|")[
            1:6
        ]  # the five original columns, whatever came after them
        label = cells[0].strip()
        milestone = milestones.get(int(label)) if label.isdigit() else None
        if milestone is None and label != "⭐":
            break
        progress = (milestone or overall).cell
        rebuilt.append("|" + "|".join(cells) + f"| {progress} |")

    tail = head + 2 + (len(rebuilt) - 2)
    return "\n".join(lines[:head] + rebuilt + lines[tail:]) + (
        "\n" if text.endswith("\n") else ""
    )


def apply_to_index(milestones: dict[int, Milestone]) -> str:
    """The milestone index's *Milestone summary* table, with its Status column rewritten.

    Only the last cell of each row changes, and only for a row whose first cell names a milestone
    (``**[M3: …``), so the focus, issue range and owner stay exactly as they were written.
    """
    text = INDEX.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        head = next(
            i for i, line in enumerate(lines) if line.startswith(INDEX_ROW_START)
        )
    except StopIteration:
        raise SystemExit(
            f"{INDEX.name} has no 'Milestone summary' table to update"
        ) from None

    for index, line in enumerate(lines[head + 2 :], start=head + 2):
        if not line.startswith("|"):
            break
        named = INDEX_MILESTONE.search(line)
        milestone = milestones.get(int(named["number"])) if named else None
        if milestone is None:
            continue
        cells = line.split("|")
        cells[-2] = f" {milestone.status_cell} "
        lines[index] = "|".join(cells)

    overall = Milestone(
        0,
        "all",
        sum(m.closed for m in milestones.values()),
        sum(m.total for m in milestones.values()),
    )
    done = sum(1 for m in milestones.values() if m.done)
    summary = (
        f"{INDEX_SUMMARY_PREFIX} {overall.cell} closed · "
        f"**{done} of {len(milestones)} milestones done**"
    )
    for index, line in enumerate(lines):
        if line.startswith(INDEX_SUMMARY_PREFIX):
            lines[index] = summary
            break
    else:  # first run: the line becomes its own paragraph under the table's hand-written total
        total = next(
            i for i, line in enumerate(lines) if line.startswith(INDEX_TOTAL_PREFIX)
        )
        blank = next(
            i for i, line in enumerate(lines[total:], total) if not line.strip()
        )
        lines[blank + 1 : blank + 1] = [summary, ""]
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def main(argv: list[str] | None = None) -> int:
    """Write (or check) every milestone's progress; report what moved."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when a document disagrees with GitHub, and change nothing",
    )
    parser.add_argument(
        "--close-completed",
        action="store_true",
        help="also close the GitHub milestone once its last issue is closed",
    )
    parser.add_argument(
        "--assume-closed",
        type=parse_issue_numbers,
        default=frozenset(),
        metavar="N[,N...]",
        help=(
            "count these open issues as closed: the issues this pull request (and any branch it is "
            "stacked on) closes, so the bars are right the moment it merges"
        ),
    )
    parser.add_argument("--repo", help="owner/name (default: the current repository)")
    args = parser.parse_args(argv)

    milestones, found = read_milestones(args.repo, args.assume_closed)
    missing = sorted(args.assume_closed - found)
    if missing:
        raise SystemExit(
            f"--assume-closed names issues in no milestone: {', '.join(map(str, missing))}"
        )
    if args.assume_closed:
        print(
            "  assuming closed on merge: "
            + ", ".join(f"#{n}" for n in sorted(args.assume_closed))
        )
    by_number = {m.number: m for m in milestones}
    stale: list[str] = []

    for milestone in milestones:
        path = milestone_doc(milestone.number)
        if path is None:
            print(f"  no document for milestone {milestone.number}; skipped")
            continue
        current = path.read_text(encoding="utf-8")
        updated = apply_to_doc(path, milestone)
        if updated != current:
            stale.append(str(path.relative_to(ROOT)))
            if not args.check:
                path.write_text(updated, encoding="utf-8")
        print(f"  M{milestone.number:<2} {milestone.cell}")

    for path, updated in (
        (README, apply_to_readme(by_number)),
        (INDEX, apply_to_index(by_number)),
    ):
        if updated != path.read_text(encoding="utf-8"):
            stale.append(str(path.relative_to(ROOT)))
            if not args.check:
                path.write_text(updated, encoding="utf-8")

    if args.close_completed and not args.check:
        for milestone in milestones:
            if not milestone.done:
                continue
            endpoint = _repo_path(args.repo, f"/milestones/{milestone.number}")
            if json.loads(_gh("api", endpoint))["state"] != "closed":
                _gh("api", "-X", "PATCH", endpoint, "-f", "state=closed")
                print(f"  closed GitHub milestone {milestone.number}")

    if args.check and stale:
        print("\n::error title=Milestone progress::out of date: " + ", ".join(stale))
        print(
            "run `make milestone-progress` (with the same --assume-closed) and commit the result"
        )
        return 1
    print(
        f"\n{len(milestones)} milestone(s): "
        + ("up to date" if not stale else f"updated {', '.join(stale)}")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
