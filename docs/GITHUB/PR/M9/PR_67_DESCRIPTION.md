# PR: Patients choose how and when they hear from ClinicQ, and STOP stops every channel at once (Issue 67 / M9-67)

**Milestone:** [Milestone 9: Notifications & Patient PWA](https://github.com/Billykat7/clinicQ/milestone/9) ·
**Issue:** [#67](https://github.com/Billykat7/clinicQ/issues/67) · **Builds on:** #21 (consent), #63, #68, #64,
#65 and #66 (PRs #186 to #190), all merged · **Unblocks:** #71

Consent says whether ClinicQ may message a patient at all. This PR adds how and when, and makes "stop" mean
stop:

- **A patient's own preferences are checked before every send and every retry**, in the same gate that
  already checks consent (`preferences.resolve`), not in a second one: a global opt-out, per-message mutes,
  a preferred channel, a language and a quiet-hours window.
- **Replying STOP to any SMS stops every channel immediately**: SMS, web push and WhatsApp, including a
  message already queued or waiting for quiet hours to end, because the gate is asked again when it is due.
  START undoes it.
- **Quiet hours hold every message except three**: "you are next", "please come in now" and "you were called
  again". Each is about a visit happening at that moment and is worthless in the morning. A cancellation,
  a transfer or a missed ticket waits. An opt-out stops all six.
- **Preferences are changed without an account**, by the ticket page's unguessable link, from a new
  **Message settings** panel on that page.
- **A guard test fails if a transport can dispatch without the gate.**

**Decisions made here, for the reviewer to confirm or change:**

- **Anyone holding the ticket link can change how its patient is told.** A patient has no account, and the link
  is what was sent to them; a family member following along can already see the ticket. The link reaches the
  preferences and nothing else: no other ticket, no other patient, no consent. The page's share note now says
  so ("Anyone with it can follow this ticket and change how you are told, but not cancel it").
- **STOP does not text back a confirmation.** A confirmation is a paid SMS to someone who just asked for no
  more. The ticket page shows the opt-out. Whether a regulator or the gateway requires a confirmation is not
  checked here, and should be before a live account is used.
- **The exception is a set of three templates in one constant** (`PATIENT_QUIET_HOURS_EXEMPT`), documented
  where it is defined and in `docs/OPS/SMS_GATEWAY.md`, and a parametrized test runs all six events against it.
- **Preferences belong to the patient, not the clinic.** Stopping at one clinic stops messages from every clinic.

**Not done here, and not claimed:**

- **USSD and WhatsApp menus for preferences** are listed in the scope, but both channels arrive in M10
  (#72 to #79). `patient_preferences.update` is the function they call, and `PreferenceSource` already names
  them; the menus themselves are not built.
- **No live Africa's Talking reply has been received.** The inbound callback is tested with a form post in the
  shape Africa's Talking documents, not from a real handset.

## Summary

- **The gate** (`src/modules/notifications/preferences.py`):
  - `resolve` asks consent first, then `_resolve_patient` for a patient message: opted out →
    `SUPPRESS "opted-out"`; muted event → `SUPPRESS "muted-<event>"`; inside quiet hours and not exempt →
    `DEFER "quiet-hours"` until the window ends; otherwise `SEND`.
  - `quiet_until(start, end, tz, now)` is extracted from the account-holder code, so both use one
    implementation, including windows that wrap past midnight.
  - The service already calls `resolve` in `attempt` before any transport, for first sends and for every retry
    and fallback. That is why a STOP reaches a message queued before it.
- **Changing preferences** (`src/modules/notifications/patient_preferences.py`, new):
  - `read` and `update(db, patient_id, change, source)`: only the fields sent move; a channel a patient
    cannot prefer, a language messages are not written in, or half a quiet-hours window is a
    `PreferenceError`;
  - `apply_reply(db, phone, text)`: the first word, upper-cased, decides. `STOP`, `STOPALL`, `UNSUBSCRIBE`,
    `END`, `QUIT`, `CANCEL`, `OPTOUT` stop; `START`, `UNSTOP`, `SUBSCRIBE`, `OPTIN` restart; anything else,
    or a number no patient has, is ignored.
- **The routes:**
  - `GET` and `PUT /api/v1/notifications/patient-preferences/{page_token}`: `404` for an unknown link or a
    walk-in with no patient, `422` with the reason for a refused change;
  - `POST /api/v1/webhooks/sms/africastalking/{token}/inbound` (`smsInboundMessage`): the same URL secret as
    #65's receipts, checked before the body is read, each reply recorded once (`duplicate` on a repeat);
  - `TicketPageOut.preferences_url` tells the page where its patient's settings are, `null` for a walk-in.
- **The data** (migration `0036`):
  - `patient_notification_preference` gains `quiet_hours_start`, `quiet_hours_end`, `muted_events` (JSON,
    default `[]`), `opted_out_at` and `opted_out_via` (where the settings last changed);
  - `sms_inbound_event` records each reply's id, keyword (only when it was one), outcome and patient, never
    the phone number or the text.
- **The ticket page:** a collapsed **Message settings** panel (`ticket.html`, `ticket-preferences.js`, external
  under the CSP, `ticket.css`): stop all messages, preferred channel, language, quiet hours, and which messages
  not to receive. It loads when opened and saves in one `PUT`.
- **The guard** (`tests/unit/security/test_transport_gate.py`, new), read from the source:
  1. a class deriving from `Transport` outside `notifications/transports/` is a finding;
  2. a `transport.send` or `provider.send` / `send_message` call is allowed only in the service's
     `_deliver_via_transport` and in the SMS transport handing its message to its gateway; the one other
     `provider.send` in the tree, the e-signature provider's, is named with its reason;
  3. `_deliver_via_transport` is called only from `attempt`;
  4. inside `attempt`, `resolve` is called before `_deliver_via_transport`, and a `SUPPRESS` decision returns
     before it.

  A fixture proves rules 1 and 2 catch a `PagerTransport` in `src/modules/queue/pager.py` that sends
  straight to a pager.

## Design notes

**Why the gate is asked at delivery, not only when the message is queued.** A patient who replies STOP while
a cancellation is waiting for their quiet hours to end expects that cancellation not to arrive. Deciding once,
at queue time, would send it at 07:00. Because `attempt` asks `resolve` every time a row is due, the opt-out
applies to what is already waiting, and the test shows it.

**Why "urgent" is a list, not a flag.** A flag invites each new message to call itself urgent. Three named
templates, one constant and a test that runs every event keep the exception as narrow as the issue asks: adding
a fourth means changing the constant and its test together, in review.

**Why STOP lives in a keyword set and a webhook.** The keyword is checked on the gateway's inbound callback,
so it works whatever the patient replied to. Opt-out is a column on the patient's preference row, which every
channel's send reads, so there is nothing per channel to forget.

**Why the guard reads source.** A runtime test proves the transports we have pass the gate. It cannot prove
that a transport added next month does. Reading the tree for every class deriving from `Transport` and every
`send` call does, and its fixture shows it fails on the shape it exists to catch.

## Changes

- **New:**
  - `src/modules/notifications/patient_preferences.py`
  - `alembic/versions/0036_patient_preferences_quiet_hours.py`
  - `src/static/js/ticket-preferences.js`
- **Changed:**
  - `src/modules/notifications/preferences.py` (`_resolve_patient`, `quiet_until`), `router.py`,
    `schemas.py` (`PatientPreferencesIn`, `PatientPreferencesOut`)
  - `src/core/webhook_gateways/africastalking.py` (`parse_reply`, `process_reply`),
    `src/api/v1/routes/webhooks.py`
  - `src/database/models/patient_notification_preference.py`, `sms_budget.py` (`SmsInboundEvent`),
    `models/__init__.py`
  - `src/commons/enums.py` (`PATIENT_QUIET_HOURS_EXEMPT`, `PreferenceSource`, `SMS_STOP_KEYWORDS`,
    `SMS_START_KEYWORDS`)
  - `src/modules/queue/schemas.py`, `ticket_page.py` (`preferences_url`), `contracts/queue.yaml`
  - `src/templates/queue/ticket.html`, `src/static/css/ticket.css`, `src/static/js/ticket.js` (the share note)
- **Tests, new:**
  - `tests/unit/security/test_transport_gate.py` (4)
  - `tests/integration/notifications/test_quiet_hours.py` (14)
  - `tests/e2e/patient/test_message_settings.py` (3)
- **Tests, updated:** `tests/unit/security/test_api_route_gates.py` (the two public preference routes and the
  inbound webhook, each with its reason), `tests/e2e/patient/conftest.py` (each day empties the preferences).
- **Docs:** `docs/OPS/SMS_GATEWAY.md` (section 6, replies), the Issue 67 spec (files), the M9 status row and
  progress bars (`--assume-closed 67`), and the README Status block (68 of 109).

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (298 files) clean.
- [x] `TZ=UTC pytest tests/ -n auto` on the Docker PostgreSQL 18 and Redis, with browsers: **2218 passed,
  1 skipped, 9 xfailed, 0 failed** (before the last one-line change below, which was then re-run with its
  tests).
- [x] After that run, re-saving "stop" was changed to keep the moment the patient first stopped instead of
  resetting it; `test_quiet_hours.py` and `test_message_settings.py` were re-run: **17 passed**.
- [x] Migrations: `alembic` upgrade to `0036`, downgrade to `0035` and upgrade again, in the Alembic tests
  (6 passed).
- [x] **How to verify, step 1 (reply STOP):** `test_replying_stop_blocks_the_next_message_on_every_channel_at_once`.
  A patient with quiet hours around now has a cancellation `queued`; the inbound callback gets `stop please`
  (`stopped`, and `duplicate` on the same id again); with web push, WhatsApp and SMS each preferred in turn the
  next message is `suppressed by preference (opted-out)`; the queued cancellation is `suppressed` when the sweep
  reaches it; no transport received anything; `START` (`restarted`) lets the next message go.
- [x] **How to verify, step 2 (quiet hours):** `test_quiet_hours_hold_a_non_urgent_message_and_let_you_are_next_through`:
  a cancellation is `queued` to the end of the window while "you are next" is `sent`, and the cancellation is
  sent once the window ends. `test_the_urgent_exception_is_exactly_the_three_come_now_messages` runs all six
  events: next, called and recalled send; no-show, transferred and cancelled wait.
- [x] **How to verify, step 3 (a transport that skips the gate):** I added `src/modules/queue/pager.py` with a
  `PagerTransport` whose `page_patient` calls `transport.send` directly, ran the guard, and removed the file:

  ```text
  $ pytest tests/unit/security/test_transport_gate.py
  E  AssertionError: a transport outside src/modules/notifications/transports: ['pager.py:4 class PagerTransport']
  E    src/modules/queue/pager.py:11 page_patient() calls transport.send
  2 failed, 2 passed

  $ rm src/modules/queue/pager.py && pytest tests/unit/security/test_transport_gate.py
  4 passed
  ```

- [x] Integration (`test_quiet_hours.py`, 14 passed), beyond the steps: a reply with no keyword or from an
  unknown number changes nothing, and a forged callback secret gets `404`; an opt-out stops the urgent messages
  too; preferences read and changed by the link, with `422` for an email channel, a language messages are not written in
  (`tsn`), half a window, and an extra field such as `patient_id`; a walk-in's link gets `404`; the opt-out holds for the same patient's ticket at clinic B; quiet hours
  wrapping past midnight.
- [x] Browser (`tests/e2e/patient/test_message_settings.py`, 3 passed, Chromium on the real server and a
  migrated database): a phone that was only sent the link sets 21:00 to 07:00, mutes "I move to another queue"
  and prefers SMS, and the database has exactly that; the patient's own phone (signed in, so the save carries
  the CSRF token) is refused half a window on the page with nothing sent, then stops all messages and the row
  records `opted_out_via = ticket_page`; a walk-in's page has no panel; at 320 px nothing in the panel is wider
  than the screen.

| Settings from a shared link | After stopping all messages |
|---|---|
| ![The message settings panel with quiet hours 21:00 to 07:00, SMS preferred and one message muted](https://github.com/Billykat7/clinicQ/blob/12c9bd660a4603853d442a56e3be373d914099de/docs/GITHUB/PR/M9/assets/pr67/message-settings.png?raw=true) | ![The panel after saving stop all messages, saying no more messages will be sent](https://github.com/Billykat7/clinicQ/blob/12c9bd660a4603853d442a56e3be373d914099de/docs/GITHUB/PR/M9/assets/pr67/message-settings-stopped.png?raw=true) |

## Acceptance criteria

- [x] **A send outside the allowed window or after an opt-out is blocked, proven by a test:** inside quiet
  hours a cancellation is `queued` until the window ends and then sent; after an opt-out every event, the
  urgent ones included, is `suppressed` (`test_quiet_hours_hold_…`, `test_an_opt_out_stops_even_the_urgent_messages`).
- [x] **Opting out via an SMS reply keyword takes effect immediately across all channels:** after `stop please`
  the next message with web push, WhatsApp and SMS each preferred is `suppressed by preference (opted-out)`, a
  message queued before the reply is suppressed when due, nothing reaches any transport, and `START` lets the
  next one through (step 1).
- [x] **The urgent exception is narrowly defined, documented and tested:** three templates in
  `PATIENT_QUIET_HOURS_EXEMPT`, documented there and in `docs/OPS/SMS_GATEWAY.md`, and a test over all six
  events (step 2).
- [x] **Preferences are editable without an account, using the ticket reference:** by the ticket page's
  link, over the API and in the browser from a phone that was only sent the link; a walk-in with no patient
  gets `404`.
- [x] **No transport can dispatch without passing the gate, enforced by a guard test:**
  `test_transport_gate.py`, with the fixture that fails on a transport skipping the gate (step 3).
- [x] **Opt-out state survives the patient joining a queue at a different clinic:** a patient who stopped at
  clinic A joins at clinic B and that clinic's message is suppressed.

## Risk and rollback

- **No patient sees a change on merge.** Nobody has preferences yet, so every patient message is decided as
  before. The panel is collapsed on the ticket page.
- **A STOP reply now has an effect.** Once the inbound URL is set in the gateway dashboard, a patient's STOP
  suppresses their messages everywhere, which is the point; `START`, or unticking the box on the ticket page,
  undoes it.
- **Anyone with a ticket link can mute that patient's messages**, as described above. The worst case is a
  patient who is not told they are next by someone they shared the link with, and the ticket page still shows it.
- **Migration `0036`** adds five nullable or defaulted columns and one table. The previous release ignores them.
- **Rollback** is a revert and a downgrade to `0035`. Preferences and recorded replies are lost, and every
  patient message is decided by consent alone, as before.

Closes #67
