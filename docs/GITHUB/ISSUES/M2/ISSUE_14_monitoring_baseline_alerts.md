# Issue 14: Monitoring baseline: uptime checks, error tracking, deploy notifications

**Area:** Infra / Observability
**Milestone:** M2 - CI/CD, Environments & Team Workflow
**Owner role:** DevOps/QA Lead
**Depends on:** Issue 11
**Estimate:** 1 day
**Status:** Planned

## Context

Knowing that staging is down only when a teammate tries to demo it is not monitoring. This issue puts
the minimum in place early: uptime checks, error capture and deploy notifications, so the team learns
the habit long before a real clinic depends on it.

## Scope

- Uptime monitoring on `/health` for staging and production with alerting to the team channel
- Error tracking capturing unhandled exceptions with request context and a release tag
- Deploy notifications posting version, environment and result
- A simple `/metrics` endpoint or log-derived counters for request rate, latency and error rate
- A one-page runbook: what each alert means and who responds

## Acceptance criteria

- [ ] An intentional staging outage raises an alert within 5 minutes
- [ ] An unhandled exception appears in error tracking with request id and release tag
- [ ] Alerts name a responsible role, not just a URL
- [ ] Deploy notifications include the version and a link to the release notes
- [ ] No alert fires more than once per incident (deduplicated)
- [ ] The runbook is short enough that a teammate can act on it at 07:30 without help

## Files touched

- `infra/monitoring/`
- `app/core/telemetry.py`
- `docs/CICD/RUNBOOK_ALERTS.md`

---

**Refs:** [M2 milestone](../../MILESTONES/M2_cicd_environments.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #14
