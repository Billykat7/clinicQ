# Issue 82: Appointment reminders with confirm/cancel by reply

> **In short:** Patients are reminded the day before and two hours before, and can confirm or cancel by simply replying.

| | |
|---|---|
| **Milestone** | [M11: Appointments, Check-in & Patient Care Extras](../../MILESTONES/M11_appointments_checkin_patient_care.md) |
| **Sprint** | 11 (weeks 21–22) |
| **Owner** | B, Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Appointments |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 63](../M9/ISSUE_63_notification_service_adapters.md): Notification service, transport adapters and delivery log<br>[Issue 81](../M11/ISSUE_81_booking_reschedule_auto_ticket.md): Book, reschedule, cancel and auto-convert an appointment into a ticket |
| **Unblocks** | [Issue 85](../M11/ISSUE_85_chronic_repeat_reminders.md): Chronic and repeat-visit reminder schedules<br>[Issue 93](../M12/ISSUE_93_noshow_risk_staffing_insight.md): No-show risk insight and staffing recommendation |

## Context

Reminders with a reply action are the cheapest no-show reduction available, and they are what turns
an appointment system into one a clinic will actually pay for: a cancelled slot the day before can be
given to someone else.

## Starting point

- Sends go through the notification service (Issue 63), so preferences, quiet hours and consent apply automatically.
- Replies arrive on the same inbound channels as M10 (SMS keyword, WhatsApp button, push action).

## Scope

- Reminders at 24 hours and 2 hours before a slot, on the patient's preferred transport
- Confirm and cancel by reply keyword, quick-reply button or push action
- A cancelled slot returned to availability immediately and offered to a waiting list
- Reminder suppression when a patient has already checked in
- Reminder effectiveness measured for the M12 no-show analysis

## Out of scope

- Chronic medication reminders (Issue 85).
- The no-show analysis that measures whether reminders work (Issue 93).

## Acceptance criteria

- [ ] Reminders are sent at both intervals on the correct transport
- [ ] A reply cancels or confirms without opening any app
- [ ] A cancellation frees the slot within seconds
- [ ] An already-checked-in patient receives no reminder
- [ ] Reminder-to-attendance correlation is measurable per site
- [ ] Reminders respect quiet hours and opt-outs

## How to verify

1. Book a slot and advance the clock: reminders at 24 hours and at 2 hours, on the preferred channel.
2. Reply CANCEL: the slot is free within seconds.
3. Check in early: the 2-hour reminder is not sent.

## Files touched

- `src/modules/appointments/reminders.py` (the sweep, replies, the Issue 93 counts), `booking_router.py` (reply and report routes)
- `alembic/versions/0043_reminders.py`, `src/core/scheduler.py` (lock 882)
- `src/core/webhook_gateways/africastalking.py` (CONFIRM/CANCEL before STOP), `src/static/patient-sw.js` (Confirm/Cancel push buttons)
- `src/modules/notifications/template_registry.py`, `src/locales/en/notifications.toml`, `transports/webpush.py`
- `contracts/appointments.yaml`, `tests/integration/appointments/test_reminders.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #82
