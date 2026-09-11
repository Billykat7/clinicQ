<!--
A pull request into main (CONTRIBUTING.md). The same headings as docs/GITHUB/PR/M*/PR_<N>_DESCRIPTION.md,
which is where the full description lives; paste it here, or fill these sections in.

Title: "Issue <N>: <imperative summary>". Branch: Issue/<N>/<short-slug>. Every commit: "Issue <N>: ...".
CI checks the branch, the commits, the closing line and, for a UI change, the screenshot.
-->

# PR: <short imperative title> (Issue <N> / M<MS>-<nn>)

**Milestone:** [M<MS>](https://github.com/Billykat7/clinicQ/milestones) · **Issue:** #<N>

<One paragraph, plain English: what problem this solves and what a reader should understand
before looking at the diff. Not a list of files: a reason.>

## Scope

- **In:** <what this pull request changes>
- **Out:** <what it deliberately leaves alone, and which issue has it>

## Summary

- **<Change one>:** what it does and the behaviour it produces.
- **<Change two>:** …

## Design notes

<The decisions a reviewer would otherwise have to reverse-engineer: why this approach and not the
obvious alternative, what invariant is being protected, what is deliberately out of scope.>

## Changes

- **`path/to/file.py`:** what changed and why.

## Testing

<Evidence, not intentions: the commands you ran and what they printed (trimmed).>

- [ ] `./scripts/ci-local.sh` green
- [ ] New tests cover every acceptance criterion on the issue
- [ ] Manual check: <what you actually did, on what data>

## Screenshots

<Required for any change under src/templates/ or src/static/ (CI checks): before and after, desktop
and mobile where it differs. Delete this section only when no UI file changed.>

## Acceptance criteria

<Copy the checklist from the issue, and tick each item with the evidence that shows it.>

## Risk and rollback

<What could break, who is affected, and how to undo this if it does.>

Closes #<N>
