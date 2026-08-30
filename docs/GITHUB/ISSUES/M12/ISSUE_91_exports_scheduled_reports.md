# Issue 91: CSV/PDF export and scheduled weekly email report

**Area:** Backend / Reporting
**Milestone:** M12 - Reporting, Analytics & District Dashboards
**Owner role:** Data & Research Lead
**Depends on:** Issue 89
**Estimate:** 2 days
**Status:** Planned

## Context

A clinic manager's monthly report to their district should be one click, and it should arrive in their
inbox without anyone remembering to send it. Exports that disagree with the screen destroy trust in both,
so they are generated from the same service.

## Scope

- CSV export of any report view, and a branded PDF summary
- Scheduled weekly email report per site, opt-in per manager
- Exports generated from the same service functions as the on-screen figures
- Export requests audited, and large exports handled as a background job
- Exports carry no patient-identifying data unless explicitly requested and permitted

## Acceptance criteria

- [ ] An export matches the on-screen figures exactly, verified against a fixture
- [ ] The weekly email arrives on schedule with the correct period
- [ ] A large export runs in the background and notifies on completion
- [ ] Every export is audited with the requesting user
- [ ] Default exports contain aggregates only, no personal data
- [ ] The PDF is legible when printed in black and white

## Files touched

- `app/services/exports.py`
- `workers/scheduled_reports.py`
- `app/templates/reports/pdf_summary.html`

---

**Refs:** [M12 milestone](../../MILESTONES/M12_reporting_analytics.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #91
