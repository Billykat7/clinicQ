# PR: Repeating medication collections, with one word to take a place and exactly one follow-up (Issue 85 / M11-85)

**Milestone:** [Milestone 11: Appointments, Check-in & Patient Care Extras](https://github.com/Billykat7/clinicQ/milestone/11) ·
**Issue:** [#85](https://github.com/Billykat7/clinicQ/issues/85) · **Builds on:** #63 (the notification
service), #82 (reminders answered by reply, PR #211), #40 (the join service), all merged · **Closes the
milestone:** this pull request (#214) also carries the [`v0.11.0` release note](../../RELEASES/RELEASE_v0_11_0.md)

A patient on chronic treatment comes back every 28 days, forever, and missing one collection is how people
fall off treatment. With this PR:

- **The clinic sets a repeat up** on a new **Repeat collections** settings tab: a phone number, a collection
  queue, a first date, the interval and the grace period.
- **The sweep reminds once per cycle**, on the first day from the due date the clinic actually **opens**, and
  never before 08:00. However many sweeps run, one message goes.
- **One interaction takes a place**: the reminder's own button posts its token, and a reply of `COLLECT` does
  the same over SMS — through Issue 40's `join_queue`, so the ticket is in the same sequence as everyone
  else's, and the patient is told their number.
- **A missed collection gets exactly one follow-up**, after the grace period, and the cycle then moves on:
  the patient is reminded again next month, and never nagged in between.
- **Collecting rolls the repeat forward from the day it happened**, not the day it was due, which is how a
  month's supply is actually counted.
- **Stopping works from any channel**: `STOP` stops every message, as it always has, and the clinic can stop
  a repeat from its own screen.
- **Adherence is reportable** per collection queue, for #90's dashboard.

**Not done here, and not claimed:**

- **No USSD or WhatsApp menu** (M10): the reminder's one-word reply works on any channel that carries words,
  and the WhatsApp text already says `COLLECT`, but the adapters do not exist.
- **The adherence *screen*** is Issue 90's; this ships the numbers it reads.
- **A patient cannot set up their own repeat.** A repeat is a clinical fact about a course of treatment, so
  the clinic sets it up; the patient can stop the messages at any time.

## Summary

- **The model** (migration `0046`): `chronic_schedule` — the clinic, the collection queue, the patient, the
  interval and grace period, `next_due_on`, `last_collected_on`, `reminded_for`, `followed_up_for`, the join
  token and `stopped_at`. One active repeat per patient and queue.
- **The sweep** (`src/modules/appointments/chronic.py`, `run_chronic_collections`, lock 883, every quarter
  of an hour):
  - `run_due` reminds, follows up **once**, and rolls the cycle on;
  - `collected`, called from the lifecycle's `done` hook, moves the repeat forward when a collection
    actually happens;
  - `join_now`, `by_token` and `join_by_phone` are the one tap;
  - `create`, `stop`, `schedules_at` and `adherence` are the clinic's own half.
- **Messages:** `collection_due` and `collection_missed` for SMS, WhatsApp and web push, each one segment
  with worst-case names. A message about a repeat has **no ticket number**, so the registry now asks for
  `{number}` only where a template has one.
- **The API** (documented in `contracts/appointments.yaml`):
  - `GET/POST /api/v1/sites/{id}/collection-schedules` and `POST …/{schedule_id}/stop` (`appointments`);
  - `POST /api/v1/collections/joins/{token}` — public, because a notification carries no session;
  - `GET /api/v1/sites/{id}/reports/collections`.
- **The screen** (`dashboard/settings_collections.html`): who is due, when, what has been sent this cycle,
  and a Stop button. A patient is identified by their **masked number**, never by name.

## Design notes

**"Exactly one" is a column, not an intention.** `reminded_for` and `followed_up_for` hold the **due date**
each message was sent for. A second sweep in the same cycle matches neither branch, and a sweep that runs
four days late still sends one message, for the cycle it is about. The test runs the sweep four times over
four days and counts two messages in total.

**A collection is a visit, so the reminder waits for an open day.** The sweep looks forward from the due
date for the first day the clinic opens (up to a fortnight) and sends on that day. Asking somebody to come
on a day the doors are shut wastes a taxi fare they may not have.

**A queue that takes walk-ins only still takes the patient it invited.** Most collection queues are
walk-in-only in a clinic's own configuration. `join_queue` gains `invited=True`, used only here: the clinic
put this patient on this queue's repeat list and asked them to come today, exactly as it accepted a booking
(#81 already passes the same rule for `appointment=`). The queue's hours, its daily capacity and every other
rule are unchanged, and a patient with no repeat there is refused as before — both halves are tested.

**The cycle moves on after a missed collection.** Rolling `next_due_on` forward once the follow-up has gone
is what makes "exactly one" hold across months: the next cycle is a new due date with its own single
reminder, rather than the same one retried forever.

**Stopping is two different things, kept apart.** `STOP` is the patient's, and stops every message on every
channel through the notification service's own opt-out. Stopping a **repeat** is the clinic's, and ends the
schedule. A patient who has opted out still appears on the clinic's list, because the pharmacy still has to
plan for their collection — that is written on the screen.

## Changes

- **New:**
  - `src/modules/appointments/chronic.py`, `chronic_router.py`, `chronic_schemas.py`
  - `src/database/models/chronic_schedule.py`, `alembic/versions/0046_chronic_schedules.py`
  - `src/templates/dashboard/settings_collections.html`
  - `tests/integration/appointments/test_chronic_reminders.py` (10)
  - `docs/GITHUB/RELEASES/RELEASE_v0_11_0.md`
- **Changed:**
  - Enums: `PatientEvent.COLLECTION_DUE`/`COLLECTION_MISSED`, the two templates, `SMS_COLLECT_KEYWORDS`,
    `ReplyOutcome.COLLECTING`.
  - Queue: `service.py` (`invited`), `lifecycle.py` (the `done` hook rolls the repeat forward).
  - Notifications: `template_registry.py` (a message with no ticket number), `src/locales/en/notifications.toml`,
    `notifications.lock.json`.
  - Wiring: `src/core/scheduler.py` (lock 883), `src/api/v1/router.py`,
    `src/core/webhook_gateways/africastalking.py` (`COLLECT`).
  - Dashboard: `src/web/dashboard/settings.py` (a seventh settings tab).
  - Contracts: `contracts/appointments.yaml`.
- **Tests, updated:**
  - `test_push_payload.py` and `test_transport_plan.py`: a message about a repeat has no ticket number to
    carry, so the sweep over every template asks for one only where the template has one.
  - `test_api_route_gates.py`: the public join route, with its reason.
  - `test_site_scoped_queries.py`: ten reasoned entries for the sweep's system reads and the clinic's list.
  - `test_manager_settings.py`: the front desk now reads three tabs.
- **Docs:** the Issue 85 spec (files), `docs/OPS/SMS_GATEWAY.md` (`COLLECT`),
  `docs/OPS/PATIENT_APP_TESTING.md`, **the M11 exit criteria re-derived from the code**, the milestone's
  status and bars (`--assume-closed 85`), the README Status block (81 of 111), and sprint 11's row.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (332 files) are clean.
- [x] **Migration:** `alembic check` reports "No new upgrade operations detected"; `downgrade 0045` and
  `upgrade head` both run cleanly, and 0046 ran on the demo database carrying #80 to #84's data.
- [x] **Full suite:** `TZ=UTC pytest tests/ -n auto --dist loadscope` on the Docker PostgreSQL 18 and Redis,
  with browsers, gave **2654 passed, 1 skipped, 9 xfailed**, with no failures. The first run of it failed on one
  guard — the cross-tenant sweep, which refuses a new site-scoped model with no case — and the case for
  `chronic_schedule` was added, which is what that guard is for.
- [x] **How to verify, on a running server.** Playwright and the real sweep drove the demo world on
  PostgreSQL (`clinicq_m11_demo` at 0046): two patients, one who collects and one who does not.

  ```text
     agreed to messages: 2
  1. schedules set up: ['28 days, due 2026-09-12', '28 days, due 2026-09-16']
  2. the sweep, twice, at 09:00 today
    run 1 reminded 1 followed up 1 rolled 1
    run 2 reminded 0 followed up 0 rolled 0
     +2782555xx62 -> BK ClinicQ: we did not see you for your collection at Hillbrow Community Health.... Please come in, or reply COLLECT for a place in the queue.
     +2782555xx61 -> BK ClinicQ: your medicine collection at Hillbrow Community Health... is due Wed 16 Sep. Reply COLLECT to take a place in the queue today.
  3. reply COLLECT -> {"received":true,"outcome":"collecting","event_id":"africas_talking:cc-44"} 200
     the ticket it made: ['C006 waiting in Chronic medication collection']
     collected; the repeat now reads: ['last collected 2026-09-16, next due 2026-10-14']
  4. three more sweeps past the grace period: nothing more is sent
    run 1 reminded 0 followed up 0 rolled 0
    run 2 reminded 0 followed up 0 rolled 0
    run 3 reminded 0 followed up 0 rolled 0
     what each schedule has been sent: ['…62 reminded never, followed up 2026-09-12, next due 2026-10-10',
                                        '…61 reminded 2026-09-16, followed up never, next due 2026-10-14']
  5. stop: 200 (and the next sweep sends nothing)
     stopped: ['manager@clinicq.example']
  6. adherence: 200 [{"queue_name": "Chronic medication collection", "schedules": 6, "due": 3,
                      "collected_on_time": 1, "collected_late": 0, "missed": 2}]
  ```

  - **Line 2** is "once per cycle": the second run of the same sweep sends nothing.
  - **Line 3**: the collection queue at this clinic takes **walk-ins only**, and the patient the clinic
    invited still got `C006` — the reason `invited` exists. Taking that ticket to done moved the repeat to
    14 October, 28 days from the day they came.
  - **Line 4** is "exactly one follow-up": three more sweeps, nothing.
- [x] **Over HTTP and the sweep** (`test_chronic_reminders.py`, 10 passed, at fixed times):
  - **Once per cycle:** nothing at 06:00, one reminder at 09:00, nothing at 14:00; then the collection rolls
    the repeat 28 days from the day it happened.
  - **Exactly one follow-up:** nothing inside the grace period, one message after it, nothing on the two
    days after that, and the cycle moved on.
  - **One interaction:** the token takes a place, a second tap answers with the same number and makes no
    second ticket, and an unknown token is 404 `collections.not_found`.
  - **`COLLECT` by SMS** answers `collecting` and makes the same ticket.
  - **Stopping:** a stopped repeat sends nothing and its join link is 409 `collections.stopped`; a patient's
    `STOP` leaves the next reminder `suppressed` by the notification service.
  - **A day the clinic is shut:** nothing on the due day, one reminder on the next open day.
  - **A walk-in-only queue** takes the invited patient and still refuses a stranger (`queue.join.walk_in_only`).
  - **Adherence:** two repeats, one collected on time, one missed; another clinic's report is the same 404.
  - **The clinic's own management:** set up by phone number, corrected rather than duplicated, read by the
    front desk, stopped by the manager, and invisible to another clinic.

| The clinic's Repeat collections tab | After the sweep, a collection and a stop |
|---|---|
| ![Set up a repeat collection, with the list of who is due](https://github.com/Billykat7/clinicQ/blob/fff1aaad3dbdd3cab56ee639b03b38d51a596f26/docs/GITHUB/PR/M11/assets/pr85/collections-tab-1440.png?raw=true) | ![The list showing reminded dates, one patient's last collection and the next due date](https://github.com/Billykat7/clinicQ/blob/fff1aaad3dbdd3cab56ee639b03b38d51a596f26/docs/GITHUB/PR/M11/assets/pr85/collections-after-1440.png?raw=true) |

## Acceptance criteria

- [x] **A recurring schedule sends reminders on time and rolls forward after each collection:** one reminder
  per cycle on the first open day from the due date, and `next_due_on` set an interval from the day the
  collection happened.
- [x] **The reminder's join action creates a ticket in one interaction:** the push button's token, or one
  word by SMS; the ticket comes from the same counter as every other.
- [x] **A missed collection triggers exactly one follow-up, not a repeating nag:** `followed_up_for` holds
  the cycle it was sent for, and the cycle then moves on. Proved with four sweeps and on the demo server.
- [x] **A patient can stop reminders from any channel:** `STOP` on any channel stops every message (the next
  reminder is recorded `suppressed`), and the clinic can stop the repeat itself.
- [x] **Adherence is reportable per site and per service:** `GET /sites/{id}/reports/collections`, per
  collection queue, with on-time, late and missed counts. The M12 screen is #90's.
- [x] **Reminders respect quiet hours, opt-outs and clinic opening days:** the first two because every send
  goes through the notification service, which #82's suite also covers; the third is this module's own rule,
  tested with a day the clinic does not open.

## Risk and rollback

- **A new quarter-hourly sweep**, which reads only live schedules whose cycle is near.
- **`join_queue` gains `invited`.** Every existing caller passes nothing and behaves as before; only a
  collection reminder sets it.
- **The lifecycle's `done` hook does one more thing**, which returns immediately unless that patient has a
  repeat in that queue.
- **A seventh settings tab**, readable with the `appointments` grant the front desk already holds.
- **Rollback:** revert, then `alembic downgrade 0045`. The schedules are dropped; the tickets they created
  stay, because they are ordinary tickets.

Closes #85
