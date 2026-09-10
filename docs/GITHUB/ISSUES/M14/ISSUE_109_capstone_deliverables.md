# Issue 109: Capstone deliverables: demo script, video, report, poster, presentation

> **In short:** The capstone submission: the report, the demo video, the presentation and poster, and evidence of what each team member built.

| | |
|---|---|
| **Milestone** | [M14: Production Readiness, Pilot & Go-live](../../MILESTONES/M14_production_pilot_golive.md) |
| **Sprint** | 14 (weeks 27–28) |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Docs / Capstone |
| **Estimate** | 5 days |
| **Status** | Planned |
| **Depends on** | [Issue 100](../M13/ISSUE_100_pen_test_remediation.md): Authorised penetration test of staging and remediation<br>[Issue 105](../M14/ISSUE_105_load_soak_testing.md): Load and soak testing of the morning-rush profile<br>[Issue 108](../M14/ISSUE_108_uat_clinic_staff.md): User acceptance testing with clinic staff and remediation |
| **Unblocks** | No other issue waits on this one. |

## Context

Scheduled as real work with real time, because it carries a large share of the marks and is the thing
student teams most reliably leave until the final week. Every claim in the report should point at
something in this repository.

## Starting point

- Closing this issue is what cuts `v1.0.0`; see the release rules in the [GitHub docs index](../../README.md#release-tags).
- The contribution evidence comes from merged PRs, so the branch and commit conventions (`Issue/<N>/…`, `Issue N: …`) matter from sprint 1.
- The landing page (`src/templates/web/index.html`) and the visual demo (`docs/DEMO/index.html`) are reusable material.

## Scope

- Project report: problem, literature and benchmark, requirements, architecture, implementation, testing, evaluation, reflection
- Demo script and a recorded demo video covering the full loop: discover → join → call → board → notify
- Presentation deck and a rehearsed talk, with timing
- Poster or one-page summary for the showcase
- An individual contribution statement per team member, traceable to issues, branches and pull requests

## Out of scope

- Any new product work: this is write-up and rehearsal only.

## Acceptance criteria

- [ ] The report cites figures reproducible from the repository
- [ ] The demo video shows the complete loop end to end without cuts that hide failures
- [ ] The presentation has been rehearsed at least twice against the clock
- [ ] Each member's contribution statement is evidenced by their merged pull requests
- [ ] The benchmark section positions ClinicQ against real UK and US systems with sources
- [ ] All deliverables are complete and committed before the submission deadline

## How to verify

1. Every figure in the report can be regenerated from the repository (Issue 94's fixtures).
2. The demo video shows discover, join, call, board and notify end to end, without cuts that hide failures.
3. Each member's contribution statement links to their merged PRs.

## Files touched

- `docs/CAPSTONE/REPORT.md`
- `docs/CAPSTONE/DEMO_SCRIPT.md`
- `docs/CAPSTONE/PRESENTATION.md`
- `docs/CAPSTONE/CONTRIBUTIONS.md`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [product docs](../../../PRODUCT/10-business-plan.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #109
