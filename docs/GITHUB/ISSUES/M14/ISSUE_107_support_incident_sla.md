# Issue 107: Support process, incident runbooks and internal SLA

**Area:** Ops / Pilot
**Milestone:** M14 - Production Readiness, Pilot & Go-live
**Owner role:** DevOps/QA Lead
**Depends on:** Issues 104, 106
**Estimate:** 2 days
**Status:** Planned

## Context

During a pilot the team is the support desk. Agreeing response times and writing down who does what
before the first incident is what stops a Tuesday-morning outage becoming six people improvising in a
group chat.

## Scope

- Support channel for clinic staff: phone or WhatsApp, with published hours
- Severity definitions and target response times, agreed with the pilot clinic
- Incident runbooks for the most likely failures: board dark, dashboard unreachable, notifications not arriving, queue stuck
- Escalation path and a post-incident review template
- An issue-triage process feeding real problems back into the backlog

## Acceptance criteria

- [ ] Clinic staff know exactly how to reach the team and within what hours
- [ ] Severity definitions and response times are agreed in writing with the clinic
- [ ] Each runbook has been walked through by someone who did not write it
- [ ] Every incident gets a written post-incident review
- [ ] Incidents produce backlog issues, not only fixes
- [ ] The rota covers the whole pilot period with named people

## Files touched

- `docs/OPS/SUPPORT.md`
- `docs/OPS/RUNBOOK_INCIDENTS.md`
- `docs/OPS/POSTMORTEM_TEMPLATE.md`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [product docs](../../../PRODUCT/10-business-plan.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #107
