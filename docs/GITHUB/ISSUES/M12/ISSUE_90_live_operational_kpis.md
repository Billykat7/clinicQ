# Issue 90: Live operational KPIs on the manager dashboard

**Area:** Frontend / Reporting
**Milestone:** M12 - Reporting, Analytics & District Dashboards
**Owner role:** Data & Research Lead
**Depends on:** Issues 88, 49
**Estimate:** 2 days
**Status:** Planned

## Context

A manager walking past reception should be able to tell in one glance whether today is going well.
These are today's numbers against the clinic's own four-week normal, not a leaderboard, and not a staff
performance monitor.

## Scope

- KPI strip on the dashboard: patients seen today, currently waiting, average wait today, longest current wait, no-shows today
- Comparison against the site's trailing four-week average for the same weekday and hour
- Threshold colouring with a documented, configurable rule
- Auto-refresh on the same live channel as the board
- Explicitly not framed as individual staff performance measurement

## Acceptance criteria

- [ ] KPIs refresh live alongside the board
- [ ] Comparisons use the same weekday and hour, not a flat average
- [ ] Threshold rules are documented and configurable per site
- [ ] The strip fits above the fold at 1366×768
- [ ] No KPI ranks or compares individual staff members
- [ ] Figures reconcile exactly with the M12 reports for the same period

## Files touched

- `app/web/dashboard/kpis.py`
- `app/templates/dashboard/_kpi_strip.html`
- `app/services/reporting.py`

---

**Refs:** [M12 milestone](../../MILESTONES/M12_reporting_analytics.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #90
