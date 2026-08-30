# Issue 81: Book, reschedule, cancel and auto-convert an appointment into a ticket

**Area:** Backend / Appointments
**Milestone:** M11 - Appointments, Check-in & Patient Care Extras
**Owner role:** Backend Lead
**Depends on:** Issue 80
**Estimate:** 3 days
**Status:** Planned

## Context

The hand-off that makes appointments useful: at a configured lead time the booking quietly becomes a
live ticket in the same queue as everyone else, so the clinic runs one board rather than a queue and a
diary that disagree.

## Scope

- Book, reschedule and cancel across web, USSD and WhatsApp
- Automatic conversion into a ticket at a configured lead time before the slot
- Late arrival handling: an unconverted booking past its slot becomes a walk-in-style ticket, not a loss
- No-show handling for a booking whose ticket is never attended
- Booking reference and confirmation across every channel

## Acceptance criteria

- [ ] A booking becomes a ticket automatically at the configured lead time
- [ ] The converted ticket enters the same queue and sequence as walk-ins
- [ ] Rescheduling frees the original slot immediately
- [ ] A late patient is not silently dropped; they receive a ticket with a documented rule
- [ ] Booking works identically on all channels, covered by the parity suite
- [ ] Conversion is idempotent: a retried job never creates two tickets

## Files touched

- `app/services/appointments.py`
- `workers/appointment_converter.py`
- `tests/integration/test_appointment_conversion.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #81
