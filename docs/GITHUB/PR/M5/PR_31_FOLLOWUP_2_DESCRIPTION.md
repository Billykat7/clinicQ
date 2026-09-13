# PR: Progress bars written as they will read on merge (Issue 31 / M5-31 second follow-up)

**Milestone:** [Milestone 5: Discovery & Geolocation](https://github.com/Billykat7/clinicQ/milestone/5) ·
**Issue:** [#31](https://github.com/Billykat7/clinicQ/issues/31) (closed by #147; this is its second
follow-up) · **Unblocks:** the `Conventions` check on #150–#154, together with the Issue 32 follow-up

#155 merged before its last three commits were pushed to its branch, so `main` has the median timing
but not the progress tooling that followed it. Those three commits are carried up the M5 stack, where
`Conventions` rejects them on #150–#154 (`does not start with 'Issue 35: '`, and so on). This PR lands
them on `main`, plus one commit bringing the bars up to the issues closed since.

No application source changes.

## Summary

- **`make milestone-progress ARGS='--assume-closed <N>'`** counts the issues a pull request closes as
  closed and takes every other figure from GitHub, so the bars a pull request commits are right the
  moment it merges. A stacked pull request names its whole stack (`--assume-closed 32,35,36`). An
  assumed number in no milestone is an error, and `make milestone-progress-check ARGS=...` accepts the
  same flag.
- **`main`'s bars catch up.** This PR closes nothing, so it runs with no assumption. GitHub has 31,
  32 and 34 closed, which puts M5 at 3/8 (38%) and the project at 33/109. On `main` today they read
  0/8 and 30/109, because #147, #148 and #149 merged without regenerating them.
- **The hand-written status agrees with the bars.** The README Status block counts 33 of 109, M5's
  Status row moves from `📋 Planned` to `🚧 In progress: issues 31, 32, 34 closed`, and sprint 5's row
  says M5 is under way rather than not started.
- **CONTRIBUTING and QUICKSTART** say to run the generator with the pull request's issues, and that
  the README's hand-written count must match the generated total. The same rule is in
  `docs/IDE/RULES/milestone-progress.mdc`, which lands with #154.

## Changes

- **`scripts/update_milestone_progress.py`:** `--assume-closed`, `count_closed`, `parse_issue_numbers`.
  **`tests/unit/scripts/test_milestone_progress.py`** (new, 6 tests, no GitHub needed).
  **`Makefile`:** `milestone-progress-check` passes `ARGS`.
- **`CONTRIBUTING.md`**, **`docs/QUICKSTART.md`:** the rule.
- **`README.md`**, **`docs/GITHUB/README.md`**, **`docs/GITHUB/MILESTONES/M5_discovery_geolocation.md`**,
  **`docs/TEAM/WORKLOAD_SPLIT.md`:** the regenerated bars and the hand-written status beside them.
- **`docs/GITHUB/PR/M5/PR_31_FOLLOWUP_DESCRIPTION.md`:** #155's description as it was last edited, and
  this description.
- A merge of `origin/main` into the branch.

## Testing

- [x] `pytest tests/unit/scripts`: 6 passed.
- [x] Against GitHub, on this branch:

```text
$ python scripts/update_milestone_progress.py --check
  M5  🟩🟩🟩🟩⬜⬜⬜⬜⬜⬜ **38%** (3/8 issues)
14 milestone(s): up to date
$ python scripts/update_milestone_progress.py --check --assume-closed 999
--assume-closed names issues in no milestone: 999
$ python scripts/update_milestone_progress.py --assume-closed x
update_milestone_progress.py: error: argument --assume-closed: not an issue number: 'x'
```

## Risk and rollback

Documentation tooling and docs only. Rollback is a revert, after which the bars are only right
after someone regenerates them later.

**Merge order:** this first, then the Issue 32 follow-up (it contains these commits), then `main` is
merged forward into #150 and on up the stack.

Refs #31 (already closed by #147; this PR closes nothing new)
