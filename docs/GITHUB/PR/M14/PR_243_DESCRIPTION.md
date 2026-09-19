# PR: Deploy through the platform's shared CD (Issue 243 / M14-243) — **draft**

**Milestone:** [Milestone 14: Production Readiness, Pilot & Go-live](https://github.com/Billykat7/clinicQ/milestone/14) ·
**Issue:** [#243](https://github.com/Billykat7/clinicQ/issues/243) · **Builds on:** #239 (the CD
secrets split and the host's paths), merged

> **This is a draft, and it does not pass its own guards yet.** It is open so the direction can be
> reviewed before the guards are rewritten to match it — see *What is still failing* below, which
> is the substance of the review, not a list of chores.

`deploy.yml` becomes a thin caller of `Billykat7/infra`'s `cd-product.yml@main`, the shape BK
Properties and the other BTK products already use:

```yaml
jobs:
  deploy:
    uses: Billykat7/infra/.github/workflows/cd-product.yml@main
    with:
      project_slug: clinicq
      app_port: "8011"
      domain: clinicq.bkatalayi.com
      db_schema: clinicq
      display_name: BK ClinicQ
      compose_file: infra/docker/docker-compose.prod.yml
    secrets: inherit
```

The reusable workflow owns the pull, the compose up, the nginx render, the registry row and the
deploy events; this repository names only what is its own. 216 lines of ClinicQ-specific deploy
choreography go away, and the gateway becomes the one place CD is changed for every product.

`ci.yml` is in this PR too, and is **comment-only**: every job, shard, matrix entry, action pin,
timeout and concurrency group is byte-for-byte what it was. No test reads a comment, and the suite
agrees.

## What is still failing, and why it is left failing

`actionlint` refuses one line, and seven guards in `tests/unit/platform/test_workflow_guardrails.py`
still describe the on-host sequence this replaces. **They are not relaxed in this PR**, because six
of the seven are promises about what a deploy guarantees, and retiring one is a decision about
ClinicQ's release safety rather than a line to delete.

| What fails | Why | What it needs |
|---|---|---|
| `actionlint`: `inputs.image_tag` is not defined | the inputs here are `environment`, `version`, `rollback`; `github.event_name == 'push'` is dead too — there is no push trigger | almost certainly `image_tag: ${{ inputs.version }}`, and drop the `push` half |
| `test_every_action_is_pinned_to_a_commit` | `…/cd-product.yml@main` is a moving ref; a tag or branch can be repointed at other code after review | pin `@<sha>`, or agree in writing that an internal reusable workflow is exempt |
| `test_the_deploy_sequence_is_identical_in_both_workflows` | CI and the deploy must run the *same* `scripts/db/deploy-sequence.sh`, so "what a deploy does" has one definition | decide where migrations run (below) |
| `test_migrations_run_before_the_new_image_serves_and_never_on_a_rollback` | today migrations run in the new image **before it serves**, and a rollback skips them | `cd-product.yml` leaves schema migrations to an operator |
| `test_nothing_deploys_itself_every_deploy_is_a_manual_run` | a release must never also be a deploy | confirm the caller stays `workflow_dispatch`-only |
| `test_the_deploy_runs_on_the_host_rather_than_reaching_it_over_ssh` | asserts the `clinicq` runner labels on this job | the labels now live in `cd-product.yml` (`self-hosted, Linux, X64, infra`) — the guard has to look there or be dropped |
| `test_the_deploy_directory_comes_only_from_deploy_dir` | Issue 239's rule: the repo names no host path, and `DEPLOY_DIR` is a masked secret | `cd-product.yml` derives its path from `PRODUCTION_DEPLOY_PATH` instead |
| `test_the_app_settings_reach_the_host_privately` | asserts `APP_ENV` is written `umask 077` and never echoed | `APP_ENV` was removed by #239; settings now come from `deploy/env/` + SOPS |

## Three things this gives up, which are the real question

Worth deciding explicitly, because each one is a safety property ClinicQ has today:

1. **Migrations before traffic.** ClinicQ runs `deploy-sequence.sh` *inside the new image* before it
   serves anything, and CI runs the same script on every pull request (Issue 9). `cd-product.yml`
   leaves migrations to an operator.
2. **The candidate on a side port.** ClinicQ smoke-tests the new image on a port nothing routes to,
   then swaps and rolls back by itself if the live check fails.
3. **ClinicQ's own settings.** `cd-product.yml` writes `.env` from `write-prod-env.sh`, whose key
   list is shared across BTK products and carries none of ClinicQ's ~179 settings — every `SMS_*`,
   `QUEUE_*`, `DISPLAY_*`, `PATIENT_*` and `VAPID_*` value would fall back to its default. That is
   the problem [#239](https://github.com/Billykat7/clinicQ/pull/241)'s `deploy/env/` split solved
   two days ago, and this PR does not yet say how the two fit together.

None of that argues against the direction — one place to change CD for eleven products is worth a
lot. It argues for answering these three before it merges.

## Verification

- [x] `TZ=UTC pytest tests/unit/platform` — **293 passed**, 3 xfailed, **7 failed** (the table above).
- [x] `ci.yml` is comment-only: no guard, shard map or pin test changes behaviour.
- [x] `python scripts/update_milestone_progress.py --check --assume-closed 243` — up to date.
- [ ] **Not run:** a deploy. It needs the platform host's runner and the gateway checkout, neither of
  which is this machine.

## Risk and rollback

Nothing on any host changes until someone dispatches a deploy, and the workflow will not run in its
current state. Rollback is a revert; `main`'s `deploy.yml` (Issue 239) is a working deploy today.

Closes #243
