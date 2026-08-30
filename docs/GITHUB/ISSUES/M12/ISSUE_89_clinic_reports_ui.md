# Issue 89: Clinic reports UI: wait time, no-show, channel mix, heatmap

**Area:** Frontend / Reporting
**Milestone:** M12 - Reporting, Analytics & District Dashboards
**Owner role:** Data & Research Lead
**Depends on:** Issues 88, 48
**Estimate:** 3 days
**Status:** Planned

## Context

The screen that sells the product. A clinic manager should see their busiest hour, their no-show rate
and how patients are reaching them within five seconds of opening the page; the queue-length heatmap in
particular is the single most persuasive chart in the whole system.

## Scope

- Reports page: average wait over time, no-show rate, channel mix, queue-length heatmap by hour and weekday
- Chart.js visualisations with accessible colour palettes and data tables behind every chart
- Date-range selector with sensible presets (today, this week, last 30 days)
- Per-queue drill-down from any chart
- Comparison against the site's own previous period, never against other clinics

## Acceptance criteria

- [ ] The busiest hour is obvious from the heatmap within 5 seconds of looking
- [ ] Every chart has an accessible data-table equivalent
- [ ] Reports load in under 2 seconds for a 30-day range
- [ ] Charts are readable in greyscale and by a colour-blind viewer
- [ ] Drill-down preserves the selected date range
- [ ] A clinic sees only its own data, enforced by the site-scoping guard

## Files touched

- `app/web/dashboard/reports.py`
- `app/templates/dashboard/reports.html`
- `app/static/js/charts.js`

---

**Refs:** [M12 milestone](../../MILESTONES/M12_reporting_analytics.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #89
