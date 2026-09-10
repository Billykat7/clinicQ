# Issue 89: Clinic reports UI: wait time, no-show, channel mix, heatmap

> **In short:** Clinic managers see their own wait times, no-shows, channel mix and busiest hours, the report that shows the product is worth keeping.

| | |
|---|---|
| **Milestone** | [M12: Reporting, Analytics & District Dashboards](../../MILESTONES/M12_reporting_analytics.md) |
| **Sprint** | 10 (weeks 19–20) |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Frontend / Reporting |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 48](../M7/ISSUE_48_dashboard_shell_role_nav.md): Dashboard shell, role-aware navigation and site switcher<br>[Issue 88](../M12/ISSUE_88_daily_stats_worker.md): Nightly `daily_queue_stats` aggregation worker |
| **Unblocks** | [Issue 91](../M12/ISSUE_91_exports_scheduled_reports.md): CSV/PDF export and scheduled weekly email report<br>[Issue 94](../M12/ISSUE_94_reporting_contract_accuracy.md): Reporting contract and figure-accuracy tests |

> **Note:** The sprint plan has C building UI on this; F owns the metrics.

## Context

The screen that sells the product. A clinic manager should see their busiest hour, their no-show rate
and how patients are reaching them within five seconds of opening the page; the queue-length heatmap in
particular is the single most persuasive chart in the whole system.

## Starting point

- CSP blocks CDN scripts, so a chart library must be vendored under `src/static/vendor/` like htmx and Leaflet.
- Every figure comes from `daily_queue_stats` (Issue 88) through the reporting service, never from ad-hoc queries in the page.

## Scope

- Reports page: average wait over time, no-show rate, channel mix, queue-length heatmap by hour and weekday
- Chart.js visualisations with accessible colour palettes and data tables behind every chart
- Date-range selector with sensible presets (today, this week, last 30 days)
- Per-queue drill-down from any chart
- Comparison against the site's own previous period, never against other clinics

## Out of scope

- Exports (Issue 91) and live KPIs (Issue 90).
- Comparing one clinic with another (district view only, Issue 92).

## Acceptance criteria

- [ ] The busiest hour is obvious from the heatmap within 5 seconds of looking
- [ ] Every chart has an accessible data-table equivalent
- [ ] Reports load in under 2 seconds for a 30-day range
- [ ] Charts are readable in greyscale and by a colour-blind viewer
- [ ] Drill-down preserves the selected date range
- [ ] A clinic sees only its own data, enforced by the site-scoping guard

## How to verify

1. Open the heatmap for the seeded clinic: the busiest hour is obvious at a glance.
2. Load 30 days: under 2 seconds.
3. Switch the display to greyscale: every chart is still readable, and each has a data table.

## Files touched

- `src/web/dashboard/reports.py`
- `src/templates/dashboard/reports.html`
- `src/static/js/reports-charts.js`
- `src/static/vendor/`

---

**Refs:** [M12 milestone](../../MILESTONES/M12_reporting_analytics.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #89
