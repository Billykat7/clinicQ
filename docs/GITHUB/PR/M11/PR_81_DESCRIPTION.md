# PR: Book, move and cancel an appointment, and turn each booking into one ticket before its time (Issue 81 / M11-81)

**Milestone:** [Milestone 11: Appointments, Check-in & Patient Care Extras](https://github.com/Billykat7/clinicQ/milestone/11) ·
**Issue:** [#81](https://github.com/Billykat7/clinicQ/issues/81) · **Builds on:** #80 (slots and the shared daily limit, PR
#207), #40 (the join service), #43 (recall), #63 (notifications), all merged · **Unblocks:** #82, #83, #84

The appointment book from #80 now takes bookings, and each booking quietly becomes a normal ticket shortly
before its time. The clinic runs one board, not a queue and a diary that disagree. With this PR:

- **A patient books a time on the web**:
  - they open "Book a time" (linked from the join page), sign in with their phone and pick a day and a time;
  - they get a reference such as `K7M-4QP` on screen, and by message if they agreed to messages;
  - the same page lists their bookings, with **Move to another time** and **Cancel this booking**.
- **Moving or cancelling frees the original time at once.** A move takes the new time and gives the old one
  back in one transaction; if the new time refuses, nothing changes.
- **About 30 minutes before the time, the booking becomes a ticket** in the same queue, drawn from the same
  counter as everyone else. A walk-in at 09:25 and a booked patient converted at 09:30 get consecutive
  numbers. The lead time is the clinic's own setting.
- **Running the sweep again never makes a second ticket.** The booking moves to `converted` once, and the
  database refuses a second ticket naming it. This was proved with two sweeps racing on PostgreSQL.
- **A late patient is never silently dropped.** The rule is written on the booking page and in the code:
  - the ticket exists at the lead time whether or not the patient has arrived;
  - a closed clinic makes the booking wait, and it still becomes a ticket after its time;
  - a patient called while not there gets the normal recall before a no-show, and may then join again;
  - a booking whose day ends unconverted lapses, and the patient is told.

**Not done here, and not claimed:**

- **"Booking works identically on all channels, covered by the parity suite":** USSD and WhatsApp are not built
  (M10, Issues 73 to 76), and neither is the parity suite (Issue 79). What is here:
  - booking is **one set of service functions every channel calls with its own source**, the way every channel
    calls `join_queue`;
  - a parametrised test runs book, a refused second booking, move and conversion for web, USSD and WhatsApp,
    and gets the same answers, with the source carried onto the ticket.

  Issue 79 should adopt that test when the adapters exist.
- **No-show handling needs nothing new.** Once converted, the ticket is called, recalled and marked a no-show
  by #43's rule like any other, and the booking stays `converted` with its ticket linked, for #93's analysis.

## Summary

- **The model** (migration `0042`):
  - `appointment.reference` (unique), `source`, `rescheduled_from_id`, `converted_at` and `lapsed_at`, plus
    the statuses `rescheduled`, `converted` and `lapsed`;
  - `appointment_policy.convert_lead_minutes` (5 to 240, default 30);
  - `ticket.appointment_id` with `uq_ticket_appointment`, the conversion's idempotency key.
- **Booking** (`src/modules/appointments/booking.py`):
  - `book`, `reschedule` and `cancel` take `source` and change no rule by it;
  - `patient_bookings`, `booking_view` and `view_by_id` read bookings back.
  - A new time is checked against #80's offer rule (blocks, hours, lead time, horizon), then the capacity
    guard.
- **Capacity** (`capacity.py`):
  - `claim_place` now checks one booking per patient per queue per day under the day lock, draws a reference
    (retrying a collision in a savepoint) and records the source;
  - `release_place(to=RESCHEDULED)` gives a place back for a move;
  - `mark_converted` hands the place to the ticket.
- **The join service** (`src/modules/queue/service.py`): `join_queue(..., appointment=)` skips the abuse guards
  and the walk-in-only rule (the booking already passed both). Inside the same savepoint, after the counter
  and the day are locked, it marks the booking converted before the day is counted.
- **Conversion** (`conversion.py`): `convert_due` hands every due booking to `join_queue`, one savepoint each,
  and lapses bookings from earlier days. `run_appointment_conversion` runs every minute under lock 881.
- **Messages:**
  - `appointment_booked` ("booked Wed 16 Sep 09:00 at …, Ref S7S-3AG") and `appointment_lapsed`;
  - both are one SMS segment with worst-case names, and there is a new `{when}` blank.
- **The API** (`booking_router.py`, all documented in `contracts/appointments.yaml`):
  - `GET /api/v1/clinics/{site}/appointments/availability` (public; times with room only, no counts);
  - `POST /api/v1/clinics/{site}/appointments`;
  - `GET /api/v1/patients/me/appointments`;
  - `POST …/{id}/reschedule` and `…/{id}/cancel`;
  - `GET /api/v1/sites/{site}/appointments/bookings` (the front desk's day);
  - the policy routes now carry `convert_lead_minutes`.
- **The page** (`src/web/book.py`, `discover/book.html`, `patient-book.js`): phone sign-in, the messages
  question, day and time buttons, the confirmation, and the patient's bookings with Move, Cancel, and once
  converted, "Open my place in the queue".

## Design notes

**One lock order for every writer, so nothing deadlocks.** #80 set counter → day for joins and day → slot for
bookings. This PR adds two writers and keeps the order:
- **conversion:** counter → day → booking. The booking is marked converted inside `join_queue`, after the day
  lock, not locked up front, which would reverse the order against walk-ins.
- **reschedule:** the day locks of both slots in a fixed order → the booking → the old slot → the new slot.

A cancel touches only the booking and its slot. Every pair of writers takes shared locks in the same order.

**The booking gives its place to the ticket before the day is counted.** The day's usage counts tickets plus
*booked* appointments. If the booking stayed `booked` while its ticket was issued, a full queue would count
the patient twice and refuse their own conversion. Marking it `converted` first, in the same savepoint, keeps
the count exact. The test fills a limit of 2 with one booking and one walk-in, converts, and reads 2 tickets,
0 appointments, 0 remaining.

**Idempotency is keyed on the booking, twice.**
1. The conditional `booked → converted` update matches nothing on a second run, and the savepoint rolls back
   the number it had drawn.
2. `uq_ticket_appointment` refuses a second ticket naming the booking whatever writes it.

On PostgreSQL, two sweeps released together over five due bookings made 5 tickets and 5 conversions between
them, with no errors.

**A move creates a new booking row with a new reference.** Each row is one place held once, and
`reference` is unique. The new row names the old one (`rescheduled_from_id`), and the confirmation message
gives the new reference.

**Booked times are public, bookings are not.** The public day view shows times with room at a clinic a
patient may be shown. It never shows counts, other patients or blocked times. A patient's bookings are read
by their own session; the front desk's list sits behind `appointments:read`.

**Why conversion does not wait for the patient.** A ticket that waits for arrival is a second, hidden queue.
Converting at the lead time puts the booked patient in the one line, where the board, the recall rule and
the capacity count already work. Arrival check-in belongs to the kiosk (#83).

## Changes

- **New:**
  - `src/modules/appointments/booking.py`, `conversion.py`, `booking_router.py`
  - `alembic/versions/0042_booking.py`
  - `src/web/book.py`, `src/templates/discover/book.html`, `src/static/js/patient-book.js`
- **Changed:**
  - Appointments and queue: `src/modules/appointments/capacity.py`, `schemas.py`, `service.py`,
    `availability.py`, `router.py`; `src/modules/queue/service.py`.
  - Models and enums: `src/database/models/appointment_slot.py` and `ticket.py`; `src/commons/enums.py`
    (statuses, `ALREADY_BOOKED`, `BOOKED`, `BOOKING_LAPSED`, templates).
  - Notifications: `src/modules/notifications/template_registry.py` (`{when}`), `src/locales/en/notifications.toml`,
    `notifications.lock.json`.
  - Wiring: `src/core/scheduler.py`, `src/api/v1/router.py`, `src/main.py`.
  - Web: `discover/join.html` (the "Book a time" link), `discover.css`.
  - Contracts: `contracts/appointments.yaml`.
- **Tests, new:**
  - `tests/integration/appointments/test_booking.py` (11)
  - `test_two_conversion_sweeps_at_once_make_one_ticket_per_booking` in `test_capacity_concurrency.py`
    (PostgreSQL)
- **Tests, updated:**
  - `test_openapi_contracts.py`: the appointments contract claims `/clinics/…/appointments` and
    `/patients/me/appointments`.
  - `test_cross_tenant.py`: bookings are a case now, and the #80 `PENDING` entry is gone.
  - `test_api_route_gates.py`: the public day view, with its reason.
  - `test_site_scoped_queries.py`: eleven reasoned entries: the patient's own bookings, and the sweep's system
    reads.
- **Docs:**
  - `docs/OPS/PATIENT_APP_TESTING.md`: booking is no longer "not built";
  - the Issue 81 spec (files), the M11 status row and bars (`--assume-closed 81`), the README Status block (77
    of 111), and sprint 9's row.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (323 files) are clean.
- [x] **Migration:** 0042 ran on a database holding #80's demo bookings, giving each a reference. `alembic check`
  reports "No new upgrade operations detected". `downgrade 0041` and `upgrade head` both run cleanly.
- [x] **Full suite:** `TZ=UTC pytest tests/ -n auto --dist loadscope` on the Docker PostgreSQL 18 and Redis, with
  browsers, gave **2584 passed, 10 failed, 1 skipped, 9 xfailed**.
  - The run crossed midnight in Johannesburg. All ten failures were tests that issue a ticket and read "today"
    a moment later: one dashboard undo test and nine waiting-room board browser tests.
  - Re-run straight after, those three files pass: **25 passed**.
  - None of them touches booking, and every appointments test was written against fixed times for this reason.
- [x] **How to verify, on a running server.** Playwright drove the demo world on PostgreSQL (`clinicq_m11_demo`
  at 0042). The manager set Chronic medication collection to 09:00–11:00 in 30-minute slots for two each, and
  generated a week of slots. A patient then used the booking page at 390 px:

  ```text
  generate: {'created': 24, 'kept': 0, 'removed': 0, 'withdrawn': 0, 'restored': 0}
  booked: Booked: Chronic medication collection at Hillbrow Community Health Centre, Wed 16 Sep 09:00. Reference S7S-3AG. …
  sms: BK ClinicQ: booked Wed 16 Sep 09:00 at Hillbrow Community Health..., Chronic medicat.... Ref S7S-3AG. Please arrive a little early.
  times before move: ['09:30 · Chronic medication collection', '10:00 · Chronic medication collection', '10:30 · Chronic medication collection']
  times after move:  ['09:00 · Chronic medication collection', '09:30 · Chronic medication collection', '10:00 · …', '10:30 · …']
  my booking: TUJ-3C9 2026-09-16T10:00:00+02:00 booked
  run 1 converted 2 waiting 0 lapsed 0
  run 2 converted 0 waiting 0 lapsed 0
  tickets for the booking: [('C002', 'web', 'waiting', '2026-09-16')]
  after conversion: Wed, 16 Sept 10:00 · Chronic medication collection | Hillbrow Community Health Centre · Your booking is now your place in the queue. | Open my place in the queue
  ```

  - Before the move, 09:00 was not offered: both of its places were held, one of them by this patient.
  - The instant the move answered, 09:00 was offered again.
  - The conversion ran `convert_due` twice against the same database at 09:30, the booking's lead time. The
    first run converted this booking and an earlier patient's; the second converted nothing.
  - (The page shown below is from a second identical run, after a fix that stopped the list repeating the
    reference.)
- [x] **Over HTTP and the sweep** (`test_booking.py`, 11 passed, in UTC, at fixed times tomorrow):
  - **Book:**
    - the public day view lists the time, with no counts;
    - booking answers 201 with a `K7M-4QP` reference and "Booked: Triage at …", and the patient is sent it;
    - a second booking that day in that queue is 409 `appointments.slot.already_booked`.
  - **Move:** a move to a full time is 409 `slot_full` and the original stays booked (`booked_count` 1). A move to
    a free time leaves the original at 0 and the new at 1 immediately, with a new reference naming the old
    booking. The old booking then refuses a cancel (`appointments.booking.not_booked`).
  - **Cancel:** another patient gets 404; a cancel leaves 0 and the day view offers the time again.
  - **Lead time:**
    - nothing at 31 minutes before, one ticket at 30, nothing more at 30 again or at 29;
    - the ticket is `T002` between walk-ins `T001` and `T003`, status waiting, source web;
    - the patient holds one ticket, and their list shows `converted` and the ticket page.
  - **Never counted twice:** with a limit of 2, one booking and one walk-in fill the queue (the next walk-in is
    `queue_full`); after conversion the day reads 2 tickets, 0 appointments, 0 remaining.
  - **Late rule:**
    - a booking an hour past its time converts at noon, behind the walk-in already waiting;
    - one due during an announced closure waits (`clinic_closed`) and converts when the clinic reopens, after
      its own time;
    - yesterday's unconverted booking lapses once, with one `booking_lapsed` message.
  - **Walked in early:** the booking is converted onto the ticket the patient already holds; no second ticket.
  - **Parity** (web, USSD, WhatsApp): the same reference shape, `slot_full` for a rival, the move, the
    conversion, and `ticket.source` equal to the channel.
  - **Front desk:** the day's list shows the booking; the other clinic's desk gets 404; a suspended clinic's
    public day view is 404.
- [x] **PostgreSQL** (`test_capacity_concurrency.py`, 6 passed, including #80's five): two `convert_due` sweeps
  released by a barrier over five due bookings convert 5 between them, with no errors, giving 5 tickets naming
  a booking and 5 bookings `converted`.

| Choose a time (390 px) | Booked, with the reference | After a move | After conversion |
|---|---|---|---|
| ![Book a time: a day chooser, three time buttons and the late rule](https://github.com/Billykat7/clinicQ/blob/de26da2a34c21424da2c83acbf196de563876757/docs/GITHUB/PR/M11/assets/pr81/book-choose-time-390.png?raw=true) | ![The Booked card with reference MTR-9AZ, and Your bookings with Move and Cancel](https://github.com/Billykat7/clinicQ/blob/de26da2a34c21424da2c83acbf196de563876757/docs/GITHUB/PR/M11/assets/pr81/book-confirmed-390.png?raw=true) | ![The times list offering 09:00 again after the booking moved to 10:00](https://github.com/Billykat7/clinicQ/blob/de26da2a34c21424da2c83acbf196de563876757/docs/GITHUB/PR/M11/assets/pr81/book-moved-390.png?raw=true) | ![Your bookings: Your booking is now your place in the queue, with Open my place in the queue](https://github.com/Billykat7/clinicQ/blob/de26da2a34c21424da2c83acbf196de563876757/docs/GITHUB/PR/M11/assets/pr81/book-converted-390.png?raw=true) |

## Acceptance criteria

- [x] **A booking becomes a ticket automatically at the configured lead time:** not at 31 minutes, one ticket
  at 30 (the clinic's `convert_lead_minutes`). The server's two sweep runs converted at the lead time.
- [x] **The converted ticket enters the same queue and sequence as walk-ins:** it is `T002` between walk-ins
  `T001` and `T003`, issued by `join_queue` from the same counter.
- [x] **Rescheduling frees the original slot immediately:** `booked_count` 0 the moment the move answers, and the
  page offered 09:00 again on its next read. A refused move leaves the original booked.
- [x] **A late patient is not silently dropped; they receive a ticket with a documented rule:** conversion
  happens after the time, waits through a closure then converts, and a lost day lapses with a message. The rule
  is in `conversion.py` and on the booking page.
- [ ] **Booking works identically on all channels, covered by the parity suite.** Partly: web, USSD and WhatsApp
  sources give identical answers through the one service, in a parametrised test. The USSD and WhatsApp
  adapters (Issues 73 to 76) and the parity suite (Issue 79) are M10 and not built.
- [x] **Conversion is idempotent: a retried job never creates two tickets:** repeated sweeps (SQLite), two racing
  sweeps (PostgreSQL), and `uq_ticket_appointment` behind both.

## Risk and rollback

- **`join_queue` gains an `appointment` argument.** Every existing caller passes none and behaves as before;
  the queue suite passes.
- **A new minute sweep**, which reads only bookings still `booked` for today or earlier.
- **The join page gains a "Book a time" link.** The booking page only offers times where a manager has made
  some.
- **Rollback:** revert, then `alembic downgrade 0041`. References, sources and the ticket-to-booking link are
  dropped; bookings and tickets stay.

Closes #81
