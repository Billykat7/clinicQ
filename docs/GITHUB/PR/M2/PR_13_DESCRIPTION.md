# PR: Encode code owners, templates and rulesets, and check the conventions in CI (Issue 13 / M2-13)

**Milestone:** [Milestone 2: CI/CD, Environments & Team Workflow](https://github.com/Billykat7/clinicQ/milestone/2) ·
**Issue:** [#13](https://github.com/Billykat7/clinicQ/issues/13)

Six people on one `main` need rules that hold when nobody is watching. This PR writes them down as
the files GitHub reads, and makes CI check the ones GitHub cannot. `.github/CODEOWNERS` routes every
path to one owner. A pull-request template and three issue forms ask the questions that make work
reviewable without a conversation. Two rulesets, kept as code and applied with
`make gh-sync-rulesets`, reject direct pushes and require a green CI gate for everyone, plus one
code-owner approval that only an administrator may bypass, through a pull request. A new
`conventions` job checks the branch name, the commit prefixes, the closing line and a screenshot for
UI changes. `labels.yml` is synced: GitHub now has exactly its 42 labels.

## Summary

- **CODEOWNERS** from `WORKLOAD_SPLIT.md` §2, translated from its `app/` layout to `src/`. There is one
  owner per path; where §2 named two roles, the second is the backup reviewer. The role table, the
  backups and the SLA are in the file's header. Every path routes to @Billykat7 (E) until §1 names
  the team, which was the decision at the start of M2.
- **Templates.** `.github/pull_request_template.md` (scope, test evidence, screenshots, `Closes #N`) is
  byte for byte `docs/GITHUB/PR/PR_TEMPLATE.md`, which gained the Scope and Screenshots sections.
  **Feature**, **Bug** and **Spike** issue forms apply their type label; blank issues are off.
- **Rulesets as code.** `main: CI gate` (everyone, no bypass): no deletion, no force-push, changes only
  through a pull request, `CI gate` from the GitHub Actions app. `main: review`: one approval from
  the code owner, stale approvals dismissed on push, bypassable by the Admin role in pull-request
  mode only. `scripts/gh_sync_rulesets.py` applies them by name and is idempotent.
- **Conventions in CI.** `scripts/check_pr_conventions.py`, run by a new `conventions` job that needs
  nothing and that the gate requires on every pull request. It checks `Issue/<N>/<slug>` (or
  `Release/v<X.Y.Z>`), `Issue <N>: ` on every non-merge commit, `Closes #N` (or `Refs #N` for a
  follow-up), and an image in the description when `src/templates/` or `src/static/` changed.
- **Labels synced**, and the sync fixed: it compared names case-sensitively, so its first `--prune`
  deleted two of the file's own labels (below). It is now case-insensitive and renames on a case
  difference.
- **CONTRIBUTING.md:** the conventions, what `main` accepts, reviews and the admin bypass, and issues.

## Design notes

**One owner per path, with every role on one person for now.** CODEOWNERS accepts only real users
here (a personal repository has no teams), and §1 names only E. Placeholder handles would make
GitHub reject the file. So every rule names @Billykat7, and the header and a comment on each rule
keep the role, so handing a role over means replacing a handle on its lines.
`test_team_workflow.py` fails if a rule has two owners, names anyone not in §1, or if a `src/`
package or top-level folder has no rule of its own. GitHub reports 0 errors for the file.

**Two rulesets, because the bypass must be narrow.** A single ruleset with an admin bypass would let
an administrator push to `main` directly. Split in two, the no-bypass one (pull request required, CI
gate, no force-push) holds for everyone, and only the approval rule can be bypassed, and only while
merging a pull request. The bypass exists because the code owner of every path cannot approve their
own pull request; CONTRIBUTING.md says when it may be used, and GitHub logs each one.

**Conventions are checked, not only written.** A convention nobody checks lasts until the first busy
week. The check is its own job for two reasons. It never stops the tests (a naming slip is fixed in
seconds). And "Re-run failed jobs" after fixing a description then re-runs just this job and the
gate, which read the pull request afresh. The first version was a `continue-on-error` step, which
left its job green, so the re-run replayed the stale failure; the walkthrough caught it. The job
adds a minute to every run (the PIPELINES.md projection moved from 790 to 890 of 2,000).

**`Refs #N` counts.** Run against the 14 pull requests already merged, the first rule set failed
#118 and #119, which are follow-ups to issues an earlier PR had closed and say `Refs #N` on purpose.
They pass now. #110 (a UI scaffold with no screenshot) and #121 (the Issue 9 demo, no closing line)
still fail, as they should.

**Out of scope:** the CI workflow itself (Issue 9) and the deploy environments (Issue 11).

## Changes

- **`.github/CODEOWNERS`**, **`.github/pull_request_template.md`**, **`.github/ISSUE_TEMPLATE/`**
  (`feature.yml`, `bug.yml`, `spike.yml`, `config.yml`), **`.github/rulesets/main-review.json`**,
  **`.github/rulesets/README.md`** (new); **`.github/rulesets/main-ci-gate.json`:** the deletion,
  force-push and pull-request rules beside the CI gate.
- **`.github/workflows/ci.yml`:** the `conventions` job; the gate needs it and requires it on a pull
  request; `PROSE_PATHS` narrowed (below).
- **`scripts/check_pr_conventions.py`**, **`scripts/gh_sync_rulesets.py`** (new);
  **`scripts/gh_sync_labels.py`:** case-insensitive matching and renames. **`Makefile`:**
  `make gh-sync-rulesets`.
- **`tests/unit/platform/test_team_workflow.py`** (new): CODEOWNERS, templates, issue forms, labels,
  both rulesets and the sync's comparison. **`tests/unit/platform/test_pr_conventions.py`** (new):
  every rule, accepted and refused. **`test_workflow_guardrails.py`:** the conventions job's
  shape.
- **`docs/GITHUB/PR/PR_TEMPLATE.md`**, **`CONTRIBUTING.md`**, **`docs/CICD/PIPELINES.md`**,
  **`docs/TEAM/WORKLOAD_SPLIT.md`** (§1: E is Billykat7).

## Testing

- [x] **A direct push to `main` is rejected**, for an administrator too. An empty probe commit on a
      throwaway local branch:

      ```text
      $ git push origin HEAD:main
      remote: - Required status check "CI gate" is expected.
      remote: - Changes must be made through a pull request.
       ! [remote rejected] HEAD -> main (push declined due to repository rule violations)
      origin/main is still: db3f7a9 Merge pull request #123 …
      ```

- [x] **No approval, no merge:** this PR, with CI green, reads
      `reviewDecision=REVIEW_REQUIRED mergeStateStatus=BLOCKED`. A red CI gate blocks everyone,
      administrators included (Issue 9's PR #121, and #125 below).
- [x] **The conventions check blocks a pull request that breaks them** (demo PR #125, closed
      unmerged): a commit `wip` and no closing line. Every test job passed, and the gate failed:

      ```text
      failure: Convention: commit 'wip' does not start with 'Issue 13: '
      failure: Convention: the description does not say `Closes #13` (or `Refs #13` for a follow-up …)
      Tests (unit): success · Tests (integration): success · Tests (flows): success · CI gate: failure
      mergeStateStatus=BLOCKED
      ```

      On this PR: `Conventions: success` ("2 commit(s), 21 file(s): follows the conventions").
- [x] **CODEOWNERS is valid for GitHub:** `GET /repos/…/codeowners/errors?ref=Issue/13/…` → 0 errors.
- [x] **Labels, synced cleanly.** The first `make gh-sync-labels` created all 42 (GitHub had only its 9
      defaults). Issues #1 and #2, the only ones using a default label (`enhancement`), were
      relabelled `TYPE: Feature` and `AREA: Infra`, and `--prune` removed the 9 defaults. That prune
      also removed `Good First Issue` and `Help Wanted` from the file: GitHub matches label names
      without case, so they *were* `good first issue` and `help wanted`. No issue carried them.
      With the script fixed, a sync recreated both, and now

      ```text
      $ python scripts/gh_sync_labels.py --prune --dry-run
      Done. created=0 updated=0 unchanged=42 stale=0 pruned=0
      ```

- [x] **Rulesets applied and in sync:** `make gh-sync-rulesets` updated `main: CI gate` (22938115) and
      created `main: review` (22946886); `ARGS=--dry-run` now reports both `unchanged`. The rules
      on `main`: `deletion`, `non_fast_forward`, `pull_request` (two rulesets), `required_status_checks`.
- [x] **The suite:** 1010 passed, 16 skipped, 11 xfailed; `./scripts/ci-local.sh --no-docker` green.
- [ ] **After merge (GitHub reads issue forms and the PR template from `main` only):** a new issue
      opening from the forms. Checked right after the merge; the evidence is posted on this PR.

## Walking an issue through the workflow

Issue 13 itself went branch → PR → CI → merge under the new rules: branch
`Issue/13/team-workflow-templates-codeowners`, every commit `Issue 13: …`, the description from the
template's sections, CI with the conventions job, a review the ruleset required, and a merge through
the administrator bypass. What was confusing, or wrong, on the way:

1. **The only code owner cannot approve their own pull request**, and GitHub requests no review at
   all when the author owns every path. Until §1 names the team, every teammate's PR waits on one
   reviewer, and that reviewer's own PRs need the bypass. Filling in §1 is the fix, and it is the
   team's to do.
2. **A re-run after fixing a description replayed the old failure** while the check was a step.
   Fixed by giving it its own job.
3. **Issue forms and the PR template cannot be tried on a branch**: GitHub reads them from `main`,
   so a template change is only visible after it merges.
4. **Label names are case-insensitive on GitHub**, and the sync script did not know it; its prune
   deleted two labels. Fixed, with a note in the script.
5. **The docs-only fast path had to shrink** the moment these tests started reading
   `docs/TEAM/WORKLOAD_SPLIT.md`, `labels.yml` and the PR template. Issue 9's guard test failed
   with the file names, which is what it is for.
6. **GitHub adds defaults to a ruleset** (`required_reviewers: []`), so the first sync reported drift
   forever. The comparison now covers only what the files state.

**Not done: the walkthrough "as a team".** It needs teammates, and only one person was available.
At the sprint review, one teammate should open an issue from a form, branch and push, and a
different code owner should review and merge without the bypass.

## Acceptance criteria

- [x] A direct push to `main` is rejected (above, as an administrator).
- [x] A pull request cannot merge without CI green and one approval. CI green is required for
      everyone, with no bypass. The approval is required for everyone but the Admin role, which may
      bypass it only when merging a pull request; the reason is documented in CONTRIBUTING.md.
- [x] Every module path has exactly one code owner, with a documented backup reviewer (CODEOWNERS
      header and §1; enforced by `test_every_codeowners_path_has_exactly_one_owner_from_the_team_table`).
- [ ] New issues open with the template pre-filled: the forms are in place and tested; the live
      check needs them on `main` (posted on this PR after the merge).
- [x] `labels.yml` is the single source of truth and syncs cleanly (42 = 42, a clean `--prune` dry-run).
- [ ] The whole team has walked through one issue end to end: walked through by the DevOps/QA Lead
      alone (above); the team walkthrough is for the sprint review.

## Risk and rollback

The rulesets change how everyone merges: nothing reaches `main` without a pull request and a green
CI gate, and a PR needs an approval unless an administrator bypasses it. A wrong rule is undone by
editing its JSON and running `make gh-sync-rulesets`, or, in an emergency, by setting the ruleset's
enforcement to *Disabled* in **Settings → Rules** (then restoring it from the file). Reverting this PR
does not remove the rulesets from GitHub; they are settings. The two labels the first prune deleted
were recreated; no issue lost a label.

Closes #13
