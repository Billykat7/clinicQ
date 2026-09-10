# Issue 107: Support process, incident runbooks and internal SLA

> **In short:** Clinic staff know exactly how to get help and how fast, and every incident makes the product better rather than just getting patched.

| | |
|---|---|
| **Milestone** | [M14: Production Readiness, Pilot & Go-live](../../MILESTONES/M14_production_pilot_golive.md) |
| **Sprint** | 13 (weeks 25–26); the sprint plan puts this in **F**'s lane, see the note below |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Ops / Pilot |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 104](../M14/ISSUE_104_monitoring_alerting_status.md): Monitoring, alerting, on-call rota and status page<br>[Issue 106](../M14/ISSUE_106_pilot_rollout_kit.md): Pilot rollout kit: site survey, install guide, staff training pack |
| **Unblocks** | [Issue 108](../M14/ISSUE_108_uat_clinic_staff.md): User acceptance testing with clinic staff and remediation |

## Context

During a pilot the team is the support desk. Agreeing response times and writing down who does what
before the first incident is what stops a Tuesday-morning outage becoming six people improvising in a
group chat.

## Starting point

- Uses the alerting from Issue 104. The runbooks cover the four likeliest failures named in the scope.

## Scope

- Support channel for clinic staff: phone or WhatsApp, with published hours
- Severity definitions and target response times, agreed with the pilot clinic
- Incident runbooks for the most likely failures: board dark, dashboard unreachable, notifications not arriving, queue stuck
- Escalation path and a post-incident review template
- An issue-triage process feeding real problems back into the backlog

## Out of scope

- Monitoring configuration (Issue 104).

## Acceptance criteria

- [ ] Clinic staff know exactly how to reach the team and within what hours
- [ ] Severity definitions and response times are agreed in writing with the clinic
- [ ] Each runbook has been walked through by someone who did not write it
- [ ] Every incident gets a written post-incident review
- [ ] Incidents produce backlog issues, not only fixes
- [ ] The rota covers the whole pilot period with named people

## How to verify

1. The pilot clinic signs off the severity levels and response times in writing.
2. A teammate who did not write each runbook walks through it successfully.
3. Raise a practice incident: a post-incident review and at least one backlog issue come out of it.

## Files touched

- `docs/OPS/SUPPORT.md`
- `docs/OPS/RUNBOOK_INCIDENTS.md`
- `docs/OPS/POSTMORTEM_TEMPLATE.md`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [product docs](../../../PRODUCT/10-business-plan.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #107
