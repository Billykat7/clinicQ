# PR: Make every deploy a manual run, and name it "Deploy ClinicQ \<version\>" (Issue 11 follow-up / M2-11)

**Milestone:** [Milestone 2: CI/CD, Environments & Team Workflow](https://github.com/Billykat7/clinicQ/milestone/2) ·
**Issue:** [#11](https://github.com/Billykat7/clinicQ/issues/11) (CD: staging on tag, production behind approval), closed by PR #126

Pushing a release tag did two things at once: `release.yml` published the image, and `deploy.yml`
woke on that workflow's completion and put it on staging, with nobody asked. That is one decision
too many for one `git push --tags` — a tag is a statement about an artifact, not about a host — and
it is not how BK Properties' CD is run, where a deploy is something a person starts. This PR removes
the `workflow_run` trigger, so **Deploy has exactly one trigger and it is "Run workflow"**, and gives
the workflow a `run-name` so the Actions list says which product and which version a run carries:
**Deploy ClinicQ 0.2.0**, where it used to say only "Deploy".

## Scope

- **In:** `.github/workflows/deploy.yml` (triggers, `run-name`, and the input fallbacks that only
  existed to serve the automatic run), its two guard tests, and the four places the docs promised
  that staging deploys itself.
- **Out:** every step of the deploy is untouched — pre-flight, migrations, candidate, swap, the
  team-channel message, the approval on `production` and the rollback path are byte-for-byte the
  same, as is `release.yml`. Nothing about *how* a deploy runs changes here, only *who starts it*.

## Summary

- **One trigger.** `workflow_run: {workflows: [Release], types: [completed]}` is gone. Deploying is
  now: push the tag, let Release publish the image, then **Actions → Deploy → Run workflow** with
  that version and an environment.
- **`run-name: Deploy ClinicQ ${{ inputs.version }}`.** The same device `properties/.github/workflows/cd.yml`
  uses (`run-name: Deploy ${{ … }}`), with the product named because Release and Deploy runs sit in
  one list several times per version. The job under it still reads `Deploy 0.2.0 to production`, so
  the environment is one click away rather than in every row.
- **The fallbacks went with the trigger.** `inputs.environment || 'staging'` appeared four times
  (the job name, `environment.name`, the concurrency group, `ENVIRONMENT`) and existed only because
  the automatic run had no inputs. A required `choice` input is always present, so the fallbacks now
  say nothing — except in the failure case, where `|| 'staging'` would quietly deploy somewhere
  nobody chose. Likewise the job's `if:`, which only existed to tell the two events apart, and
  `TAG: github.event.workflow_run.head_branch`.
- **`SHA` for the team message is `github.sha`** — the commit the run was started from — where it
  was the release run's `head_sha`.
- **A leading `v` is still accepted**: `version="${INPUT_VERSION#v}"` keeps `v0.2.0` and `0.2.0`
  naming the same release, which the old expression did for the tag and not for the typed input.

## Design notes

**Why not keep the automatic staging deploy and only add the run name?** Because the two halves are
the same decision. The reason staging deployed itself was that a tag "meant" a deploy; once that is
false, the `workflow_run` trigger is the only thing in the file that can move a host without a
person, and the fallbacks it needs are the only expressions that can pick an environment on their
own. Leaving either behind would leave the file saying two different things about who deploys.

**What this costs.** Shipping a release is now two steps instead of one, and staging will sit on an
older version until somebody runs Deploy. That is the intended trade: the runbook's first row is a
`Run workflow`, exactly like production's, and the tag no longer has a side effect on a host.

**The guard tests are where the rule lives.**
`test_only_staging_deploys_by_itself_and_only_after_a_release` asserted the old behaviour, so it is
replaced rather than deleted: `test_nothing_deploys_itself_every_deploy_is_a_manual_run` pins the
one trigger, the absence of a job `if:`, `environment.name` with no `|| 'staging'` default, and no
`workflow_run` anywhere in the file (a leftover `github.event.workflow_run.*` expression is not an
error on a manual run — it is silently empty). `test_the_deploy_run_names_the_product_and_the_version`
pins the run name, which is otherwise the kind of cosmetic line that disappears in a refactor.

## Changes

- **`.github/workflows/deploy.yml`:** `workflow_run` removed; `run-name` added; the four
  `|| 'staging'` fallbacks, the job `if:`, the `TAG` env and `workflow_run.head_sha` removed; the
  header comment now states the two-step release and why.
- **`tests/unit/platform/test_workflow_guardrails.py`:** the trigger guard rewritten, a run-name
  guard added (34 tests in the file, all green).
- **`docs/CICD/RUNBOOK_DEPLOY.md`:** the "In one minute" staging row is now a `Run workflow`, and
  "What a deploy does" opens with the manual rule.
- **`docs/CICD/PIPELINES.md`:** the trigger table no longer chains a staging deploy onto a tag, and
  the Deploy row says what a manual run does.
- **`.github/environments/README.md`:** `staging` is no longer described as the place "a release
  deploys itself"; the `main`-only note now says `workflow_dispatch` rather than `workflow_run`.

## Testing

`actionlint` on the changed workflow reports the same six pre-existing shellcheck **info** notes
(SC2029, the deliberate client-side expansion in the `ssh` steps) as `main` does, and nothing new:

```
$ actionlint .github/workflows/deploy.yml | grep -c SC2029
6
$ git show main:.github/workflows/deploy.yml > /tmp/deploy.yml && actionlint /tmp/deploy.yml | grep -c SC2029
6
```

The guard tests, and then the whole suite through `ci-local.sh`:

```
$ python -m pytest tests/unit/platform/test_workflow_guardrails.py -q
34 passed, 1 xfailed, 1 warning in 2.03s

$ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… ./scripts/ci-local.sh --no-docker
✓ quality (ruff check, ruff format --check, mypy src/: 332 source files, no issues)
✓ secrets (gitleaks, whole history)
3 failed, 2655 passed, 1 skipped, 9 xfailed in 505.95s
```

The three failures are the machine-load-sensitive tests, not this change — no `src/` file is touched
by this PR. Run on a quiet machine they pass:

```
$ python -m pytest -q --no-cov \
    tests/e2e/dashboard/test_dashboard.py::test_call_next_answers_at_once_and_a_double_click_calls_one_patient \
    tests/integration/discovery/test_nearby_search.py::test_an_open_now_search_also_stays_inside_the_budget \
    tests/e2e/display/test_board_accessibility.py::test_under_reduced_motion_the_new_call_stops_pulsing_and_gains_a_still_ring
3 passed, 8 warnings in 13.42s
```

- [x] `ruff check .`, `ruff format --check .`, `mypy src/` green
- [x] `actionlint` clean of new findings on `deploy.yml`
- [x] The guard tests assert the new rule, and fail on the old file (the rewritten test is what
      would have to change to bring `workflow_run` back)
- [ ] **Not runnable here:** a deploy itself. Neither environment is provisioned (`DEPLOY_HOST`,
      `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`, `APP_ENV`, `DEPLOY_DIR` are unset), so a dispatched run
      would stop at "Is this environment provisioned?" and change nothing — by design. The run name
      and the input form are visible on the first `Run workflow` after merge.

## Acceptance criteria

This is a follow-up to Issue 11, whose acceptance criteria are unchanged and still met: migrations
run before the new image serves, production waits for the DevOps/QA Lead, a rollback skips the
migrations, and the deploy is serialised per environment. What changes is the trigger:

- [x] Deploy runs only when a person runs it (`workflow_dispatch`, one trigger)
- [x] A tag publishes an image and deploys nothing
- [x] The Actions list shows `Deploy ClinicQ <version>` for a deploy run
- [x] The docs a reader would trust say the same thing

## Risk and rollback

- **The risk is a version sitting un-deployed.** Staging no longer follows a tag, so someone has to
  run Deploy. The runbook's first row is now that run, and the release checklist ends at a published
  image, which is what it always actually produced.
- **Nothing in flight can break.** No deploy step, secret, environment or approval changed; a run
  started from the Actions tab behaves exactly as a manual run did before this PR.
- **Rollback is the trigger block.** Restoring the four lines of `workflow_run:` and the
  `|| 'staging'` fallbacks brings the automatic staging deploy back; the guard test names both.

Refs #11
