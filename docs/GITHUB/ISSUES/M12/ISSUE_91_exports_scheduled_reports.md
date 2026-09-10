# Issue 91: CSV/PDF export and scheduled weekly email report

> **In short:** Managers can take their figures anywhere: CSV, a printable PDF, or a weekly email, all matching the screen exactly.

| | |
|---|---|
| **Milestone** | [M12: Reporting, Analytics & District Dashboards](../../MILESTONES/M12_reporting_analytics.md) |
| **Sprint** | 11 (weeks 21–22) |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Backend / Reporting |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 89](../M12/ISSUE_89_clinic_reports_ui.md): Clinic reports UI: wait time, no-show, channel mix, heatmap |
| **Unblocks** | [Issue 94](../M12/ISSUE_94_reporting_contract_accuracy.md): Reporting contract and figure-accuracy tests |

## Context

A clinic manager's monthly report to their district should be one click, and it should arrive in their
inbox without anyone remembering to send it. Exports that disagree with the screen destroy trust in both,
so they are generated from the same service.

## Starting point

- `reportlab` is already in `requirements.txt` for the PDF.
- The weekly email goes through the kernel's single email path (`deliver_email` in `src/modules/notifications/service.py`), and the schedule is a sweep in `src/core/scheduler.py`.

## Scope

- CSV export of any report view, and a branded PDF summary
- Scheduled weekly email report per site, opt-in per manager
- Exports generated from the same service functions as the on-screen figures
- Export requests audited, and large exports handled as a background job
- Exports carry no patient-identifying data unless explicitly requested and permitted

## Out of scope

- District open-data exports (Issue 92).

## Acceptance criteria

- [ ] An export matches the on-screen figures exactly, verified against a fixture
- [ ] The weekly email arrives on schedule with the correct period
- [ ] A large export runs in the background and notifies on completion
- [ ] Every export is audited with the requesting user
- [ ] Default exports contain aggregates only, no personal data
- [ ] The PDF is legible when printed in black and white

## How to verify

1. Export a report view as CSV: the totals equal the screen.
2. Opt in to the weekly email and advance the clock: it arrives with the right period.
3. Print the PDF in black and white: legible.

## Files touched

- `src/modules/reporting/exports.py`
- `src/core/scheduler.py`
- `src/templates/reports/pdf_summary.html`

---

**Refs:** [M12 milestone](../../MILESTONES/M12_reporting_analytics.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #91
