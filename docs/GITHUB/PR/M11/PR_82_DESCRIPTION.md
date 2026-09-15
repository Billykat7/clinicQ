# PR: Remind a booked patient a day and two hours before, and let them confirm or cancel by reply (Issue 82 / M11-82)

**Milestone:** [Milestone 11: Appointments, Check-in & Patient Care Extras](https://github.com/Billykat7/clinicQ/milestone/11) ·
**Issue:** [#82](https://github.com/Billykat7/clinicQ/issues/82) · **Builds on:** #81 (booking and conversion, PR #210),
#63 (the notification service), #64 (web push), #65 (the SMS gateway), #67 (preferences, quiet hours and STOP), all merged · **Unblocks:** #85, #93

A booking from #81 is now followed by two reminders, and the patient can answer either one without opening
anything. A time the patient no longer needs goes back on offer the moment they say so. With this PR:

- **Two reminders per booking, once each**:
  - the **day before** (from 24 hours ahead), if the booking was made more than a day ahead;
  - **two hours before**, if it was made more than two hours ahead.

  Both go through the notification service, so the patient's preferred transport, quiet hours, mutes, consent
  and opt-out apply. None of those is re-checked in the reminder code. However many sweeps run, a reminder
  goes once.
- **A reply confirms or cancels, with no app opened**:
  - **SMS:** `CONFIRM` or `YES` keeps the booking; `CANCEL` or `NO` cancels it;
  - **web push:** the reminder carries **Confirm** and **Cancel** buttons, and the service worker posts the
    answer without opening a window.
- **A cancellation frees the time in the same request.** On the demo server the time was offered again 0.46 s
  after the push Cancel was sent.
- **A patient already in the queue gets no reminder.** This covers a booking that became a ticket, and a
  patient holding a ticket in that queue that day.
- **What #93 needs is recorded and counted.** For each clinic, bookings are grouped by how many reminders they
  got (0, 1 or 2), and each group counts bookings, confirmations, cancellations by reply, attended visits and
  no-shows.

**Not done here, and not claimed:**

- **"Offered to a waiting list":** there is no appointment waiting list in ClinicQ yet. The freed time is
  offered again to everyone at once, on the booking page and the public day view; nobody is notified.
- **WhatsApp quick-reply buttons:** WhatsApp inbound (#75) is M10 and not built. `reminders.reply_by_phone` is
  the function its adapter will call, the same one the SMS webhook calls. The WhatsApp reminder text already
  asks for CONFIRM or CANCEL.
- **iOS push buttons:** Safari shows web notifications without action buttons. There, the SMS reply is the
  one-step answer. This is written in `docs/OPS/WEB_PUSH.md`.

## Summary

- **The model** (migration `0043`): on `appointment`,
  - `reminded_24h_at` and `reminded_2h_at`;
  - `confirmed_at`;
  - `cancelled_via` (`sms`, `web_push` or `whatsapp`);
  - `reply_token` (unique): the unguessable key a push button answers with. `claim_place` draws it for every
    new booking.
- **Reminders** (`src/modules/appointments/reminders.py`):
  - `send_due` is the sweep body. Each reminder is claimed with a conditional `UPDATE` of its `reminded_*_at`
    column, then sent with dedupe key `{booking}:{event}`. `run_appointment_reminders` runs every minute under
    advisory lock 882.
  - `apply` confirms (`confirmed_at`) or cancels (`capacity.release_place`, `cancelled_via`), and writes an
    audit row naming the channel.
  - `reply_by_phone` answers the patient's soonest reminded booking.
  - `by_token` finds a push button's booking.
  - `reminder_outcomes` feeds the report.
- **SMS** (`africastalking.process_reply`): a booking reply is tried first, then STOP/START, then #87's
  feedback digits.
- **Push:**
  - `RenderedMessage.reply_url` becomes the payload's `reply`;
  - `patient-sw.js` shows Confirm and Cancel when `reply` is present;
  - a tap on a button posts `{reply}` to that path and opens nothing, while a tap on the body still opens the
    page.
- **Messages:**
  - `appointment_reminder_24h` and `appointment_reminder_2h` for SMS, WhatsApp and web push;
  - one SMS segment each with worst-case names;
  - the push text says only the reference and the clinic.
- **The API** (documented in `contracts/appointments.yaml`):
  - `POST /api/v1/appointments/replies/{token}`: public, because the token is the credential, like the ticket
    page;
  - `GET /api/v1/sites/{site_id}/reports/reminders?start=&end=`: `sites.reports` read.
  - A booking now shows `reminded_24h_at`, `reminded_2h_at` and `confirmed_at`, and a confirmed booking reads
    "Confirmed for … See you then."

## Design notes

**CANCEL already meant STOP.** Since #67, `CANCEL` opts a patient out of every message. Taking it over for
bookings would break that promise for everyone else. The rule is:

- `CANCEL` cancels a booking only when the sender has a **reminded** booking still open;
- from anyone else it is still a STOP.

Both cases are tested. A patient who has just been asked "Reply CONFIRM or CANCEL" means the booking, and a
cancelled booking can be booked again, whereas a STOP silences safety messages too.

**The soonest reminded booking answers a keyword.** An SMS reply names no booking, and a patient rarely holds
two reminded bookings at once. The soonest is the one the latest reminder was about, or will be first.

**A push button carries a token, not a session.** A service worker's `fetch` from a notification may run with
no page and no fresh sign-in. The reminder carries `/api/v1/appointments/replies/{token}`:

- the token is 32 random bytes and unique;
- a wrong or reused token is the same 404 as a missing one;
- an answer to a booking no longer booked is `applied: false`, and changes nothing.

**Claim, then send, in one savepoint.** The conditional `UPDATE` matches nothing on a second sweep, so two
servers never both send. If the send's ledger write fails, the savepoint rolls the claim back and the next
sweep tries again.

**No reminder inside a window the booking was made in.** A booking made three hours ahead already got its
confirmation, so a "tomorrow" reminder would be wrong. It gets only the two-hour one.

**The record for #93 lives on the booking.** The reminder times, confirmation and cancellation channel stay
on the booking row; the ticket's outcome is joined through `ticket.appointment_id`. #93 can therefore compare
attendance with and without reminders without reading the notification ledger, which retention (#95) trims.

## Changes

- **New:**
  - `src/modules/appointments/reminders.py`
  - `alembic/versions/0043_reminders.py`
  - `tests/integration/appointments/test_reminders.py` (9)
- **Changed:**
  - Appointments: `booking_router.py` (reply and report routes), `booking.py` (the confirmed sentence), `capacity.py`
    (`reply_token`), `schemas.py`.
  - Models and enums: `src/database/models/appointment_slot.py`; `src/commons/enums.py` (`REMINDER_24H`,
    `REMINDER_2H`, the two templates, `BookingReply`, `BookingReplyChannel`, the keywords, the `confirmed` and
    `cancelled` reply outcomes).
  - Notifications:
    - `template_registry.py` (blanks, `reply_url`) and `schemas.py`;
    - `transports/webpush.py` (`reply` in the payload) and `patient_preferences.py`;
    - `src/locales/en/notifications.toml` and `notifications.lock.json`.
  - Wiring: `src/core/scheduler.py` (lock 882), `src/core/webhook_gateways/africastalking.py`.
  - Web: `src/static/patient-sw.js`.
  - Contracts: `contracts/appointments.yaml`, `contracts/notifications.yaml` (template, event and outcome enums).
- **Tests, updated:**
  - `test_openapi_contracts.py`: the appointments contract claims `/appointments/replies/` and
    `/sites/{site_id}/reports/reminders`.
  - `test_api_route_gates.py`: the public reply route, with its reason.
  - `test_site_scoped_queries.py`: five reasoned entries for the sweep's system reads and the token and phone
    lookups.
- **Docs:**
  - `docs/OPS/SMS_GATEWAY.md` (CONFIRM/CANCEL, and when CANCEL is still STOP);
  - `docs/OPS/WEB_PUSH.md` (the buttons, and iOS);
  - the Issue 82 spec (files);
  - the M11 status row and bars (`--assume-closed 82`), the README Status block (78 of 111), and sprint 11's row.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (324 files) are clean.
- [x] **Migration:** `alembic check` reports "No new upgrade operations detected". `downgrade 0042` and
  `upgrade head` both run cleanly. 0043 ran on the demo database, which holds #81's bookings.
- [x] **Full suite:** `TZ=UTC pytest tests/ -n auto --dist loadscope` on the Docker PostgreSQL 18, with browsers,
  gave **2598 passed, 13 skipped, 9 xfailed**, with no failures. The run did not set `TEST_REDIS_URL`, so the Redis
  tests skipped; `pytest -m redis` against the compose Redis then gave **13 passed**.
- [x] **How to verify, on a running server.** A Playwright script drove the demo world on PostgreSQL
  (`clinicq_m11_demo` at 0043, the server on port 8050), in phone-sized browsers:
  1. Two patients, who both said yes to messages, booked Mon 21 Sep 10:00 at Chronic medication collection,
     which holds two places. 10:00 disappeared from the list.
  2. The real `reminders.send_due` ran twice, in a separate process against the same database, at 24 hours
     before.
  3. Patient A replied `CONFIRM` to the Africa's Talking inbound webhook.
  4. Patient B's reminder Cancel button was sent exactly as the service worker sends it.
  5. The sweep ran twice more at 2 hours before.
  6. The manager read the report.

  ```text
  1. two patients booked 2026-09-21 10:00 (capacity 2): GKX-TDM 23P-RHX
     times offered after both: ['09:00 · …', '09:30 · …', '10:30 · …']
  2. the day before, at 2026-09-20T10:00:00+02:00
    sweep 1 24h: 2 2h: 0 checked in: 0
    sweep 2 24h: 0 2h: 0 checked in: 0
     sms +27825550829 -> BK ClinicQ: reminder, Mon 21 Sep 10:00 at Hillbrow Community Health..., ref GKX-TDM. Reply CONFIRM to keep it or CANCEL to free the time.
     sms +27825550830 -> BK ClinicQ: reminder, Mon 21 Sep 10:00 at Hillbrow Community Health..., ref 23P-RHX. Reply CONFIRM to keep it or CANCEL to free the time.
  3. A replies CONFIRM by SMS -> {"received":true,"outcome":"confirmed","event_id":"africas_talking:demo-82-3"} 200
     A's bookings: Mon, 21 Sept 10:00 · Chronic medication collection | Hillbrow Community Health Centre · Confirmed for Mon 21 Sep 10:00, reference GKX-TDM. See you then. | Move to another time | Cancel this booking
  4. B taps Cancel on the push -> {"reply":"cancel","applied":true,"message":"Your booking is cancelled. The time is free for someone else."} 200
     10:00 offered again after 0.46 s: ['09:00 · …', '09:30 · …', '10:00 · …', '10:30 · …']
     cancelled_via: cancelled web_push
  5. two hours before, A (confirmed) is reminded again; B (cancelled) is not
    sweep 1 24h: 0 2h: 1 checked in: 0
    sweep 2 24h: 0 2h: 0 checked in: 0
     sms +27825550829 -> BK ClinicQ: in 2 hours, Mon 21 Sep 10:00 at Hillbrow Community Health..., ref GKX-TDM. Reply CONFIRM or CANCEL.
  6. reminders report: 200 {"start": "2026-09-21", "end": "2026-09-21", "groups": [
       {"reminders": 0, "bookings": 0, ...},
       {"reminders": 1, "bookings": 1, "confirmed": 0, "cancelled_by_reply": 1, "attended": 0, "no_show": 0},
       {"reminders": 2, "bookings": 1, "confirmed": 1, "cancelled_by_reply": 0, "attended": 0, "no_show": 0}]}
  ```

  The 0.46 s covers the whole round trip: the reply POST answered, then the patient's page asked for that
  day's times again and drew them.

  An earlier run of the same script showed the reminders `suppressed` with "SMS is switched off: SMS_ENABLED".
  The sweep's process had not been given the server's settings. That is the notification service refusing,
  correctly, not the reminder code. The run above passes the server's environment.
- [x] **Over HTTP and the sweep** (`test_reminders.py`, 9 passed, in UTC, at fixed times two days ahead):
  - **Both reminders, once each, on the preferred transport:**
    - sweeps at 24 h 1 min, 23 h 59 min, 3 h, 2 h and 30 min before send nothing, the day-before reminder, nothing,
      the two-hour reminder, and nothing;
    - both are on SMS, the patient's preferred transport, while push was also available.
  - **Booked inside a window:** a booking made 3 hours ahead gets no day-before reminder, only the two-hour one.
  - **SMS reply:**
    - `Confirm` answers `confirmed` and sets `confirmed_at`;
    - `CANCEL please` answers `cancelled`, leaves the slot's `booked_count` at 0 in the webhook's own request,
      and records `cancelled_via` `sms`;
    - that patient is **not** opted out.
  - **CANCEL still means STOP** from a patient with no reminded booking (`stopped`).
  - **Push button:**
    - the rendered push payload's `reply` is the token path;
    - a POST `cancel` is applied with the patient's sentence, and a second answer is `applied: false`;
    - an unknown token is 404, and `cancelled_via` is `web_push`.
  - **Already checked in:** a patient who joined the queue three hours before is skipped at the two-hour mark
    (`checked_in`), and no message is recorded.
  - **Opt-out:** an opted-out patient's reminder is recorded `suppressed` by the notification service.
  - **Quiet hours:** a 08:00 booking's two-hour reminder falls at 06:00, inside quiet hours of 21:00 to 07:00. It
    is claimed and queued, held until 07:00.
  - **Report:**
    - a reminded, confirmed booking that was attended counts under 2 reminders;
    - a booking made an hour ahead, never reminded, then a no-show, counts under 0;
    - the front desk gets 403.

| Confirmed by an SMS reply (390 px) | The time offered again after a push Cancel |
|---|---|
| ![Your bookings: Confirmed for Mon 21 Sep 10:00, reference GKX-TDM. See you then.](https://github.com/Billykat7/clinicQ/blob/5bb378e781753ccc5267c60e0184851526dc0a6e/docs/GITHUB/PR/M11/assets/pr82/reminder-confirmed-by-sms-390.png?raw=true) | ![Mon 21 Sep offering 09:00, 09:30, 10:00 and 10:30 again once the other patient cancelled](https://github.com/Billykat7/clinicQ/blob/5bb378e781753ccc5267c60e0184851526dc0a6e/docs/GITHUB/PR/M11/assets/pr82/reminder-cancelled-time-offered-again-390.png?raw=true) |

## Acceptance criteria

- [x] **Reminders are sent at both intervals on the correct transport:** at 24 hours and 2 hours, once each, on
  the patient's preferred transport, chosen by the notification service. The demo server sent both by SMS.
- [x] **A reply cancels or confirms without opening any app:**
  - SMS `CONFIRM`/`CANCEL` goes to the inbound webhook;
  - the push Confirm/Cancel buttons post from the service worker with no window.

  WhatsApp buttons wait for #75, and are noted above.
- [x] **A cancellation frees the slot within seconds:** in the same request (`booked_count` 0 when the webhook
  answers). On the demo server the time was back on the page 0.46 s after the Cancel.
- [x] **An already-checked-in patient receives no reminder:** a patient already holding a ticket in that queue that
  day is skipped, and a converted booking is no longer `booked`.
- [x] **Reminder-to-attendance correlation is measurable per site:** `GET /sites/{id}/reports/reminders` groups
  bookings by reminders sent, with confirmations, cancellations by reply, attendance and no-shows. The M12
  screen is #89/#93's.
- [x] **Reminders respect quiet hours and opt-outs:** a reminder due at 06:00 is held until the patient's quiet
  hours end, and an opted-out patient's is suppressed. The notification service does both; the reminder code
  re-checks neither.

## Risk and rollback

- **`CANCEL` changes meaning for a patient with a reminded booking.** It cancels that booking instead of
  stopping messages. The reminder says so in its own words, and a later `STOP` still opts out.
- **A new minute sweep.** It reads only bookings still `booked` in the next 24 hours.
- **The patient service worker changes.** An existing push without `reply` shows and opens exactly as
  before.
- **Rollback:** revert, then `alembic downgrade 0042`. Reminder times, confirmations and reply tokens are dropped;
  bookings stay, and an already-sent push's buttons then answer 404.

Closes #82
