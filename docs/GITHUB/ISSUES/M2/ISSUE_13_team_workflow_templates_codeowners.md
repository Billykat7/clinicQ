# Issue 13: Team workflow: branch protection, PR/issue templates, CODEOWNERS, labels sync

**Area:** Process / Team
**Milestone:** M2 - CI/CD, Environments & Team Workflow
**Owner role:** DevOps/QA Lead
**Depends on:** Issue 9
**Estimate:** 1 day
**Status:** Planned

## Context

This is the anti-blocking infrastructure. CODEOWNERS routes each review to the person who owns that
module, so a pull request never sits waiting on whoever happens to be online; the templates make an
issue or a PR reviewable without a conversation.

## Scope

- Branch protection on `main`: no direct pushes, one approving review, CI green required
- Branch naming `Issue/<N>/<short-slug>` and commit convention `Issue N: <imperative summary>`
- Pull-request template requiring scope, screenshots for UI work, test evidence and `Closes #N`
- Issue templates for feature, bug and spike
- `CODEOWNERS` mapping each package to its owning role, and `LABELS/labels.yml` synced to GitHub

## Acceptance criteria

- [ ] A direct push to `main` is rejected
- [ ] A pull request cannot merge without CI green and one approval
- [ ] Every module path has exactly one code owner, with a documented backup reviewer
- [ ] New issues open with the template pre-filled
- [ ] `labels.yml` is the single source of truth and syncs cleanly to the repository
- [ ] The whole team has walked through one issue end to end using this workflow

## Files touched

- `.github/CODEOWNERS`
- `.github/pull_request_template.md`
- `.github/ISSUE_TEMPLATE/*.yml`
- `docs/GITHUB/LABELS/labels.yml`

---

**Refs:** [M2 milestone](../../MILESTONES/M2_cicd_environments.md) · [team workflow](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #13
