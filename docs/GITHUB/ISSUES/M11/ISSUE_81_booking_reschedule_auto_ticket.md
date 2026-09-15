# Issue 81: Book, reschedule, cancel and auto-convert an appointment into a ticket

> **In short:** A booking turns itself into a normal ticket shortly before the appointment, so booked patients join the same fair queue as everyone else.

| | |
|---|---|
| **Milestone** | [M11: Appointments, Check-in & Patient Care Extras](../../MILESTONES/M11_appointments_checkin_patient_care.md) |
| **Sprint** | 9 (weeks 17–18) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Appointments |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 80](../M11/ISSUE_80_appointment_slots_capacity.md): Appointment slots and capacity model |
| **Unblocks** | [Issue 82](../M11/ISSUE_82_appointment_reminders_reply.md): Appointment reminders with confirm/cancel by reply<br>[Issue 83](../M11/ISSUE_83_kiosk_self_checkin.md): Self check-in kiosk and QR arrival check-in<br>[Issue 84](../M11/ISSUE_84_proxy_dependant_booking.md): Proxy booking for dependants with recorded consent |

## Context

The hand-off that makes appointments useful: at a configured lead time the booking quietly becomes a
live ticket in the same queue as everyone else, so the clinic runs one board rather than a queue and a
diary that disagree.

## Starting point

- The conversion runs as a scheduled sweep in `src/core/scheduler.py` (advisory lock, so it is safe on several instances), calling the join service from Issue 40.
- Idempotency is the main risk: key the conversion on the booking id.

## Scope

- Book, reschedule and cancel across web, USSD and WhatsApp
- Automatic conversion into a ticket at a configured lead time before the slot
- Late arrival handling: an unconverted booking past its slot becomes a walk-in-style ticket, not a loss
- No-show handling for a booking whose ticket is never attended
- Booking reference and confirmation across every channel

## Out of scope

- Reminders (Issue 82) and kiosk check-in (Issue 83).
- Booking on someone else's behalf (Issue 84).

## Acceptance criteria

- [ ] A booking becomes a ticket automatically at the configured lead time
- [ ] The converted ticket enters the same queue and sequence as walk-ins
- [ ] Rescheduling frees the original slot immediately
- [ ] A late patient is not silently dropped; they receive a ticket with a documented rule
- [ ] Booking works identically on all channels, covered by the parity suite
- [ ] Conversion is idempotent: a retried job never creates two tickets

## How to verify

1. Book a slot, then advance the test clock to the lead time: exactly one ticket appears, in the normal sequence.
2. Run the sweep twice for the same window: still one ticket.
3. Arrive after the slot: the patient still gets a ticket, under the documented rule.

## Files touched

- `src/modules/appointments/booking.py` (book, reschedule, cancel for every channel), `conversion.py` (the sweep and the late rule), `booking_router.py`
- `src/modules/appointments/capacity.py` (references, one booking a day, `mark_converted`), `src/modules/queue/service.py` (`join_queue(appointment=...)`)
- `alembic/versions/0042_booking.py`, `src/core/scheduler.py` (lock 881)
- `src/web/book.py`, `src/templates/discover/book.html`, `src/static/js/patient-book.js`
- `contracts/appointments.yaml`, `tests/integration/appointments/test_booking.py`, `test_capacity_concurrency.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #81
