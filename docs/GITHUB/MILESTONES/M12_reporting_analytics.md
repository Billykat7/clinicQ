# Milestone 12: Reporting, Analytics & District Dashboards

**Status:** 📋 planned · **Phase:** Semester 2 · Sprint 12 · **Suggested tag:** `v0.12.0`
**Primary owner:** Data & Research Lead
**Depends on:** M6, M7
**Blocks:** None

## Goal

Turn the queue's exhaust into the evidence that sells the product: wait-time trends, no-show rates, channel mix, a busiest-hours heatmap, exports, and an anonymised district-level view for health-department planning.

## Why this milestone exists

The queue-length heatmap is the single easiest "why should we pay for this" chart for a clinic
manager, and the district aggregate is the one that opens a provincial conversation later. Both are
also the natural home for the capstone's data-analysis marks.

The engineering constraint is that **reports must never query the live ticket table across a date
range**. A nightly aggregation into `daily_queue_stats` keeps the dashboard fast at pilot scale and
keeps a heavy report from ever competing with a Call Next.

## Scope

- Nightly `daily_queue_stats` aggregation worker with idempotent re-runs and catch-up
- Clinic reports UI: average wait, no-show rate, channel mix, queue-length heatmap (Chart.js)
- Live operational KPIs on the manager dashboard (today vs the 4-week average)
- CSV and PDF export plus a scheduled emailed weekly report
- Anonymised district/provincial aggregate dashboard with a small-cell suppression rule
- No-show risk insight and a simple staffing recommendation
- Reporting contract plus figure-accuracy tests against a fixed fixture dataset

## Issues

| # | Title |
|---|-------|
| 88 | Nightly `daily_queue_stats` aggregation worker |
| 89 | Clinic reports UI: wait time, no-show, channel mix, heatmap |
| 90 | Live operational KPIs on the manager dashboard |
| 91 | CSV/PDF export and scheduled weekly email report |
| 92 | Anonymised district aggregate dashboard with small-cell suppression |
| 93 | No-show risk insight and staffing recommendation |
| 94 | Reporting contract and figure-accuracy tests |

## Exit criteria

- [ ] Every report reads pre-aggregated rows; none scans `tickets` across a date range
- [ ] Re-running the nightly job for a past date produces identical figures (idempotent)
- [ ] The heatmap makes a clinic's busiest hour obvious at a glance in under 5 seconds of looking
- [ ] Exports match the on-screen figures exactly, checked by a test against a fixed fixture
- [ ] The district view never exposes a cell small enough to identify an individual visit
- [ ] Figures are reproducible from the fixture dataset, so the capstone report can cite them

---

**Navigation:** [GitHub docs index](../README.md) · [Implementation plan](../../PLAN/IMPLEMENTATION_PLAN.md) · [Workload split](../../TEAM/WORKLOAD_SPLIT.md) · [Issues](../ISSUES/M12/)
