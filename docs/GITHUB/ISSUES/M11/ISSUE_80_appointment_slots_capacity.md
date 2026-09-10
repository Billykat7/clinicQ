# Issue 80: Appointment slots and capacity model

> **In short:** Clinics can offer bookable appointment times without appointments and walk-ins together overfilling a room.

| | |
|---|---|
| **Milestone** | [M11: Appointments, Check-in & Patient Care Extras](../../MILESTONES/M11_appointments_checkin_patient_care.md) |
| **Sprint** | 8 (weeks 15–16) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Appointments |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 25](../M4/ISSUE_25_queues_model_multiroom.md): `queues` model: multi-room, multi-service queues per site<br>[Issue 26](../M4/ISSUE_26_services_catalogue_service_times.md): Services catalogue with expected service times |
| **Unblocks** | [Issue 81](../M11/ISSUE_81_booking_reschedule_auto_ticket.md): Book, reschedule, cancel and auto-convert an appointment into a ticket |

## Context

The gap between a walk-in queue and what NHS App or Zocdoc patients expect. Chronic and follow-up
patients should not queue at all; they should hold a slot, and the slot should become a ticket by
itself when the time comes.

## Starting point

- New module: `src/modules/appointments/`.
- Holidays and closures come from Issue 24; daily capacity from the queue settings in Issue 25.
- Guard against overbooking in the database (a locked row or a constraint), the same way Issue 39 guards ticket numbers.

## Scope

- `appointment_slots`: site, queue or service, start, duration, capacity, booked count
- Slot generation from a recurring template plus per-day overrides
- Booking horizon and a minimum lead time, configurable per site
- Capacity honouring the queue's daily limit so appointments and walk-ins cannot jointly oversubscribe a room
- Slot availability API with a per-day view

## Out of scope

- Booking itself and converting a booking into a ticket (Issue 81).
- Reminders (Issue 82).

## Acceptance criteria

- [ ] Slots generate correctly from a weekly template including holiday exclusions
- [ ] A slot cannot be overbooked beyond its capacity, proven under concurrent booking
- [ ] Appointment capacity and walk-in capacity are reconciled against one daily limit
- [ ] Availability reflects a cancellation within seconds
- [ ] Slot times respect `Africa/Johannesburg` across a DST-free but midnight-crossing test
- [ ] A clinic manager can block a slot range for a staff absence

## How to verify

1. Generate a month of slots from a weekly template: holidays are skipped.
2. Book the last place in a slot from two sessions at once: one succeeds, one is told it is full.
3. Block a range for a staff absence: those slots disappear from availability.

## Files touched

- `src/modules/appointments/`
- `src/database/models/appointment_slot.py`
- `alembic/versions/NNNN_appointments.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #80
