# Issue 14: Monitoring baseline: uptime checks, error tracking, deploy notifications

> **In short:** When staging or production breaks, the team hears about it within five minutes, from a message that says who should act.

| | |
|---|---|
| **Milestone** | [M2: CI/CD, Environments & Team Workflow](../../MILESTONES/M2_cicd_environments.md) |
| **Sprint** | 3 (weeks 5–6) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Infra / Observability |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | [Issue 11](../M2/ISSUE_11_cd_staging_prod_approval.md): CD: staging deploy on tag, production behind manual approval |
| **Unblocks** | [Issue 104](../M14/ISSUE_104_monitoring_alerting_status.md): Monitoring, alerting, on-call rota and status page |

## Context

Knowing that staging is down only when a teammate tries to demo it is not monitoring. This issue puts
the minimum in place early: uptime checks, error capture and deploy notifications, so the team learns
the habit long before a real clinic depends on it.

## Starting point

- Liveness and readiness probes already exist (`/health`, `/health/ready`), and logs ship to S3 (`src/core/s3_logging.py`).
- There is no error tracking or uptime monitoring yet.

## Scope

- Uptime monitoring on `/health` for staging and production with alerting to the team channel
- Error tracking capturing unhandled exceptions with request context and a release tag
- Deploy notifications posting version, environment and result
- A simple `/metrics` endpoint or log-derived counters for request rate, latency and error rate
- A one-page runbook: what each alert means and who responds

## Out of scope

- Production-grade dashboards and on-call rotation (Issue 104).
- Load testing (Issue 105).

## Acceptance criteria

- [ ] An intentional staging outage raises an alert within 5 minutes
- [ ] An unhandled exception appears in error tracking with request id and release tag
- [ ] Alerts name a responsible role, not just a URL
- [ ] Deploy notifications include the version and a link to the release notes
- [ ] No alert fires more than once per incident (deduplicated)
- [ ] The runbook is short enough that a teammate can act on it at 07:30 without help

## How to verify

1. Stop the staging app: an alert naming the responsible role arrives within 5 minutes, once.
2. Raise an exception from a test route on staging: it shows up in error tracking with the request id and release tag.
3. Deploy a tag: the team channel gets the version and a link to its release notes.

## Files touched

- `infra/monitoring/`
- `src/core/telemetry.py`
- `docs/CICD/RUNBOOK_ALERTS.md`

---

**Refs:** [M2 milestone](../../MILESTONES/M2_cicd_environments.md) · [product docs](../../../PRODUCT/08-topology.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #14
