# Issue 92: Anonymised district aggregate dashboard with small-cell suppression

**Area:** Backend / Reporting
**Milestone:** M12 - Reporting, Analytics & District Dashboards
**Owner role:** Data & Research Lead
**Depends on:** Issue 88
**Estimate:** 3 days
**Status:** Planned

## Context

The view that opens a provincial conversation: anonymised wait-time and queue-length trends across
public clinics in an area. Small-cell suppression is mandatory: an aggregate over three visits is not an
aggregate, it is a disclosure.

## Scope

- District-level aggregation across sites, restricted to public clinics by default
- Anonymised metrics only: average wait, ticket volume, no-show rate, by clinic and by hour
- Small-cell suppression rule with a documented threshold
- Access restricted to a `district_viewer` role granted only by a platform admin
- Open-data export of suppressed aggregates for research use

## Acceptance criteria

- [ ] No district figure can be traced to an individual visit
- [ ] Cells below the suppression threshold are withheld, not rounded
- [ ] Access requires an explicitly granted role and every view is audited
- [ ] The suppression rule is documented with its rationale
- [ ] The open-data export carries a licence and a methodology note
- [ ] The dashboard is useful at a district level without naming any patient

## Files touched

- `app/services/district_reporting.py`
- `app/web/district/routes.py`
- `docs/PRODUCT/12-upscaling-24-months.md`

---

**Refs:** [M12 milestone](../../MILESTONES/M12_reporting_analytics.md) · [product docs](../../../PRODUCT/12-upscaling-24-months.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #92
