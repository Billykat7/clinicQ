# Release v0.11.0: Appointments, Check-in & Patient Care Extras

**Date:** 2026-09-16 · **Milestone:** M11 · **Issues closed:** 80–87

A pre-release. v0.9.0 reached the patient wherever they were waiting; this release gives them a **time to
come**, and a way to arrive without queueing to say they have arrived:

- a clinic keeps an appointment book whose bookable times share **one daily limit** with the walk-ins at the
  same door, held by the database rather than by a screen;
- a patient books a time on the web, moves it or cancels it, and shortly before the time the booking
  becomes a normal ticket, drawn from the same counter as everyone else;
- they are reminded the day before and two hours before, and answer `CONFIRM` or `CANCEL` in one word or one
  tap, with a cancellation freeing the time in the same request;
- a tablet at the door takes their arrival from one scan, turning a booking into a ticket there and then;
- one phone serves a household: a parent joins or books for a child, a daughter for her mother, with the
  ticket belonging to the person being seen and the messages going to the phone that exists;
- a patient on chronic medication is reminded when their collection is due and takes their place with one
  word, with exactly one follow-up if they do not come;
- a clinic can run a virtual waiting room, where a patient waits at home and is told once when to leave,
  timed to their own trip;
- after the visit, one question about how it went, answered with one tap or one digit, with any comment
  screened for personal information before it is stored.

The USSD and WhatsApp channels these features would also be offered on (M10) are still to come, and the
report **screens** that draw the numbers this milestone records are M12's.

Four rules shape the release, and each is enforced where a bug cannot get round it:

- **One line, one limit.** Bookings and walk-ins take places from the same per-queue daily count, under one
  locked row per queue and day, and the slot's own `booked_count <= capacity` check is a database
  constraint. Two sessions racing for the last place on PostgreSQL leave one booking and one refusal.
- **One booking, one ticket.** Conversion moves the booking `booked → converted` with a conditional update
  inside the join's savepoint, and `uq_ticket_appointment` refuses a second ticket naming it. Two sweeps
  racing on PostgreSQL made one ticket per booking.
- **A ticket is a place, whether or not anyone has arrived.** Checking in stamps `arrived_at` and changes no
  status; conversion does not wait for arrival; an unanswered "time to leave" never costs the place. The
  late rule is written down, and tested, rather than implied.
- **Nothing reaches a patient past the gate.** Every message in this release goes through Issue 63's
  service, so consent, preferences, quiet hours and opt-outs are checked before each send and each retry —
  including a dependant's message, which is answered for by the phone it rings on.

Eight pull requests merged on 15 and 16 September 2026, each after its checks were green and before the
next branched from `main`: #207 (80) → #208 (86) → #209 (87) → #210 (81) → #211 (82) → #212 (83) → #213
(84) → #214 (85), which carries this note. The tag is cut from `main` after that merge.

## What shipped

- **Appointment slots and one shared daily limit** (Issue 80, PR #207; migration `0039`).
  - `appointment_policy`, `appointment_template_window`, `appointment_day_override`, `appointment_slot`,
    `appointment_block` and `queue_capacity_day`: a clinic's weekly pattern, its one-date changes, the slots
    generated from them, and the ranges taken out for a staff absence.
  - Slot generation skips public holidays, announced closures and closed hours, and is idempotent.
  - **The shared limit:** one `queue_capacity_day` row per queue and service day is locked by both the join
    service and a booking, so a queue's `max_daily_capacity` counts tickets **and** booked places. The last
    place, claimed from two sessions at once on PostgreSQL, is taken once.
  - The offer rule (`availability.refusal_for`) answers one question — may this patient book this slot? —
    for every channel: blocks, hours, the lead time and the booking horizon.
- **The virtual waiting room and travel-time call-forward** (Issue 86, PR #208; migration `0040`).
  - `site.virtual_waiting_enabled`, and on the ticket `travel_minutes`, `leave_alert_at`, `on_my_way_at`.
  - The join page asks how long the trip takes; the sweep (lock 886) sends **one** "time to leave" when the
    wait's low end comes down to that trip plus ten minutes, using the same estimate the patient sees.
  - "On my way" shows on the front desk's board. **An unacknowledged alert never costs the place.**
- **Post-visit feedback** (Issue 87, PR #209; migration `0041`).
  - One question per completed visit, from the `done` transition, through the notification service, gated on
    its own consent purpose; answered with one tap on `/f/{token}` or one digit by SMS.
  - Comments are screened for phone numbers, e-mail addresses and ID numbers before they are stored, and
    emptied by a retention sweep (lock 887).
  - `GET /sites/{id}/reports/feedback`: scores and response rates per queue and per staff member.
- **Booking, moving, cancelling and automatic conversion** (Issue 81, PR #210; migration `0042`).
  - `/discover/clinics/{slug}/book`: sign in by phone, pick a day and a time, get a reference such as
    `K7M-4QP`, and move or cancel it. A move frees the old time in the same transaction.
  - The conversion sweep (lock 881, every minute) hands each due booking to Issue 40's `join_queue` at the
    clinic's own lead time (30 minutes by default).
  - **The late rule:** conversion does not wait for arrival, it keeps trying while the clinic is shut, a
    patient called while not there gets the normal recall, and a booking whose day ends unconverted lapses
    with a message.
- **Reminders answered by reply** (Issue 82, PR #211; migration `0043`).
  - 24 hours and 2 hours before, once each, claimed with a conditional update and sent with a dedupe key
    (lock 882). No reminder for a booking made inside its window, or for a patient already in the queue.
  - `CONFIRM`/`YES` and `CANCEL`/`NO` by SMS, and Confirm and Cancel buttons on the web push that the
    service worker posts without opening a window. A cancellation frees the time in the same request.
  - `CANCEL` still means STOP for anyone without a reminded booking open.
  - `GET /sites/{id}/reports/reminders`: attendance grouped by how many reminders were sent, for Issue 93.
- **The check-in tablet at the door** (Issue 83, PR #212; migration `0044`).
  - A paired device with a kind (`display_device.kind`): the same six-character pairing as a board, and the
    same start address, but it opens the check-in screen.
  - A scan, a typed reference or a phone number checks the patient in; a booking becomes its ticket through
    the one conversion path; `ticket.arrived_at` records the arrival once.
  - Large targets, digits-only typing, a 20-second return to idle, no link to browse away to, and "please
    see reception" when the network is down.
  - Optional walk-ins at the door (`site.kiosk_walk_ins_enabled`).
- **Proxy booking for dependants** (Issue 84, PR #213; migration `0045`).
  - `patient_link`: who may act for whom, with the dependant's own `proxy_actions` consent, and a
    verification step — a code to **their** number — before any link to an existing number is made.
  - A dependant with no phone of their own (a small child) is a new record that can never sign in;
    `patient.phone_e164` is nullable for exactly this.
  - The ticket and the booking belong to the dependant, the board shows them under their own consent, and
    `ticket.proxy_patient_id` and the audit trail name who acted. Ending the link refuses the next action at
    once, from either side.
- **Repeating medication collections** (Issue 85, PR #214; migration `0046`).
  - `chronic_schedule`: the queue, the interval, the next due day, the grace period and the join token.
  - The sweep (lock 883, every quarter of an hour) reminds once per cycle, on the first day from the due
    date the clinic **opens**, and never before 08:00; a missed cycle gets **exactly one** follow-up and
    then moves on.
  - One interaction to take a place: the reminder's own button, or a reply of `COLLECT`. A collection rolls
    the repeat forward from the day it happened.
  - The clinic's own list, with its masked numbers, on a new **Repeat collections** settings tab, and
    `GET /sites/{id}/reports/collections` for Issue 90's adherence dashboard.

## Migrations

Eight, all forward-only in the sense that they add; each has a working `downgrade`, and `alembic check` is
clean at every step.

- `0039_appointments`: the appointment book's six tables and `queue_capacity_day`. The slot's
  `booked_count <= capacity` is a CHECK constraint.
- `0040_virtual_waiting_room`: `site.virtual_waiting_enabled`; `ticket.travel_minutes`, `leave_alert_at`,
  `on_my_way_at`.
- `0041_visit_feedback`: `visit_feedback`, one row per completed visit (unique on the visit).
- `0042_booking`: `appointment.reference` (unique; **backfilled** for the bookings #80 had already taken),
  `source`, `rescheduled_from_id`, `converted_at`, `lapsed_at`; `appointment_policy.convert_lead_minutes`;
  `ticket.appointment_id` with `uq_ticket_appointment`.
- `0043_reminders`: `appointment.reminded_24h_at`, `reminded_2h_at`, `confirmed_at`, `reply_token`
  (unique), `cancelled_via`.
- `0044_kiosk_checkin`: `display_device.kind` (every existing device becomes a `board`),
  `ticket.arrived_at`, `site.kiosk_walk_ins_enabled`.
- `0045_patient_links`: `patient_link`; `ticket.proxy_patient_id` and `appointment.proxy_patient_id`;
  **`patient.phone_e164` becomes nullable** (still unique). Its downgrade needs any patient with no number
  removed first, which the migration says.
- `0046_chronic_schedules`: `chronic_schedule`, with one active repeat per patient and queue.

## Upgrade notes

- **Nothing in this release switches itself on.** A clinic has no bookable times until its manager sets
  appointment windows and generates slots; the virtual waiting room, walk-ins at the check-in tablet and
  every collection repeat are off until somebody turns them on.
- **Four new scheduler jobs**, each behind an advisory lock so only one instance runs it: slot generation
  (880, nightly), conversion (881, every minute), appointment reminders (882, every minute) and collection
  reminders (883, every quarter of an hour). A deployment with `SCHEDULER_ENABLED=false` runs none of them,
  and nothing else in the release depends on them.
- **The patient service worker changes** (the reminder's Confirm and Cancel buttons). An existing push
  without a reply path behaves exactly as before.
- **New consent purpose** `proxy_actions`, and new message templates for the two appointment reminders and
  the two collection messages. The template lock file is regenerated; no existing wording changed.
- **The check-in tablet is paired like a board**, at the same `/display` address; a device paired before
  this release keeps showing its board.

## Known issues

- **M10 is not built**, so everything here is web and SMS. USSD and WhatsApp menus, and #79's parity suite,
  are the next milestone's: booking, check-in, proxy actions and collections are written as channel-agnostic
  service functions, and #81 ships a parametrised parity test the suite can adopt, but **"works identically
  on every channel" is not demonstrated for channels that do not exist yet.**
- **No waiting-list offer.** A cancelled appointment time is offered again to everyone at once; nobody is
  told about it (Issue 82's scope mentions a waiting list, which this release does not build).
- **WhatsApp quick-reply buttons** for the appointment reminders wait for #75's inbound adapter; the
  WhatsApp text already asks for `CONFIRM`/`CANCEL`.
- **iOS Safari shows no push action buttons**, so the reminder's one-tap Confirm and Cancel are Android and
  desktop only; the SMS reply is the one-step answer there.
- **A check-in tablet switched on with no network shows the browser's error page.** Unlike the board (#62),
  the check-in screen keeps nothing offline, on purpose: a check-in nobody recorded is worse than none.
- **Guardianship is not verified** beyond a code to the dependant's own number, as Issue 84's scope says.
- **The M12 screens do not exist yet**: the feedback, reminder and collection reports are APIs, and Issues
  89, 90 and 93 draw them.
- **The wording of the feedback question has not been reviewed by F**, the team's research lane; it is a
  draft, like the consent wording M13 reviews.
- **Only `v0.2.0` has ever been tagged**: `v0.1.0` and `v0.3.0`–`v0.10.0` have release notes but no tags,
  and they should be cut in order before this one. M10's note does not exist, because M10 is not done —
  `v0.11.0` is cut after `v0.9.0` with that gap left open.

## Verification

Run on the Issue 85 branch based on `main` after #213, which is `main` as #214 will leave it. PostgreSQL 18 and Redis in Docker, and Playwright's Chromium:

```text
TZ=UTC pytest -q -n auto --dist loadscope tests      2654 passed, 1 skipped, 9 xfailed in 450 s
ruff check . / ruff format --check .                 clean
mypy src/                                            clean (332 files)
alembic upgrade head / check / downgrade / upgrade   clean at 0039 through 0046
```

CI ran every shard on every pull request, including the browser shard, and each merged with every check
green. Each pull request carries its own evidence, and it is worth reading beside the suite:

- **#207 (80):** the last place claimed from two sessions at once on PostgreSQL, a booked place and a
  walk-in counted against one limit, and slot generation skipping a holiday and a closure.
- **#208 (86):** a 30-minute trip alerted once, the alert not costing the place, "On my way" on the front
  desk's board, and the same wait estimate behind the page and the alert.
- **#209 (87):** one question per visit, one tap and one digit answering it, a comment with a phone number
  in it stored redacted, and the clinic's report.
- **#210 (81):** booking, moving (the old time offered again at once) and cancelling from the patient's
  page, two conversion sweeps racing on PostgreSQL making one ticket per booking, and the late rule.
- **#211 (82):** both reminders once each, `CONFIRM` by SMS and `Cancel` on a push, the time back on offer
  0.46 s later, a checked-in patient skipped, and quiet hours holding a 06:00 reminder until 07:00.
- **#212 (83):** a scan checked in in 0.14 s with the board following, the screen clearing itself after 20
  seconds, "please see reception" when the network went, and a board device refused the check-in screen.
- **#213 (84):** a child with no phone joined for, the ticket theirs and the message sent to the parent's
  number, the audit trail naming both, and the link ended refusing the next action. Its demo found two
  PostgreSQL-only bugs (an over-long audit `actor_id`, then its foreign key) that the SQLite suite cannot
  see.
- **#214 (85):** a 28-day repeat reminded once at 09:00, `COLLECT` taking a place in a
  walk-in-only collection queue, the repeat rolling forward from the day the patient actually collected,
  exactly one follow-up for the patient who did not come (three more sweeps sending nothing), and the
  clinic's own list.
