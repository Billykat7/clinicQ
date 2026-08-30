# Milestone 11: Appointments, Check-in & Patient Care Extras

**Status:** 📋 planned · **Phase:** Semester 2 · Sprint 11–12 · **Suggested tag:** `v0.11.0`
**Primary owner:** Backend Lead · Frontend (Patient) Dev
**Depends on:** M6, M9
**Blocks:** None

## Goal

Extend the queue into the features UK and US patients already expect: booked appointment slots that convert into tickets automatically, self check-in at the door, booking on behalf of a dependant, chronic-medication reminders, a virtual waiting room, and a post-visit feedback survey.

## Why this milestone exists

Benchmarking against the NHS App and e-Referral Service, Solv, Zocdoc and Qmatic's kiosk products
surfaces six things a pure walk-in queue lacks, all of which are cheap to add on top of a working
ticket engine and all of which markedly raise the capstone's assessed scope:

- **Scheduled appointments** (NHS App, Zocdoc): for chronic and follow-up patients who should not queue at all.
- **Self check-in kiosk / QR arrival** (Qmatic, NHS outpatient kiosks): removes the reception bottleneck at 07:30.
- **Proxy booking** (NHS App 'linked profiles'): a daughter books for her mother; a parent books for a child.
- **Chronic repeat reminders:** the single highest-value nudge in primary care.
- **Virtual waiting room** (Solv, US urgent care): 'wait in your car / nearby', call-forward aware of travel time.
- **Post-visit feedback** (NHS Friends & Family Test): the metric a clinic manager can show their district.

## Scope

- Appointment slot and capacity model per service and room, with a booking horizon
- Book, reschedule and cancel, plus automatic conversion into a ticket near the slot time
- Reminders at 24 hours and 2 hours with confirm/cancel by reply on every channel
- Self check-in kiosk and QR arrival check-in at the clinic door
- Proxy/dependant booking with recorded consent and a clear acting-on-behalf-of trail
- Chronic and repeat-visit reminder schedules (medication collection)
- Virtual waiting room: wait nearby, travel-time-aware call-forward, 'on my way' acknowledgement
- Post-visit feedback survey with a simple satisfaction score and free-text, reported per clinic

## Issues

| # | Title |
|---|-------|
| 80 | Appointment slots and capacity model |
| 81 | Book, reschedule, cancel and auto-convert an appointment into a ticket |
| 82 | Appointment reminders with confirm/cancel by reply |
| 83 | Self check-in kiosk and QR arrival check-in |
| 84 | Proxy booking for dependants with recorded consent |
| 85 | Chronic and repeat-visit reminder schedules |
| 86 | Virtual waiting room and travel-time-aware call-forward |
| 87 | Post-visit feedback survey and satisfaction reporting |

## Exit criteria

- [ ] A booked appointment becomes a live ticket automatically at the configured lead time
- [ ] A patient can confirm or cancel a reminder by replying, without opening any app
- [ ] Kiosk check-in moves a booked patient into the waiting state without reception involvement
- [ ] A proxy booking records who booked for whom, with consent, and is visible in the audit log
- [ ] The virtual waiting room warns a patient early enough to arrive, based on their stated travel time
- [ ] Feedback scores roll up per clinic and per queue in the M12 reports

---

**Navigation:** [GitHub docs index](../README.md) · [Implementation plan](../../PLAN/IMPLEMENTATION_PLAN.md) · [Workload split](../../TEAM/WORKLOAD_SPLIT.md) · [Issues](../ISSUES/M11/)
