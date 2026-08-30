# Issue 108: User acceptance testing with clinic staff and remediation

**Area:** QA / Pilot
**Milestone:** M14 - Production Readiness, Pilot & Go-live
**Owner role:** Data & Research Lead
**Depends on:** Issues 106, 107
**Estimate:** 4 days
**Status:** Planned

## Context

The moment the project stops being a university exercise. Real receptionists and nurses using the
system for a real morning will find things no test suite does, and the evidence that their feedback
changed the product is itself a strong capstone artefact.

## Scope

- UAT plan with scripted scenarios per role: receptionist, nurse, manager, patient
- Observed sessions with real staff, with time-on-task and error counts recorded
- A feedback log with severity, and a triage decision for each item
- Fixes shipped for every blocking and high-severity item, then re-tested
- A before-and-after write-up of what changed as a result

## Acceptance criteria

- [ ] Every role has completed its scenarios with real staff
- [ ] The feedback log is complete, triaged and public to the team
- [ ] Every blocking issue is fixed and re-tested with the same staff
- [ ] Time-on-task for walk-in intake meets the target set in Issue 51
- [ ] At least one real clinic has run a full day on ClinicQ
- [ ] The write-up shows specific changes traced to specific feedback

## Files touched

- `docs/QA/UAT_PLAN.md`
- `docs/QA/UAT_FEEDBACK_LOG.md`
- `docs/QA/UAT_REPORT.md`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [product docs](../../../PRODUCT/10-business-plan.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #108
