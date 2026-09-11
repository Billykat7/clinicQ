"""Render pytest JUnit reports as the Markdown test summary CI publishes (Issue 9).

Each CI test shard writes ``junit-<shard>.xml``; the ``report`` job passes them all here and
posts the result to the run's summary page and, on a pull request, as a comment. One table row
per shard, then every failing test by node id with the first line of its message, so a reviewer
sees *what* broke without opening the logs.

Usage::

    python scripts/ci_test_summary.py junit/junit-unit.xml junit/junit-integration.xml

The reports are the ones this pipeline's own pytest wrote, never input from outside the run.
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

#: Failures listed in full before the rest are counted as "and N more".
MAX_LISTED_FAILURES = 25


class Outcome(StrEnum):
    """The JUnit child elements that mark a test case as not passed."""

    FAILURE = "failure"
    ERROR = "error"
    SKIPPED = "skipped"


#: pytest records an expected failure as ``<skipped type="pytest.xfail">``.
XFAIL_TYPE = "pytest.xfail"


@dataclass
class ShardResult:
    """The counts and failures of one shard's report."""

    name: str
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    xfailed: int = 0
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)


def shard_name(report: Path) -> str:
    """Return the shard a report belongs to: ``junit-unit.xml`` → ``unit``."""
    return report.stem.removeprefix("junit-")


def read_report(report: Path) -> ShardResult:
    """Count the outcomes in one JUnit report and collect its failing node ids."""
    result = ShardResult(name=shard_name(report))
    root = ET.parse(report).getroot()
    for suite in root.iter("testsuite"):
        result.seconds += float(suite.get("time", "0"))
    for case in root.iter("testcase"):
        # JUnit has no node id; classname (dotted module, then class) and name are what it keeps.
        node_id = f"{case.get('classname', '')}::{case.get('name')}"
        # An Element's truth value is its child count, so test for None, never with `or`.
        failure = case.find(Outcome.FAILURE)
        if failure is None:
            failure = case.find(Outcome.ERROR)
        skipped = case.find(Outcome.SKIPPED)
        if failure is not None:
            if failure.tag == Outcome.ERROR:
                result.errors += 1
            else:
                result.failed += 1
            message = (failure.get("message") or "").strip().splitlines()
            result.failures.append(
                f"`{node_id}`: {message[0] if message else failure.tag}"
            )
        elif skipped is not None:
            if skipped.get("type") == XFAIL_TYPE:
                result.xfailed += 1
            else:
                result.skipped += 1
        else:
            result.passed += 1
    return result


def render(results: list[ShardResult]) -> str:
    """Return the Markdown summary: a table of shards, then the failing tests."""
    failed = sum(r.failed + r.errors for r in results)
    headline = "❌ Tests failed" if failed else "✅ Tests passed"
    lines = [
        f"### {headline}",
        "",
        "| Shard | Passed | Failed | Errors | Skipped | xfailed | Time |",
        "|-------|-------:|-------:|-------:|--------:|--------:|-----:|",
    ]
    lines += [
        f"| {r.name} | {r.passed} | {r.failed} | {r.errors} | {r.skipped} | {r.xfailed} "
        f"| {r.seconds:.0f} s |"
        for r in results
    ]
    failures = [line for r in results for line in r.failures]
    if failures:
        lines += ["", "**Failing tests**", ""]
        lines += [f"- {line}" for line in failures[:MAX_LISTED_FAILURES]]
        if len(failures) > MAX_LISTED_FAILURES:
            lines.append(f"- … and {len(failures) - MAX_LISTED_FAILURES} more")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Print the summary of the given reports; exit 1 when there are none to read."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reports", nargs="*", type=Path, help="junit-<shard>.xml files")
    args = parser.parse_args(argv)
    reports = sorted(path for path in args.reports if path.is_file())
    if not reports:
        print("### ⚠️ No test reports were produced", file=sys.stderr)
        return 1
    print(render([read_report(path) for path in reports]), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
