# Issue 90: Live operational KPIs on the manager dashboard

> **In short:** A strip of live numbers at the top of the manager's dashboard showing how today compares with a normal day, without ranking staff.

| | |
|---|---|
| **Milestone** | [M12: Reporting, Analytics & District Dashboards](../../MILESTONES/M12_reporting_analytics.md) |
| **Sprint** | 10 (weeks 19–20) |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Frontend / Reporting |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 49](../M7/ISSUE_49_front_desk_board_live.md): Front-desk board: all active queues, live via SSE with polling fallback<br>[Issue 88](../M12/ISSUE_88_daily_stats_worker.md): Nightly `daily_queue_stats` aggregation worker |
| **Unblocks** | [Issue 94](../M12/ISSUE_94_reporting_contract_accuracy.md): Reporting contract and figure-accuracy tests |

## Context

A manager walking past reception should be able to tell in one glance whether today is going well.
These are today's numbers against the clinic's own four-week normal, not a leaderboard, and not a staff
performance monitor.

## Starting point

- Refreshes on the same live channel as the board (Issue 49).
- Use the same reporting service as Issue 89, so the numbers always reconcile.

## Scope

- KPI strip on the dashboard: patients seen today, currently waiting, average wait today, longest current wait, no-shows today
- Comparison against the site's trailing four-week average for the same weekday and hour
- Threshold colouring with a documented, configurable rule
- Auto-refresh on the same live channel as the board
- Explicitly not framed as individual staff performance measurement

## Out of scope

- Historical reports (Issue 89).
- Any per-staff performance measure (explicitly excluded).

## Acceptance criteria

- [ ] KPIs refresh live alongside the board
- [ ] Comparisons use the same weekday and hour, not a flat average
- [ ] Threshold rules are documented and configurable per site
- [ ] The strip fits above the fold at 1366×768
- [ ] No KPI ranks or compares individual staff members
- [ ] Figures reconcile exactly with the M12 reports for the same period

## How to verify

1. Call and complete tickets: the strip updates live.
2. Compare today's KPI with the same period in the reports page: identical.
3. View at 1366×768: the strip sits above the fold.

## Files touched

- `src/web/dashboard/kpis.py`
- `src/templates/dashboard/_kpi_strip.html`
- `src/modules/reporting/service.py`

---

**Refs:** [M12 milestone](../../MILESTONES/M12_reporting_analytics.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #90
