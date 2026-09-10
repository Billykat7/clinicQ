# Issue 85: Chronic and repeat-visit reminder schedules

> **In short:** Patients on chronic medication get a reminder when their collection is due, with a one-tap way to join the collection queue.

| | |
|---|---|
| **Milestone** | [M11: Appointments, Check-in & Patient Care Extras](../../MILESTONES/M11_appointments_checkin_patient_care.md) |
| **Sprint** | 11 (weeks 21–22); the sprint plan puts this in **A**'s lane, see the note below |
| **Owner** | B, Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Appointments |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 63](../M9/ISSUE_63_notification_service_adapters.md): Notification service, transport adapters and delivery log<br>[Issue 82](../M11/ISSUE_82_appointment_reminders_reply.md): Appointment reminders with confirm/cancel by reply |
| **Unblocks** | No other issue waits on this one. |

> **Note:** The sprint plan has A and B pairing on this in sprint 11; the spec lists B as owner.

## Context

Chronic medication collection is one of the highest-volume, most predictable flows in primary care and
the single most valuable nudge the product can send. A recurring schedule with a one-tap join keeps
patients on treatment and smooths the clinic's week.

## Starting point

- A scheduled sweep in `src/core/scheduler.py`, sending through the notification service (Issue 63) and reusing Issue 82's reply handling.

## Scope

- Recurring reminder schedules per patient: interval, next due date, service
- Reminder message with a one-tap or one-keypress join for the collection queue
- Missed-collection follow-up after a configurable grace period
- Clinic-side management of a patient's schedule, with an easy opt-out
- Collection adherence reporting for the M12 dashboard

## Out of scope

- Adherence reports (Issue 90 and the reports UI in Issue 89 display them).

## Acceptance criteria

- [ ] A recurring schedule sends reminders on time and rolls forward after each collection
- [ ] The reminder's join action creates a ticket in one interaction
- [ ] A missed collection triggers exactly one follow-up, not a repeating nag
- [ ] A patient can stop reminders from any channel
- [ ] Adherence is reportable per site and per service
- [ ] Reminders respect quiet hours, opt-outs and clinic opening days

## How to verify

1. Create a 28-day schedule and advance the clock: one reminder, then the next due date rolls forward after collection.
2. Miss a collection: exactly one follow-up.
3. Reply STOP: no more reminders on any channel.

## Files touched

- `src/modules/appointments/chronic.py`
- `src/database/models/chronic_schedule.py`
- `src/core/scheduler.py`
- `tests/integration/appointments/test_chronic_reminders.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #85
