# Issue 80: Appointment slots and capacity model

**Area:** Backend / Appointments
**Milestone:** M11 - Appointments, Check-in & Patient Care Extras
**Owner role:** Backend Lead
**Depends on:** Issues 25, 26
**Estimate:** 3 days
**Status:** Planned

## Context

The gap between a walk-in queue and what NHS App or Zocdoc patients expect. Chronic and follow-up
patients should not queue at all; they should hold a slot, and the slot should become a ticket by
itself when the time comes.

## Scope

- `appointment_slots`: site, queue or service, start, duration, capacity, booked count
- Slot generation from a recurring template plus per-day overrides
- Booking horizon and a minimum lead time, configurable per site
- Capacity honouring the queue's daily limit so appointments and walk-ins cannot jointly oversubscribe a room
- Slot availability API with a per-day view

## Acceptance criteria

- [ ] Slots generate correctly from a weekly template including holiday exclusions
- [ ] A slot cannot be overbooked beyond its capacity, proven under concurrent booking
- [ ] Appointment capacity and walk-in capacity are reconciled against one daily limit
- [ ] Availability reflects a cancellation within seconds
- [ ] Slot times respect `Africa/Johannesburg` across a DST-free but midnight-crossing test
- [ ] A clinic manager can block a slot range for a staff absence

## Files touched

- `app/database/models/appointment_slot.py`
- `app/services/appointments.py`
- `migrations/versions/*_appointments.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #80
