# Issue 104: Monitoring, alerting, on-call rota and status page

**Area:** Infra / Production
**Milestone:** M14 - Production Readiness, Pilot & Go-live
**Owner role:** DevOps/QA Lead
**Depends on:** Issues 14, 102
**Estimate:** 2 days
**Status:** Planned

## Context

During a pilot the team finds out about problems either from monitoring or from an angry phone call.
This issue makes the first one reliable, including the display-box heartbeat, because a dark screen in a
waiting room is an outage nobody else will report.

## Scope

- Uptime, latency and error-rate monitoring with dashboards for API, database, Redis and workers
- Alert rules with severities and an on-call rota naming a person per week
- Display-device heartbeat alerting from Issue 61 wired into the same system
- A public status page for clinics
- Log aggregation with retention and a search interface

## Acceptance criteria

- [ ] An outage pages the on-call team member within 5 minutes
- [ ] A dark display box raises an alert naming the clinic
- [ ] The status page updates during an incident
- [ ] Alerts are actionable and do not fire spuriously: a week of quiet operation produces no false pages
- [ ] Logs are searchable across services for a full incident reconstruction
- [ ] The on-call rota is agreed by the whole team and published

## Files touched

- `infra/monitoring/`
- `docs/OPS/ONCALL.md`
- `docs/OPS/RUNBOOK_INCIDENTS.md`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #104
