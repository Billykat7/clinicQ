# Issue 13: Team workflow: branch protection, PR/issue templates, CODEOWNERS, labels sync

> **In short:** The rules of the road for six people on one repo: protected `main`, templates that ask the right questions, and an owner for every folder.

| | |
|---|---|
| **Milestone** | [M2: CI/CD, Environments & Team Workflow](../../MILESTONES/M2_cicd_environments.md) |
| **Sprint** | 3 (weeks 5–6) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Process / Team |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | [Issue 9](../M2/ISSUE_9_actions_ci_lint_type_test.md): GitHub Actions CI: lint, type-check, test with PostGIS + Redis services |
| **Unblocks** | No other issue waits on this one. |

## Context

This is the anti-blocking infrastructure. CODEOWNERS routes each review to the person who owns that
module, so a pull request never sits waiting on whoever happens to be online; the templates make an
issue or a PR reviewable without a conversation.

## Starting point

- Labels are already defined in `docs/GITHUB/LABELS/labels.yml` and sync with `make gh-sync-labels`.
- A PR description template lives at `docs/GITHUB/PR/PR_TEMPLATE.md`; the `.github/` copy GitHub reads does not exist yet.
- The ownership map to encode in `CODEOWNERS` is section 2 of the [workload split](../../../TEAM/WORKLOAD_SPLIT.md#2-ownership-map-codeowners).

## Scope

- Branch protection on `main`: no direct pushes, one approving review, CI green required
- Branch naming `Issue/<N>/<short-slug>` and commit convention `Issue N: <imperative summary>`
- Pull-request template requiring scope, screenshots for UI work, test evidence and `Closes #N`
- Issue templates for feature, bug and spike
- `CODEOWNERS` mapping each package to its owning role, and `LABELS/labels.yml` synced to GitHub

## Out of scope

- The CI workflow that branch protection requires (Issue 9).

## Acceptance criteria

- [ ] A direct push to `main` is rejected
- [ ] A pull request cannot merge without CI green and one approval
- [ ] Every module path has exactly one code owner, with a documented backup reviewer
- [ ] New issues open with the template pre-filled
- [ ] `labels.yml` is the single source of truth and syncs cleanly to the repository
- [ ] The whole team has walked through one issue end to end using this workflow

## How to verify

1. `git push origin main` directly: GitHub rejects it.
2. Open a PR without an approval: it cannot merge.
3. Open a new issue: the template is pre-filled.
4. Walk one real issue from branch to merge as a team, and note anything confusing in the PR.

## Files touched

- `.github/CODEOWNERS`
- `.github/pull_request_template.md`
- `.github/ISSUE_TEMPLATE/`
- `docs/GITHUB/LABELS/labels.yml`

---

**Refs:** [M2 milestone](../../MILESTONES/M2_cicd_environments.md) · [team workflow](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #13
