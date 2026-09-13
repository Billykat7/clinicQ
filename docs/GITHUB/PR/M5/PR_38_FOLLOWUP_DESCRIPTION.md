# PR: Name this project, not another one, in the IDE rules (Issue 38 / M5-38 follow-up)

**Milestone:** [Milestone 5: Discovery & Geolocation](https://github.com/Billykat7/clinicQ/milestone/5) ·
**Issue:** [#38](https://github.com/Billykat7/clinicQ/issues/38) (closed by #154; this is its
follow-up)

#154 added `docs/IDE/RULES/`, the committed copy of the editor rules. Two of those rules were copied
from another project and still named it, so an editor or agent following them would be pointed at a
directory and an image tag that do not exist here.

Documentation only.

## Summary

- **`activate-venv-before-commands.mdc`:** "From the project root (`properties`)" → `clinicQ`.
- **`infra-layout.mdc`:** the example build tags `properties-app:local` → `clinicq:local`, matching
  the `clinicq` image name `infra/docker/docker-compose.prod.yml` already uses.

## Changes

- **`docs/IDE/RULES/activate-venv-before-commands.mdc`**, **`docs/IDE/RULES/infra-layout.mdc`:** the
  two lines above.
- **`docs/GITHUB/PR/M5/PR_38_FOLLOWUP_DESCRIPTION.md`:** this description.

## Testing

- [x] No other mention of the other project is left in either copy of the rules:

```text
$ grep -rn "properties" docs/IDE/RULES
(no output)
```

- [x] The editor's local, untracked copy of the rules has the same two edits.

- [x] This PR closes no issue, so the progress bars are checked with no assumption, and they agree with
      GitHub:

```text
$ python scripts/update_milestone_progress.py --check
  M5  🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** (8/8 issues)
14 milestone(s): up to date
```

## Risk and rollback

None beyond the two example lines. Rollback is a revert.

Refs #38 (already closed by #154; this PR closes nothing new)
