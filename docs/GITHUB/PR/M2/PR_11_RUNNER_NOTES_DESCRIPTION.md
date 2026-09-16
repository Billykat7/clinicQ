# PR: Say when ClinicQ would want a self-hosted runner, and how to install one that survives a reboot (Issue 11 follow-up / M2-11)

**Milestone:** [Milestone 2: CI/CD, Environments & Team Workflow](https://github.com/Billykat7/clinicQ/milestone/2) ·
**Issue:** [#11](https://github.com/Billykat7/clinicQ/issues/11) (CD: staging on tag, production behind approval), closed by PR #124

`docs/GITHUB/RUNNER/` held a 35-line sketch that said a runner should exist and gave five bullet
points of setup. Two things were wrong with it. It was not enough to install anything — no service
unit, no verification, nothing about the runner a host may already be running — and its **reason was
false**: it opened by saying deploys run self-hosted "because self-hosted minutes are not billed",
when nothing in this repository runs on a self-hosted runner and, the repository being public,
GitHub bills no minutes at all. This PR replaces it with the procedure from BK Properties
(`properties/docs/GITHUB/RUNNER/`), adapted to what ClinicQ actually does, and adds the
shared-host companion that folder has and this one did not.

## Scope

- **In:** `docs/GITHUB/RUNNER/README.md` (rewritten), `docs/GITHUB/RUNNER/clinicq.md` (new), and the
  two places `docs/GITHUB/README.md` describes them.
- **Out:** no workflow changes. `deploy.yml` still runs on `ubuntu-24.04` and still reaches the host
  over SSH. Whether ClinicQ ever registers a runner is [Issue 102](../../ISSUES/M14/ISSUE_102_production_infra_tls.md)'s
  decision; this PR only writes down how, and when it would be worth it.

## Summary

- **`RUNNER/README.md`, the procedure:** why a systemd service and not `./run.sh` (a table of what
  survives logout and reboot), prerequisites, download with a pinned SHA-256, `config.sh` with
  labels, `svc.sh install` + `start`, verification, day-2 operations, removal, safety notes and a
  troubleshooting table.
- **The rationale, corrected.** The doc now leads with where ClinicQ stands: every job runs on
  `ubuntu-24.04`, the deploy reaches its host over SSH with the environment's `DEPLOY_*` secrets, and
  a runner would be an *alternative* to that hop rather than an addition to it. A runner buys
  **reachability** — a host behind a firewall that cannot accept an inbound session from GitHub's
  ranges — not minutes.
- **A new section 4, "Point the deploy at it",** because the `runs-on:` line is the easy half. It
  names the two things that must move with it: `scripts/cd/deploy.sh` runs locally so `DEPLOY_DIR`
  becomes a path on the runner's own filesystem, and the four SSH secrets stop being read and should
  be deleted rather than left live. It also sends the reader to `./scripts/ci-local.sh`, because
  `test_workflow_guardrails.py` reads the workflow files.
- **The fork warning, sharpened.** The old note said never enable a runner for fork pull requests.
  It now says why that bites *here*: the repository is public, so anyone can open one. Keep CI
  GitHub-hosted, put only the deploy on the runner, leave external-contributor approval on.
- **`RUNNER/clinicq.md`** (new, the analogue of properties' `properties.md`): installing ClinicQ's
  runner into `~/actions-clinicq` on a host that already runs another one — check what is there
  first, then a distinct directory, `--name clinicq-runner`, label `clinicq-deploy`, and its own
  systemd unit. It ends with the case this project actually has: staging and production are separate
  compose projects on different `APP_PORT`s, so **one** runner serves both, and a second is needed
  only when they live on different hosts.
- **`docs/GITHUB/README.md`:** the tree names both files instead of `RUNNER/  # self-hosted runner
  notes`, and "Pipeline strategy" gained a paragraph pointing at them with the same
  reachability-not-minutes framing.

## Design notes

**Adapted, not copied.** BK Properties is private, deploys through infra's `cd-product.yml` on the
shared platform runner, and pins runner 2.335.1. Copying that file would have imported three claims
that are false here. What carried over is the shape — service install, verify, day-2, remove,
troubleshoot, plus a per-repository companion — and what changed is every fact underneath it: the
repository URL and service name (`actions.runner.Billykat7-clinicQ.clinicq-runner.service`), the
label (`clinicq-deploy`), the version and digest (this repository's own pin, below), the deploy
user, and the minutes argument.

**Every claim is sourced to something in the repository,** so the next person can check it rather
than trust it: the runner version and SHA-256 to `scripts/cd/server-initial-setup.sh`, the billing
statement to `docs/CICD/PIPELINES.md`, the `DEPLOY_*` secrets and the deploy user to
`docs/CICD/RUNBOOK_DEPLOY.md`, and the `/opt/btk/clinicq` paths to the same runbook's host setup.

**The deploy user is named twice on purpose.** The runbook's host uses `deploy`;
`scripts/cd/server-initial-setup.sh` creates `deployer`. Rather than pick one and be wrong on half
the hosts, the prerequisite says use whichever the host has, and states the part that is not
negotiable: not root, and in the `docker` group.

## Changes

- **`docs/GITHUB/RUNNER/README.md`:** 35 lines → 286. Rewritten as above.
- **`docs/GITHUB/RUNNER/clinicq.md`** (new, 145 lines): the second-runner-on-a-shared-host install.
- **`docs/GITHUB/README.md`:** the folder tree lists both RUNNER files; "Pipeline strategy" gains a
  paragraph on where jobs run and what would change that.

## Testing

Docs only, so the evidence is that the facts are true and the links land.

**The repository is public and bills nothing** (the doc's minutes claim, and `PIPELINES.md`'s):

```text
$ gh api repos/Billykat7/clinicQ --jq '.visibility'
public
$ gh api repos/Billykat7/clinicQ/actions/runs/35069110064/timing --jq '.billable'
{"UBUNTU":{"job_runs":[{"duration_ms":0,...}],"jobs":1,"total_ms":0}}
```

**The pinned digest is upstream's**, not a copied-forward guess — `actions/runner`'s own release
notes for v2.332.0, the version `scripts/cd/server-initial-setup.sh` defaults to:

```text
$ gh api repos/actions/runner/releases/tags/v2.332.0 --jq '.body' | grep linux-x64-2.332.0
- actions-runner-linux-x64-2.332.0.tar.gz <!-- BEGIN SHA linux-x64 -->f2094522a6b9afeab07ffb586d1eb3f190b6457074282796c497ce7dce9e0f2a<!-- END SHA linux-x64 -->
```

Upstream is on v2.337.0 today, which is why both files tell the reader to take the version *and* the
SHA-256 from GitHub's "New self-hosted runner" page when it offers a newer one.

**Every job really does run GitHub-hosted** (the doc's opening claim):

```text
$ grep -h "runs-on" .github/workflows/*.yml | sort -u
    runs-on: ubuntu-24.04
```

**Links and heading anchors resolve** — every relative link in the three files, and the three
anchored ones (`README.md#4-point-the-deploy-at-it`,
`RUNBOOK_DEPLOY.md#setting-up-a-host-once-per-environment`,
`PIPELINES.md#minutes-measured-and-projected-against-2000-a-month`), checked by resolving each path
and slugifying each target file's headings:

```text
link check done            (no MISSING lines)
OK   docs/GITHUB/RUNNER/README.md 4-point-the-deploy-at-it
OK   docs/CICD/RUNBOOK_DEPLOY.md setting-up-a-host-once-per-environment
OK   docs/CICD/PIPELINES.md minutes-measured-and-projected-against-2000-a-month
```

`make milestone-progress`: `14 milestone(s): up to date`. This follow-up closes nothing, so it
assumes no issue closed.

- [x] Facts checked against the repository and against upstream, as above
- [x] Links and anchors resolve
- [x] CI green on the pull request: all ten checks pass, including `Conventions`
      ([run 35071125042](https://github.com/Billykat7/clinicQ/actions/runs/35071125042)). The full
      suite ran rather than the prose fast path: the three documentation files are in `ci.yml`'s
      `PROSE_PATHS`, but `docs/GITHUB/PR/` is not, so this file makes every pull request that
      carries its own description a full run.
- [ ] `./scripts/ci-local.sh` — not run locally: no code, workflow or configuration file changed,
      and CI ran the same suite above.
- [ ] **Not checked by hand:** nobody installed a runner from these steps. There is no host to
      install one on, and the repository needs none today. The commands are the ones
      `scripts/cd/server-initial-setup.sh` already runs unattended, plus GitHub's documented
      `svc.sh` interface.

## Acceptance criteria

Issue 11's criteria were met by PR #124 and are unchanged by this PR: it adds no behaviour. The
standard it is held to instead is the one the old file failed — a reader with a host and a token can
follow it end to end, and nothing in it is untrue.

## Risk and rollback

- **Docs only.** No code, workflow, migration or configuration change; rollback is a revert.
- **The stated risk is staleness**: if `deploy.yml` ever moves onto a runner, the "Where ClinicQ
  stands today" section and section 4 both describe a past. Section 4 is written as the change to
  make, so it becomes the record of what was done.
- **The version pin trails upstream** by design, matching `scripts/cd/server-initial-setup.sh`. Both
  files say to prefer the release GitHub's own page offers, with its digest.

Refs #11
