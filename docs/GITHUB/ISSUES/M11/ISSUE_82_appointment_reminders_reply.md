# Issue 82: Appointment reminders with confirm/cancel by reply

**Area:** Backend / Appointments
**Milestone:** M11 - Appointments, Check-in & Patient Care Extras
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issues 81, 63
**Estimate:** 2 days
**Status:** Planned

## Context

Reminders with a reply action are the cheapest no-show reduction available, and they are what turns
an appointment system into one a clinic will actually pay for: a cancelled slot the day before can be
given to someone else.

## Scope

- Reminders at 24 hours and 2 hours before a slot, on the patient's preferred transport
- Confirm and cancel by reply keyword, quick-reply button or push action
- A cancelled slot returned to availability immediately and offered to a waiting list
- Reminder suppression when a patient has already checked in
- Reminder effectiveness measured for the M12 no-show analysis

## Acceptance criteria

- [ ] Reminders are sent at both intervals on the correct transport
- [ ] A reply cancels or confirms without opening any app
- [ ] A cancellation frees the slot within seconds
- [ ] An already-checked-in patient receives no reminder
- [ ] Reminder-to-attendance correlation is measurable per site
- [ ] Reminders respect quiet hours and opt-outs

## Files touched

- `app/services/appointment_reminders.py`
- `workers/reminder_worker.py`
- `tests/integration/test_reminders.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #82
