# Issue 85: Chronic and repeat-visit reminder schedules

**Area:** Backend / Appointments
**Milestone:** M11 - Appointments, Check-in & Patient Care Extras
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issues 82, 63
**Estimate:** 2 days
**Status:** Planned

## Context

Chronic medication collection is one of the highest-volume, most predictable flows in primary care and
the single most valuable nudge the product can send. A recurring schedule with a one-tap join keeps
patients on treatment and smooths the clinic's week.

## Scope

- Recurring reminder schedules per patient: interval, next due date, service
- Reminder message with a one-tap or one-keypress join for the collection queue
- Missed-collection follow-up after a configurable grace period
- Clinic-side management of a patient's schedule, with an easy opt-out
- Collection adherence reporting for the M12 dashboard

## Acceptance criteria

- [ ] A recurring schedule sends reminders on time and rolls forward after each collection
- [ ] The reminder's join action creates a ticket in one interaction
- [ ] A missed collection triggers exactly one follow-up, not a repeating nag
- [ ] A patient can stop reminders from any channel
- [ ] Adherence is reportable per site and per service
- [ ] Reminders respect quiet hours, opt-outs and clinic opening days

## Files touched

- `app/services/chronic_reminders.py`
- `workers/chronic_worker.py`
- `tests/integration/test_chronic_reminders.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #85
