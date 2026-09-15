# PR: A virtual waiting room that tells a travelling patient once when to leave (Issue 86 / M11-86)

**Milestone:** [Milestone 11: Appointments, Check-in & Patient Care Extras](https://github.com/Billykat7/clinicQ/milestone/11) ·
**Issue:** [#86](https://github.com/Billykat7/clinicQ/issues/86) · **Builds on:** #42 (the wait estimate), #43 (recall),
#63 (notifications), #67 (quiet hours and mutes), #68 (the ticket page), all merged

A patient who joins by phone should not have to sit in a crowded waiting room for two hours. With this PR:

- **A clinic manager can switch on a virtual waiting room**, per clinic. It is off by default.
- **Patients who join by phone are then asked how long their trip to the clinic takes.** The question is
  optional: an unanswered one counts as 15 minutes, and "I am at the clinic already" means no alert.
- **The ticket page says when to leave**, for example "Leave by 23:00 for your 30-minute trip.". It works this
  out from the wait estimate the page already shows.
- **The patient is told once that it is time to leave**, by SMS, WhatsApp or push. The alert goes when the
  earliest likely turn is no more than the trip plus 10 minutes away. The message quotes that same estimate.
- **"On my way" is one tap**, and reception sees it on the front desk's waiting line.
- **Ignoring the alert costs nothing.** The ticket keeps its place until staff call it, and from then the
  recall rule (Issue 43) applies as it does to everyone.

No routing or traffic service is used: the patient's own travel time is the only input.

## Summary

- **The rule** (`src/modules/appointments/call_forward.py`, pure):
  - `call_forward(estimate, travel_minutes, moment)` → `CallForward(travel_minutes, leave_at, due)`;
  - `lead = travel + ARRIVAL_MARGIN_MINUTES (10)`, `due = estimate.low_minutes <= lead`,
    `leave_at = moment + max(low − lead, 0)`;
  - `stated_travel(enabled, source, requested)` returns the answer, the default (15) or nothing (the switch is
    off, or it is a walk-in);
  - the manager's explanation text lives next to the rule.
- **One estimate for every surface** (`src/modules/queue/waits.py`): `ticket_wait(db, ticket, queue, moment)`
  returns how many are ahead and the `WaitEstimate`. The ticket page, the answer to a join and the alert all
  read it here.
- **The alert, the sweep and "On my way"** (`src/modules/appointments/virtual_waiting.py`):
  - `check_ticket` locks the ticket row, sets `leave_alert_at` once, and tells the patient through
    `notices.tell(... LEAVE_NOW, wait=estimate.label, minutes=travel)`, deduplicated;
  - `run_call_forward` covers every waiting, travelling, not yet alerted ticket today at clinics with the
    switch on;
  - `acknowledge` records `on_my_way_at` once and refreshes reception's view.
- **The sweep** (`src/core/scheduler.py`): `run_call_forward_sweep` every `VIRTUAL_WAITING_SWEEP_SECONDS`
  (default 30), under advisory lock 886. A join also checks at once, so a patient whose trip is already longer
  than the wait is told immediately.
- **The message**: `PatientEvent.LEAVE_NOW` and `ticket_leave_now` in three channels, with SMS in one GSM-7
  segment. It crosses quiet hours like "you are next", because a late "time to leave" is useless. It can be
  muted from Message settings, and an opt-out stops it.
- **The API:**
  - `JoinIn.travel_minutes` (0–180);
  - `TicketOut.travel_minutes`, `leave_alert_at` and `on_my_way_at`;
  - `TicketPageOut.call_forward`;
  - `POST /api/v1/patients/me/tickets/{ticket_id}/on-my-way` (`queueOnMyWay`, 409
    `ticket.on_my_way.not_offered` or `ticket.on_my_way.ended`);
  - `GET/PUT /api/v1/sites/{site_id}/settings/virtual-waiting` (`sites.settings`, audited).
- **The screens:**
  - the join page's travel question, only when the switch is on;
  - the ticket page's block and **On my way** button;
  - the front desk's "On my way", "Told to leave" and "Away N min" badges, and "N on the way" on the waiting
    line;
  - the switch on the manager's **Settings → Queues** tab.
- **Migration `0040`:** `site.virtual_waiting_enabled` (default false); `ticket.travel_minutes`,
  `leave_alert_at` and `on_my_way_at`; `ck_ticket_travel_minutes_range`.

## Design notes

**"The screen and the alert can never disagree" is one function, not two careful copies.** The page used to
compute `waiting_ahead` and then `estimates_for` inline, and the join answer did the same. Both now call
`ticket_wait`, and the sweep calls it too. The test reads the page and runs the sweep at the same moment, and
asserts that the alert's `wait` is the page's `wait.label` word for word.

**Why the low end of the range, plus 10 minutes.** An estimate is a range, and a turn can come as early as its
low end. A patient who leaves when the low end reaches their trip arrives before the earliest likely turn. The
10-minute margin covers the sweep's interval (at most 30 seconds) and a queue that moves one patient faster
than expected. The page says "Leave by HH:MM" until then, so nobody is sent an hour early. On the demo server a
30-minute trip at 8 people ahead (wait ~50–120) was "Leave by 23:00" at 22:50. One call later (7 ahead,
~40–110) it was due, and one SMS went out.

**An alert moves nothing.** `check_ticket` writes `leave_alert_at` and nothing else. The ticket's status and
order are the lifecycle's alone, and `test_status_written_only_by_lifecycle` fails any bulk `update(Ticket)`.
That is why the claim is a locked row read with an attribute write rather than a conditional `UPDATE`.

**"On my way" is the patient's own action.** It is a `require_patient` route on the patient's own ticket;
another patient's ticket gets the same 404. It is idempotent, audited as a patient update, and wakes the
dashboard through `on_queue_changed`. It is offered before the alert too, because a patient who sets off early
should be able to say so.

**The switch controls everything.** Off means:
- the join page asks nothing;
- a `travel_minutes` sent anyway is not kept;
- the page shows no block;
- the sweep skips the clinic;
- "On my way" answers 409.

It is off by default, so a clinic's walk-in patients never receive an alert its manager did not ask for.

**Human checks not done:** a patient has not waited at home and travelled in on a real phone. The SMS was
read from `/dev/outbox`, not received on a handset.

## Changes

- **New:**
  - `src/modules/appointments/call_forward.py` and `virtual_waiting.py`
  - `alembic/versions/0040_virtual_waiting_room.py`
- **Changed:**
  - Models: `src/database/models/site.py`, `ticket.py`.
  - Queue: `src/modules/queue/waits.py` (`ticket_wait`), `ticket_page.py`, `service.py` (`join_queue(...,
    travel_minutes)`), `router.py` (the join check and `on_my_way`), `schemas.py`.
  - Sites: `src/modules/sites/router.py` and `schemas.py` (the settings pair).
  - Notifications: `src/commons/enums.py` (`LEAVE_NOW`, `TICKET_LEAVE_NOW`, category, urgent, quiet-hours
    exempt), `src/modules/notifications/template_registry.py` (variables; the worst-case `minutes` is now
    180), `src/locales/en/notifications.toml`, `notifications.lock.json`.
  - Config and scheduling: `src/core/config.py` (`virtual_waiting_sweep_seconds`), `.env.example`,
    `src/core/scheduler.py`.
  - Web: `src/web/join.py` and `discover/join.html` with `patient-join.js`; `queue/ticket.html` with
    `ticket.js` and `ticket.css`; `src/web/dashboard/reorder.py`, `_reorder.html` and `dashboard.css`;
    `src/web/dashboard/settings.py` and `settings_queues.html`.
  - Contracts: `contracts/queue.yaml`, `sites.yaml`, `notifications.yaml`.
- **Tests, new:**
  - `tests/unit/appointments/test_call_forward.py` (6)
  - `tests/integration/appointments/test_call_forward.py` (5)
- **Tests, updated:**
  - `test_quiet_hours.py`: the exempt set is four messages, now including time to leave.
  - `test_template_versions.py`: one more template per channel.
  - `test_mutations_are_audited.py`: the `appointments` module is now covered, and #80's routes all pass.
  - `test_site_scoped_queries.py`: three reasoned entries for the sweep's system read, one ticket's queue, and
    the patient's own ticket.
- **Docs:**
  - `docs/OPS/PATIENT_APP_TESTING.md`: a walkthrough for waiting away.
  - `docs/OPS/SMS_GATEWAY.md`: the four messages that cross quiet hours.
  - The Issue 86 spec (files), the M11 status row and bars (`--assume-closed 86`), the README Status block (75
    of 111), and sprint 10's row.

## Testing

- [x] `ruff check .`, `ruff format --check .` (651 files) and `mypy src/` (314 files) are clean.
- [x] **Migration:** `alembic check` on a database at 0040 reports "No new upgrade operations detected".
  `downgrade 0039` and `upgrade head` both run cleanly.
- [x] **Full suite:** `TZ=UTC pytest tests/ -n auto --dist loadscope` on the Docker PostgreSQL 18 and Redis, with
  browsers, gave **2551 passed, 1 failed, 1 skipped, 9 xfailed**. The failure was
  `test_nothing_outside_transition_ticket_writes_a_tickets_status`: the first version claimed the alert with an
  `update(Ticket)`, which the lifecycle guard refuses. It now locks the row and writes the attribute. That guard,
  the site-scope guard and the appointments suites then pass (49 passed, 5 skipped for Redis).
- [x] **How to verify, steps 1 to 3, on a running server.** Playwright drove the demo world on PostgreSQL
  (`clinicq_m11_demo` at 0040, Hillbrow opened around the clock so the evening run could join, General
  consultation at 10 minutes, the sweep every 10 seconds) through the real screens. The output:

  ```text
  1. switch: {'virtual_waiting_enabled': True, 'default_travel_minutes': 15, 'explanation': 'Patients who join by phone are asked …'}
  3. page: Leave by 23:00 for your 30-minute trip.  On my way | Estimated wait ~50–120 min (approximate)
     called A001; page said Estimated wait ~50–120 min (approximate)
  4. page: Time to leave now: your trip takes 30 minutes.  On my way | Estimated wait ~40–110 min (approximate)
     outbox: {"channel": "sms", "to": "+27825550186", "text": "BK ClinicQ: ticket A009 at Hillbrow Community Health...: time to leave. Your turn is in ~40-110 min (approximate) and your trip is 30 min.", …} | count 1
  5. reception line: Waiting line (8) 1 on the way | … | 8 | A009 | Web | 0 min |
  6. off: travel question present: False
  ```

  The patient signed in with a phone number and the code from `/dev/outbox`, said yes to messages, chose
  General consultation and picked **30 minutes**. Reception issued 8 walk-ins and pressed Call next once. The
  badge's title reads "Said at 22:50, a 30-minute trip".
- [x] **Over HTTP** (`tests/integration/appointments/test_call_forward.py`, 5 passed, in UTC):
  - **30-minute trip:** 8 walk-ins at 10 minutes each, then the line is called down one patient at a time,
    reading the page and running the sweep at each step:
    - the alert is sent exactly once, when the page's low end is between 30 and 40 minutes;
    - one step earlier the low end was above 40;
    - the alert's `wait` equals the page's `wait.label` and its `minutes` is 30;
    - three more sweeps send nothing.
  - **On my way:** 200 with `on_my_way_at`; a second tap keeps the same time; another patient gets 404; the
    clinic's `GET /sites/{id}/tickets` shows `on_my_way_at` and `travel_minutes`; the page stops offering it.
  - **Unanswered:** a 60-minute trip is alerted at join. Five sweeps over five hours leave it `waiting` in the
    same position. Called, it is not recalled at 1 minute; at 6 minutes the recall timer recalls it.
  - **Switched off:**
    - off is the default; the desk gets 403 and the other clinic 404;
    - a join with 120 minutes keeps nothing, the page has no block, and the sweep tells nobody;
    - "On my way" returns 409 `ticket.on_my_way.not_offered`.
  - **Join page:** no travel choices while off; while on, the choices start `0, 5, 10, 15` ("I am at the clinic
    already" first) with 15 selected. An unsaid trip is stored as 15. Clinic B, which is off, keeps nothing.
- [x] **Unit** (`test_call_forward.py`, 6 passed):
  - a 30-minute trip at low ends of 60, 41, 40 and 25 gives leave at 09:20 or 09:01 (not due), or 09:00 (due);
  - no trip or 0 gives no plan;
  - `stated_travel` returns the answer, the default, off or a walk-in.
- [x] **Notifications:**
  - the unit and integration notification suites pass (170), including the new template in one SMS segment
    at the worst-case names and a 180-minute trip;
  - quiet hours send it at once;
  - the template editor lists it.

| The join page asks for the trip (390 px) | Before it is time (390 px) | Time to leave (390 px) | On my way (390 px) |
|---|---|---|---|
| ![Choose a queue with the question How long does your trip to the clinic take, set to 30 minutes](https://github.com/Billykat7/clinicQ/blob/f1012aba7492fafa46624323ec3f9375182a08c9/docs/GITHUB/PR/M11/assets/pr86/join-travel-question-390.png?raw=true) | ![The ticket page saying Leave by 23:00 for your 30-minute trip, with an On my way button](https://github.com/Billykat7/clinicQ/blob/f1012aba7492fafa46624323ec3f9375182a08c9/docs/GITHUB/PR/M11/assets/pr86/ticket-leave-by-390.png?raw=true) | ![The ticket page saying Time to leave now: your trip takes 30 minutes](https://github.com/Billykat7/clinicQ/blob/f1012aba7492fafa46624323ec3f9375182a08c9/docs/GITHUB/PR/M11/assets/pr86/ticket-time-to-leave-390.png?raw=true) | ![The ticket page saying Reception knows you are on your way](https://github.com/Billykat7/clinicQ/blob/f1012aba7492fafa46624323ec3f9375182a08c9/docs/GITHUB/PR/M11/assets/pr86/ticket-on-my-way-390.png?raw=true) |

| Reception sees it | The manager's switch | Switched off: no question |
|---|---|---|
| ![The General consultation card with Waiting line, 1 on the way, and A009 marked On my way](https://github.com/Billykat7/clinicQ/blob/f1012aba7492fafa46624323ec3f9375182a08c9/docs/GITHUB/PR/M11/assets/pr86/reception-on-my-way-1366.png?raw=true) | ![The Virtual waiting room switch, ticked, with its explanation](https://github.com/Billykat7/clinicQ/blob/f1012aba7492fafa46624323ec3f9375182a08c9/docs/GITHUB/PR/M11/assets/pr86/manager-switch-on-1366.png?raw=true) | ![Choose a queue with only the reason field, no travel question](https://github.com/Billykat7/clinicQ/blob/f1012aba7492fafa46624323ec3f9375182a08c9/docs/GITHUB/PR/M11/assets/pr86/join-no-question-when-off-390.png?raw=true) |

## Acceptance criteria

- [x] **A patient with a 30-minute travel time is alerted with enough lead time to arrive before their turn:**
  the alert went when the earliest likely turn was 30–40 minutes away (never under 30), and not one step
  earlier. On the demo server that was ~40–110 min with 7 ahead. Not yet tried with a real journey (above).
- [x] **The acknowledgement is visible to reception on the dashboard:** "On my way" and "1 on the way" appear on
  the front desk's waiting line (screenshot), and `on_my_way_at` is in the clinic's ticket list over HTTP.
- [x] **An unacknowledged alert does not lose the patient's place before the recall rule applies:** five hours
  of sweeps left the ticket waiting in the same place. Only after being called did the recall timer move it,
  on its own 5-minute clock.
- [x] **The estimate used for the alert is the same one shown to the patient:** both come from `ticket_wait`,
  and the alert's wait text equals the page's at the same moment.
- [x] **The feature can be disabled per site:** off is the default. Off, nothing is asked, kept or sent, and
  "On my way" is refused. Clinic B stays off while A is on.
- [x] **Travel-time input is optional and defaults sensibly:** unsaid is 15 minutes, "I am at the clinic
  already" is 0 (no alert), and choices run up to 2 hours, with 180 minutes accepted over the API.

## Risk and rollback

- **The join page, ticket page and front desk change only at a clinic that switches it on.** Every existing
  clinic is off after the migration, and every existing test and browser test passes.
- **One more interval job**, which reads nothing unless some clinic has the switch on and a travelling patient
  is waiting.
- **The worst-case `minutes` in the template check went from 60 to 180.** The recall SMS still fits one
  segment; its test passes.
- **Rollback:** revert, then `alembic downgrade 0039`, which drops the four columns. Places and tickets are
  untouched.

Closes #86
