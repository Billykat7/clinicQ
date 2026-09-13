# PR: Point every rule reference at the committed copy in docs/IDE/RULES (Issue 38 / M5-38 second follow-up)

**Milestone:** [Milestone 5: Discovery & Geolocation](https://github.com/Billykat7/clinicQ/milestone/5) ·
**Issue:** [#38](https://github.com/Billykat7/clinicQ/issues/38) (closed by #154; this is its second
follow-up)

Docstrings, comments and docs across the repository cite the editor rules as
`.cursor/rules/<rule>.mdc`. That directory is gitignored, so on a fresh clone, on GitHub and in CI
every one of those citations points at a file that does not exist. #154 committed the same rules under
`docs/IDE/RULES/`, and this PR points the citations there.

Comments and documentation only; no behaviour changes.

## Summary

- **64 files, one line each:** `.cursor/rules/<rule>.mdc` → `docs/IDE/RULES/<rule>.mdc` in test and
  module docstrings, `pyproject.toml`'s `DTZ` comment, `.dockerignore`, `scripts/dev.sh`,
  `scripts/ci-local.sh`, `scripts/check_pr_conventions.py` and `scripts/lint_surface_gates.py`.
- **Two places reworded**, because a straight path swap made them wrong:
  - `CONTRIBUTING.md` said "Editors that read `docs/IDE/RULES/` pick the same rule up". Editors read
    `.cursor/rules/`, so it now says the rule is written down in
    `docs/IDE/RULES/milestone-progress.mdc`.
  - `PR_38_FOLLOWUP_DESCRIPTION.md` (#158) ended up calling `docs/IDE/RULES/` "not tracked". It now
    says the editor's local, untracked copy has the same two edits.

## Changes

- The 64 citation lines above, and the two rewordings.
- **`docs/GITHUB/PR/M5/assets/pr38-followup-2/admin-verification.png`** (new): the screenshot below.
- **`docs/GITHUB/PR/M5/PR_38_FOLLOWUP_2_DESCRIPTION.md`:** this description.

## Testing

- [x] No citation of `.cursor/rules` is left outside `.cursor/` itself:

```text
$ grep -rln "\.cursor/rules" --exclude-dir=.git --exclude-dir=.venv . | grep -v "^./.cursor/"
(no output)
```

- [x] `ruff check .` and `ruff format --check .` clean; `bash -n scripts/dev.sh scripts/ci-local.sh`
      clean.
- [x] Full suite with PostgreSQL and Redis required, in UTC: **1665 passed, 9 xfailed**.
- [x] This PR closes no issue, so the progress bars are checked with no assumption. M5 reads 8/8, and
      they agree with GitHub.
- [x] **Screenshot.** `src/templates/admin/verification.html` and
      `src/static/js/admin-verification.js` changed only inside their header comments. Here is the page
      a platform admin sees on a freshly migrated and seeded database with this branch running. The
      page returns 200 and `admin-verification.js` loads:

      ![The clinic verification page, Waiting tab, empty on a fresh database](https://github.com/Billykat7/clinicQ/blob/1cca7bbd945a11b56cca536d0495d9f4b41e7206/docs/GITHUB/PR/M5/assets/pr38-followup-2/admin-verification.png?raw=true)

      The browser console shows one unrelated error, a `403` from
      `/api/v1/notifications/center/unread-count` for the platform admin. It comes from the header's
      notification bell, and this PR changes nothing in that code.

## Risk and rollback

None beyond comments and docs. Rollback is a revert.

Refs #38 (already closed by #154; this PR closes nothing new)
