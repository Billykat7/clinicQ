# Issue 108: User acceptance testing with clinic staff and remediation

> **In short:** Real clinic staff use ClinicQ for real, the team watches where they struggle, and fixes it before calling the product done.

| | |
|---|---|
| **Milestone** | [M14: Production Readiness, Pilot & Go-live](../../MILESTONES/M14_production_pilot_golive.md) |
| **Sprint** | 14 (weeks 27–28) |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | QA / Pilot |
| **Estimate** | 4 days |
| **Status** | Planned |
| **Depends on** | [Issue 106](../M14/ISSUE_106_pilot_rollout_kit.md): Pilot rollout kit: site survey, install guide, staff training pack<br>[Issue 107](../M14/ISSUE_107_support_incident_sla.md): Support process, incident runbooks and internal SLA |
| **Unblocks** | [Issue 109](../M14/ISSUE_109_capstone_deliverables.md): Capstone deliverables: demo script, video, report, poster, presentation |

## Context

The moment the project stops being a university exercise. Real receptionists and nurses using the
system for a real morning will find things no test suite does, and the evidence that their feedback
changed the product is itself a strong capstone artefact.

## Starting point

- Every role's scenarios come from the acceptance criteria already in these specs; UAT checks them with real people, not developers.

## Scope

- UAT plan with scripted scenarios per role: receptionist, nurse, manager, patient
- Observed sessions with real staff, with time-on-task and error counts recorded
- A feedback log with severity, and a triage decision for each item
- Fixes shipped for every blocking and high-severity item, then re-tested
- A before-and-after write-up of what changed as a result

## Out of scope

- The capstone write-up (Issue 109), which reports on this.

## Acceptance criteria

- [ ] Every role has completed its scenarios with real staff
- [ ] The feedback log is complete, triaged and public to the team
- [ ] Every blocking issue is fixed and re-tested with the same staff
- [ ] Time-on-task for walk-in intake meets the target set in Issue 51
- [ ] At least one real clinic has run a full day on ClinicQ
- [ ] The write-up shows specific changes traced to specific feedback

## How to verify

1. `UAT_FEEDBACK_LOG.md` has a triage decision for every item.
2. Every blocking item is fixed and retested with the same staff member.
3. At least one clinic runs a full day on ClinicQ, recorded in `UAT_REPORT.md`.

## Files touched

- `docs/QA/UAT_PLAN.md`
- `docs/QA/UAT_FEEDBACK_LOG.md`
- `docs/QA/UAT_REPORT.md`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [product docs](../../../PRODUCT/10-business-plan.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #108
