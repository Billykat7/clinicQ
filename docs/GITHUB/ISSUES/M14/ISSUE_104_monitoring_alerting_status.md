# Issue 104: Monitoring, alerting, on-call rota and status page

> **In short:** During the pilot, a named person is paged within five minutes of anything important breaking, and clinics can see the service status themselves.

| | |
|---|---|
| **Milestone** | [M14: Production Readiness, Pilot & Go-live](../../MILESTONES/M14_production_pilot_golive.md) |
| **Sprint** | 13 (weeks 25–26) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Infra / Production |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 14](../M2/ISSUE_14_monitoring_baseline_alerts.md): Monitoring baseline: uptime checks, error tracking, deploy notifications<br>[Issue 102](../M14/ISSUE_102_production_infra_tls.md): Production infrastructure, TLS, domains and edge protection |
| **Unblocks** | [Issue 107](../M14/ISSUE_107_support_incident_sla.md): Support process, incident runbooks and internal SLA |

## Context

During a pilot the team finds out about problems either from monitoring or from an angry phone call.
This issue makes the first one reliable, including the display-box heartbeat, because a dark screen in a
waiting room is an outage nobody else will report.

## Starting point

- Builds on Issue 14's uptime and error tracking. Logs already ship to S3 and are searchable in the kernel's log console at `/logs`.
- Display-box heartbeats come from Issue 61.

## Scope

- Uptime, latency and error-rate monitoring with dashboards for API, database, Redis and workers
- Alert rules with severities and an on-call rota naming a person per week
- Display-device heartbeat alerting from Issue 61 wired into the same system
- A public status page for clinics
- Log aggregation with retention and a search interface

## Out of scope

- The support process and incident runbooks (Issue 107).

## Acceptance criteria

- [ ] An outage pages the on-call team member within 5 minutes
- [ ] A dark display box raises an alert naming the clinic
- [ ] The status page updates during an incident
- [ ] Alerts are actionable and do not fire spuriously: a week of quiet operation produces no false pages
- [ ] Logs are searchable across services for a full incident reconstruction
- [ ] The on-call rota is agreed by the whole team and published

## How to verify

1. Take production down in a drill: the on-call person is paged within 5 minutes.
2. Unplug a display box: an alert names the clinic.
3. Run a quiet week: no false pages.

## Files touched

- `infra/monitoring/`
- `docs/OPS/ONCALL.md`
- `docs/OPS/RUNBOOK_INCIDENTS.md`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #104
