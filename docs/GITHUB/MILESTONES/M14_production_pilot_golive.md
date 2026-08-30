# Milestone 14: Production Readiness, Pilot & Go-live

**Status:** 📋 planned · **Phase:** Semester 2 · Sprint 13–14 · **Suggested tag:** `v0.14.0`, then **`v1.0.0`** when Issue 109 closes
**Primary owner:** DevOps/QA Lead · whole team
**Depends on:** M7, M8, M9, M13
**Blocks:** None

## Goal

Take ClinicQ from a working codebase to a system running in a real clinic with real patients, and produce the capstone deliverables that prove it.

## Why this milestone exists

A capstone is marked on demonstrated, defensible delivery, not on a repository. This milestone
covers the difference: production infrastructure with backups that have actually been restored,
monitoring that pages a human, a load test that proves the 07:30 rush is survivable, an install kit
and training pack a clinic can follow without the team present, and a documented UAT round with real
staff whose feedback visibly changed the product.

The last issue is the report, the demo video and the presentation. It is scheduled as work, with time
reserved, because it is worth a large share of the marks and is the thing student teams most reliably
leave until the final week.

## Scope

- Production infrastructure: VPS, managed PostgreSQL with PostGIS, Redis, TLS, domain, WAF/rate limit at the edge
- Backups with an actually-performed restore drill and a written DR runbook
- Monitoring, alerting, on-call rota and a public status page
- Load and soak test modelling a 07:30 clinic rush, with a documented performance budget
- Pilot rollout kit: site survey checklist, kiosk install guide, staff training pack, printed fallback procedure
- Support process, incident runbooks and an internal SLA
- User acceptance testing with real clinic staff, logged feedback and shipped fixes
- Capstone deliverables: demo script, demo video, project report, poster and presentation

## Issues

| # | Title |
|---|-------|
| 102 | Production infrastructure, TLS, domains and edge protection |
| 103 | Backups, restore drill and disaster-recovery runbook |
| 104 | Monitoring, alerting, on-call rota and status page |
| 105 | Load and soak testing of the morning-rush profile |
| 106 | Pilot rollout kit: site survey, install guide, staff training pack |
| 107 | Support process, incident runbooks and internal SLA |
| 108 | User acceptance testing with clinic staff and remediation |
| 109 | Capstone deliverables: demo script, video, report, poster, presentation |

## Exit criteria

- [ ] The production stack is reachable over HTTPS on the project domain with automated certificate renewal
- [ ] A database restore has been performed end-to-end and timed, not merely configured
- [ ] An outage pages a named on-call team member within 5 minutes
- [ ] The system sustains the modelled morning rush within the performance budget with headroom
- [ ] A clinic can install a kiosk from the written guide without a team member on site
- [ ] At least one real clinic has run a full day on ClinicQ, and the UAT feedback log shows what changed as a result
- [ ] Report, video, poster and presentation are complete and rehearsed before the submission deadline

---

**Navigation:** [GitHub docs index](../README.md) · [Implementation plan](../../PLAN/IMPLEMENTATION_PLAN.md) · [Workload split](../../TEAM/WORKLOAD_SPLIT.md) · [Issues](../ISSUES/M14/)
