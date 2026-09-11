# Rulesets on `main`

The branch rules GitHub enforces, kept here as code (Issues 9 and 13). GitHub does not read these
files; `make gh-sync-rulesets` (`scripts/gh_sync_rulesets.py`) applies them, matched by `name`, and
`ARGS=--dry-run` shows the difference first. Change a rule here, in a pull request, then sync;
never only in the GitHub settings page, or this file stops being true.

| File | Applies to | Rules | Who may bypass |
|------|-----------|-------|----------------|
| `main-ci-gate.json` | everyone | no deletion, no force-push, changes only through a pull request, and the **CI gate** check (from GitHub Actions) must pass | nobody, administrators included |
| `main-review.json` | everyone | one approving review, from the code owner of the changed paths (`.github/CODEOWNERS`); a new push dismisses an earlier approval | repository administrators, and only when merging a pull request |

GitHub applies the most restrictive rule from every active ruleset. So a direct push to `main` is
rejected for everyone; nothing merges with a red or missing CI gate; and a pull request needs one
code-owner approval unless an administrator merges it, which the ruleset's insights log as a bypass.
Why the administrator bypass exists, and when to use it: CONTRIBUTING.md, "Reviews".

`actor_id: 5` is GitHub's built-in *Admin* repository role. `integration_id: 15368` is the GitHub
Actions app, so no other integration can report the CI gate green.
