# Issue 92: Anonymised district aggregate dashboard with small-cell suppression

> **In short:** Health-department planners get anonymised, area-level wait trends without any figure being traceable to a single visit.

| | |
|---|---|
| **Milestone** | [M12: Reporting, Analytics & District Dashboards](../../MILESTONES/M12_reporting_analytics.md) |
| **Sprint** | 11 (weeks 21–22) |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Backend / Reporting |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 88](../M12/ISSUE_88_daily_stats_worker.md): Nightly `daily_queue_stats` aggregation worker |
| **Unblocks** | [Issue 94](../M12/ISSUE_94_reporting_contract_accuracy.md): Reporting contract and figure-accuracy tests |

## Context

The view that opens a provincial conversation: anonymised wait-time and queue-length trends across
public clinics in an area. Small-cell suppression is mandatory: an aggregate over three visits is not an
aggregate, it is a disclosure.

## Starting point

- Add a `district_viewer` role through the RBAC manifest (Issue 18) and scope it with a grant, not a hard-coded check.
- Only ever read from `daily_queue_stats`, never raw tickets.

## Scope

- District-level aggregation across sites, restricted to public clinics by default
- Anonymised metrics only: average wait, ticket volume, no-show rate, by clinic and by hour
- Small-cell suppression rule with a documented threshold
- Access restricted to a `district_viewer` role granted only by a platform admin
- Open-data export of suppressed aggregates for research use

## Out of scope

- Any per-clinic ranking for the public.

## Acceptance criteria

- [ ] No district figure can be traced to an individual visit
- [ ] Cells below the suppression threshold are withheld, not rounded
- [ ] Access requires an explicitly granted role and every view is audited
- [ ] The suppression rule is documented with its rationale
- [ ] The open-data export carries a licence and a methodology note
- [ ] The dashboard is useful at a district level without naming any patient

## How to verify

1. Pick a cell with fewer visits than the threshold: withheld, not rounded.
2. Open the dashboard without the role: refused; with it: allowed and audited.
3. The open-data export carries a licence and a methodology note.

## Files touched

- `src/modules/reporting/district.py`
- `src/web/district.py`
- `src/templates/district/`
- `docs/PRODUCT/12-upscaling-24-months.md`

---

**Refs:** [M12 milestone](../../MILESTONES/M12_reporting_analytics.md) · [product docs](../../../PRODUCT/12-upscaling-24-months.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #92
