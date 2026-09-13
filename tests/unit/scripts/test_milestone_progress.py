"""The progress generator counts a pull request's own issues as closed when told to.

A pull request writes the bars as they will read once it merges, because its issue is still open
while it is in review. These tests cover the counting and the argument parsing without calling
GitHub: ``read_milestones`` is the only part that needs ``gh``, and it only feeds these functions.
"""

import argparse

import pytest

from scripts.update_milestone_progress import (
    Milestone,
    count_closed,
    parse_issue_numbers,
)

_M5 = [
    {"number": 31, "state": "CLOSED"},
    {"number": 32, "state": "OPEN"},
    {"number": 33, "state": "OPEN"},
    {"number": 34, "state": "CLOSED"},
]


def test_without_an_assumption_only_githubs_closed_issues_count() -> None:
    assert count_closed(_M5, frozenset()) == 2


def test_the_issues_a_pull_request_closes_count_as_closed() -> None:
    assert count_closed(_M5, frozenset({32})) == 3
    assert count_closed(_M5, frozenset({32, 33})) == 4


def test_assuming_an_already_closed_issue_counts_it_once() -> None:
    assert count_closed(_M5, frozenset({31, 32})) == 3


def test_issue_numbers_parse_with_commas_spaces_and_hashes() -> None:
    assert parse_issue_numbers("32,35 #36") == frozenset({32, 35, 36})
    assert parse_issue_numbers("") == frozenset()


def test_anything_but_an_issue_number_is_refused() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="not an issue number"):
        parse_issue_numbers("32,thirty-five")


def test_the_last_issue_assumed_closed_marks_the_milestone_done() -> None:
    issues = [{"number": n, "state": "OPEN"} for n in range(31, 39)]
    closed = count_closed(issues, frozenset(range(31, 39)))
    milestone = Milestone(5, "Milestone 5", closed, len(issues))
    assert milestone.done
    assert milestone.cell == "🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** (8/8 issues)"
