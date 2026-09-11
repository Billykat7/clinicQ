"""A deliberately failing test (Issue 9 demonstration; this branch is never merged).

Its only job is to prove that a red test blocks the merge of a pull request into main.
"""


def test_this_pull_request_must_not_merge() -> None:
    """Fails on purpose: CI goes red and the CI gate check blocks the merge button."""
    assert 1 + 1 == 3, "deliberately failing test: the merge must be blocked"
