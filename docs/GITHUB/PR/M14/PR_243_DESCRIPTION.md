# PR: Deploy through the platform's shared CD (Issue 243 / M14-243) — **draft**

**Milestone:** [Milestone 14: Production Readiness, Pilot & Go-live](https://github.com/Billykat7/clinicQ/milestone/14) ·
**Issue:** [#243](https://github.com/Billykat7/clinicQ/issues/243) · **Builds on:** #239 (the CD
secrets split and the host's paths), merged

> **Draft, and it must stay one.** Everything inside this repository is done and green — the bug,
> the pin, the tag format and all seven guards. What is left is **not ours to fix**: the shared
> workflow overwrites ClinicQ's settings file. See [The blocker](#the-blocker).

`deploy.yml` now has **two jobs**, and the split is the point:

```yaml
jobs:
  migrations:                       # ClinicQ's own, on the clinicq runner
    …  compose the .env, then ./deploy.sh run-new "$IMAGE" ./scripts/db/deploy-sequence.sh
  deploy:
    needs: migrations
    uses: Billykat7/infra/.github/workflows/cd-product.yml@b9c3e64…
```

The shared workflow owns the pull, the compose up, the nginx render, the registry row and the
deploy events — defined once for eleven products instead of eleven times. What stays here is what it
does not do and ClinicQ will not give up: **the migrations run inside the new image before anything
serves it**, using the same `scripts/db/deploy-sequence.sh` that CI runs on every pull request
(Issue 9). A failed migration means the deploy job never starts and the previous version keeps
serving.

`ci.yml` is in this PR too and is **comment-only**: every job, shard, matrix entry, action pin,
timeout and concurrency group is byte-for-byte what it was.

## Three things found while making it work

**1. A staging deploy would have gone to production.** `cd-product.yml` takes no environment — it
pins `environment: production` and derives its path from the gateway's root. The `environment` input
still offered `staging`, so choosing it would have read the **production** Environment's secrets and
deployed to the **production** directory while the run said "staging". The input is gone rather than
misleading, and a guard now fails if one comes back. Staging returns when the shared workflow can
express it.

**2. The deploy asked GHCR for an image that does not exist.** `cd-product.yml` resolves
`ghcr.io/<repo>:<image_tag>` and insists on `v`-prefixed semver; release.yml published `:<version>`
with the `v` stripped (`v0.11.0` → `0.11.0`). A deploy would have asked for `clinicq:v0.11.0` and
failed at *Verify release image exists* — **after the migrations had already run**. release.yml now
publishes `:v<version>` beside `:<version>`, both pointing at the one digest every check ran
against, and a new guard pins the two names together.

> Existing releases have no `v` tag. `v0.15.0` onward will; anything older needs a one-off
> `docker buildx imagetools create --tag …:v0.11.0 …@<digest>` before it can be deployed this way.

**3. `inputs.image_tag` did not exist.** The inputs are `version` (and were `environment`,
`rollback`). It now passes the version the migrations job normalised, so typing `v0.11.0` cannot
become `vv0.11.0`.

## The blocker

**`cd-product.yml` overwrites ClinicQ's settings.** Its *Write production .env* step runs the
gateway's `write-prod-env.sh`, whose key list is shared across every BTK product and contains none
of ClinicQ's ~179 settings. It runs **after** the migrations job composes the real `.env` from
`deploy/env/production.env` plus the SOPS-encrypted half, so it would replace it — and the app would
come up on defaults for every `SMS_*`, `QUEUE_*`, `DISPLAY_*`, `PATIENT_*` and `VAPID_*` value.

That is precisely what [#241](https://github.com/Billykat7/clinicQ/pull/241) built the two-half split
to prevent, two days ago.

It cannot be fixed from inside this repository. **`Billykat7/infra` has to either leave an existing
`.env` alone, or accept a product env hook.** Until it does, this workflow must not deploy anything —
which is why the draft stays a draft, and why the workflow's own header opens with that warning.

## The guards: what changed, and what did not

**Two passed the moment the workflow was correct, with no test edit:** the shared deploy sequence
(the migrations job runs it) and the pinned action (`@b9c3e64…` rather than `@main`).

**Five were rewritten**, each because it specified a step that is now the platform's, and each
keeping the property that outlived the mechanism:

| Guard | Specified | Now specifies |
|---|---|---|
| manual run | `environment.name == inputs.environment` | the gate names `production`, and an `environment` **input** is banned (finding 1) |
| migration order | step index: migrate < candidate < swap | a **job dependency** — `deploy` needs `migrations`. Stronger: a failure means the deploy never starts, rather than a later step being skipped |
| app settings | `APP_ENV` written with `umask 077` | `APP_ENV` was retired by #239, so this passed by finding nothing — a guard that could not fail. Now: no step may print the settings, and `secrets.APP_ENV` may not come back |
| on the host | `jobs.deploy.runs-on` | the same assertion on the job that has steps; the ssh / scp / deploy-key ban is unchanged |
| `DEPLOY_DIR` | `jobs.deploy.env` | the same assertions on the same job — masked, never a step output, no `working-directory` |

**One is new:** the tag the deploy asks GHCR for must be one release.yml publishes (finding 2).

No guard was deleted, and none was weakened to make the workflow pass.

## Verification

- [x] `TZ=UTC pytest tests/unit` — **1383 passed**, 3 xfailed. `tests/unit/platform` is **300
  passed, 0 failed** (was 7 failed).
- [x] `actionlint .github/workflows/*.yml` — clean. The one `SC2086` note is in `ci.yml` on `main`
  already and is untouched here.
- [x] `ruff check` / `ruff format --check` clean.
- [x] `python scripts/update_milestone_progress.py --check --assume-closed 243` — up to date.
- [ ] **Not run:** a deploy. It needs the platform host's runner and the gateway checkout, and it
  must not run at all until the blocker above is resolved.

## Risk and rollback

Nothing on any host changes until someone dispatches a deploy. `main`'s `deploy.yml` (Issue 239) is
a working deploy today, so rollback is a revert.

Closes #243
