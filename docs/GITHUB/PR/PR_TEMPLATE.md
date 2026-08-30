# PR: <short imperative title> (Issue <N> / M<MS>-<nn>)

<One paragraph, plain English: what problem this solves and what a reader should understand
before looking at the diff. Not a list of files: a reason.>

## Summary

- **<Change one>:** what it does and the behaviour it produces.
- **<Change two>:** …
- **<Change three>:** …

## Design notes

<The decisions a reviewer would otherwise have to reverse-engineer: why this approach and not the
obvious alternative, what invariant is being protected, what is deliberately out of scope.>

## Changes

- **`path/to/file.py`:** what changed and why.
- **`path/to/other.py`:** …

## Testing

- [ ] `./scripts/ci-local.sh` green
- [ ] New tests cover every acceptance criterion on the issue
- [ ] Manual check: <what you actually clicked, on what data>
- [ ] Screenshot or recording attached (required for any UI change)

## Acceptance criteria

<Copy the checklist from the issue, ticked.>

## Risk and rollback

<What could break, who is affected, and how to undo this if it does.>

Closes #<N>
