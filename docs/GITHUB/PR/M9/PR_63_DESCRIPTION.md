# PR: The queue tells a patient through one call, on the transport that can reach them, after the commit (Issue 63 / M9-63)

**Milestone:** [Milestone 9: Notifications & Patient PWA](https://github.com/Billykat7/clinicQ/milestone/9) ·
**Issue:** [#63](https://github.com/Billykat7/clinicQ/issues/63) · **Builds on:** #21 (consent) and #41 (the
ticket lifecycle), both merged · **Unblocks:** #64, #65, #66, #67, #71

The product's promise is "wait at home, not on a bench", and that promise holds only if "you are next"
actually arrives. With this PR:

- **The queue makes one call.** A called patient is told to come in now, and the patient who is now first
  in line is told they are next. Recalls, no-shows, transfers and a desk cancellation go through the same
  call. The queue names the event and nothing else; no queue module can see a transport.
- **A provider cannot hold or undo a call.** The message is recorded in the call's own transaction and
  delivered only after it commits, on a worker thread by default. With the SMS provider down, *Call next*
  answers `200` and the ticket is `called`; the message waits for its retry.
- **Free transports first, SMS last.** The patient's preferred transport comes first when it can reach
  them, then web push, WhatsApp, and finally SMS, which is the only one that costs money. Each transport
  tried is its own ledger row, with what it cost.
- **Every message ends in a terminal status.** That covers a sent message, a dead-lettered one after five
  attempts, a patient who has not consented, and a patient no transport can reach.

**Not done here, and not claimed:**

- **No real provider sends anything yet.** Web push has no VAPID sender (#64), WhatsApp has no provider
  (#76), and SMS is still the logging provider (#65). Web push and WhatsApp report that they cannot reach
  anyone, so today every patient message goes out on SMS.
- **A patient cannot choose a transport yet.** Their preference is stored and honoured, but only #67
  gives them a screen for it.

## Summary

- **One call** (`src/modules/notifications/service.py::notify`): `notify(db, patient_id=, event=, context=,
  site_id=, dedupe_key=)`.
  - It checks consent through the existing send-time gate (`preferences.resolve`, now with `patient_id`),
    plans the transports and writes one `queued` row with a savepoint.
  - It does not deliver. It returns the row, or a `suppressed` row when consent or reach says no.
- **The queue's door** (`src/modules/queue/notices.py`, new): `tell()` and `tell_next_in_line()` build
  the words (number, clinic, queue, room) and a dedupe key, then call `notify`. Four modules use it:
  - `lifecycle.transition_ticket`: `called` to the called patient, and `next` to whoever is first once a
    waiting ticket leaves the line;
  - `timers` and `transfer`: they no longer call `send_sms` directly;
  - `cancellation`: tells the patient only when staff cancelled.
- **After the commit** (`src/modules/notifications/dispatch.py`, new): `after_commit()` publishes
  `PatientNotificationQueued`, and the service's subscriber hands `deliver_committed()` to the dispatcher.
  - `NOTIFICATION_DISPATCH=background` (the default) uses a pool of `NOTIFICATION_DISPATCH_WORKERS` (4)
    threads; `inline` is for tests and scripts.
  - The worker opens its own session on the committed session's engine and takes the row `FOR UPDATE SKIP
    LOCKED`, only while it is still `queued` with no attempts.
- **Transports** (`src/modules/notifications/transports/`, new):
  - `Transport` has two methods, `address_for(patient)` and `send(to, message)`. It raises
    `TransportError` (retry) or `PermanentTransportError` (dead now, fall back).
  - `SmsTransport` wraps the existing `SmsProvider`, so there is still one SMS interface. The new
    `SmsRecipientRejectedError` is permanent, and `cost_of()` defaults to 0.
  - `WebPushTransport` and `WhatsAppTransport` are skeletons that reach nobody until #64 and #76.
  - `NoopTransport` is the test double: it can fail N times, fail permanently, be slow, charge a cost and
    record what it sent.
  - `use_transports()` swaps the whole set for a test.
- **Selection and fallback** (`plan_transports`, `_fall_back`):
  - The preferred transport comes first, then free transports before paid ones (sorted by each
    transport's `free`, not by its name), skipping any transport with no address.
  - A transport with a fallback behind it gets one attempt. The last transport gets the full retry budget.
  - A dead row falls back to the next untried transport as a new row with `fallback_of_id`.
- **The ledger** (`notification`, migration `0031`): `patient_id`, `site_id`, `event`, `dedupe_key`
  (unique), `fallback_of_id`, `cost` (`Numeric(10,4)`), `cost_currency` (`NOTIFICATION_COST_CURRENCY`,
  `ZAR`), and two indexes. All of them are nullable, so the previous release runs on the new schema.
  - New table `patient_notification_preference` (`patient_id`, `preferred_channel`). #67 adds language,
    quiet hours and opt-outs to it.
- **Retries stay on APScheduler** (open decision 1). The existing retry sweep now skips rows another worker
  holds. It leaves a new patient row to its post-commit delivery for `NOTIFICATION_DISPATCH_GRACE_SECONDS`
  (60), then takes over if that delivery never happened.
- **Enums** (`src/commons/enums.py`): `NotificationChannel.WEB_PUSH`/`WHATSAPP`, `PATIENT_TRANSPORT_CHAIN`,
  `NotificationDispatchMode`, `PatientEvent` with `PATIENT_EVENT_TEMPLATE`, the templates `TICKET_NEXT`,
  `TICKET_CALLED` and `TICKET_CANCELLED` (the first two are urgent), and `NOTIFICATION_TERMINAL_STATUSES`.
- **Templates:** the three new messages, and every ticket message registered for SMS, web push and
  WhatsApp. They use the same words for now, so a fallback never changes what the patient reads; #66 writes
  per-channel variants.
- **Admin read model:** `NotificationRead` gains `patient_id`, `site_id`, `event`, `fallback_of_id`, `cost`
  and `cost_currency`.

## Design notes

**Transactional outbox, not "send after commit and hope".** The issue says dispatch is post-commit and
best-effort. Sending after the commit with nothing recorded first would lose the message if the process
died in between. Writing the row *inside* the transaction and delivering *after* it gives both properties:

- a call that commits always has its message on record, and the retry sweep delivers it if the worker
  never runs;
- a call that rolls back leaves no row and sends nothing;
- the row insert is a database write like the audit row beside it, so no provider state can fail it.

**Background by default.** Inline post-commit delivery would still make a receptionist wait for a provider
that takes ten seconds to time out, even though the call was already committed. The request now returns
when the commit does; the measured difference is below. Tests run inline through an autouse fixture, so
"what was sent" is known by the next line. The background mode has its own test.

**Found on the way: a savepoint rollback discarded every pending after-commit event.** A savepoint
rollback fires `after_rollback` and `after_soft_rollback`, and `domain_events._discard` cleared the whole
session's queue on either. The recall sweep moves each ticket in a savepoint and skips one that staff moved
first, so one skipped ticket threw away the board events of every ticket already moved in that sweep (a
bug since Issue 43), and after this PR it would have thrown away their messages too. Each event now
remembers the savepoint it was queued in, and a savepoint rollback discards only what was inside it. The
new test fails on `main`:

```text
E       AssertionError: assert ['inside a kept savepoint'] == ['before the ...pt savepoint']
E         At index 0 diff: 'inside a kept savepoint' != 'before the savepoint'
```

**One gate, extended rather than copied.** Consent (Issue 21) is still asked in exactly one place,
`preferences._patient_consent_denies`. It now accepts `patient_id`, because a push subscription id is not a
phone number. The account-holder preferences are skipped for a patient, who has no account; #67 adds the
patient's own quiet hours and opt-outs to the same function. `test_consent_is_not_bypassable` now counts
`notify` as a send path and `_deliver_via_transport` as the only transport call.

**The ledger is site-scoped now, deliberately.** `site_id` makes `Notification` a site-scoped model under the
Issue 19 guard. The rows remain platform operational data: the retry sweep, the receipt webhook, the
`logs`-gated viewer and the post-commit delivery read across clinics. Each of those functions has a named,
reasoned entry in `_UNSCOPED_BY_DESIGN`. The cross-tenant suite has a `PENDING` entry naming #65's cost
totals as the first clinic-facing reader that must add a case.

**"You are next" is told once per ticket.** It goes to whoever is first in the waiting line whenever a
waiting ticket leaves it (called, cancelled or transferred), with the dedupe key `<ticket>:next`. A
`called` key includes the call's moment, because a call that is undone and made again is a second call.
**Known edge:** after an undone call, the patient who was told "you are next" may briefly not be next.
They are not told otherwise.

**Out of scope, as the issue says:** provider details (#64, #65, #76), template wording and translations
(#66), patient preferences and quiet hours (#67).

## Changes

- **New:**
  - `src/modules/notifications/dispatch.py`
  - `src/modules/notifications/transports/` (`base`, `sms`, `webpush`, `whatsapp`, `noop`, `registry`)
  - `src/modules/queue/notices.py`
  - `src/database/models/patient_notification_preference.py`
  - `alembic/versions/0031_notification_patient_cost.py`
- **Changed:**
  - `service.py`: `notify`, `plan_transports`, `_fall_back`, `deliver_committed`, and transport routing
    and cost in `attempt`
  - `preferences.py` (`patient_id`), `sms.py` (rejected number, cost), `templates.py`, `schemas.py`,
    `notification.py`
  - `enums.py`, `config.py` (4 settings; `.env.example` regenerated), `domain_events.py` (the savepoint
    fix)
  - `lifecycle.py`, `timers.py` (the `sms_provider` argument is gone), `transfer.py`, `cancellation.py`
- **Tests, new:**
  - `tests/integration/notifications/test_patient_notifications.py` (13)
  - `tests/unit/notifications/test_transport_plan.py` (7)
  - `tests/unit/queue/test_queue_knows_no_transport.py` (3)
  - the savepoint case in `tests/unit/sites/test_domain_events.py`
  - `tests/conftest.py` (inline dispatch in tests)
- **Tests, updated for the new call message:** `test_recall_timers.py` (three cases), `test_transfer.py`,
  `test_ticket_states_property.py`, and the guards `test_site_scoped_queries.py`,
  `test_consent_is_not_bypassable.py` and `test_cross_tenant.py`.
- **Docs:**
  - the Issue 63 spec: `arq` replaced with the APScheduler sweep (decision 1), and the existing
    `notification` table instead of a new `notification_log`
  - the M9 status row and progress bars (`--assume-closed 63`), and the README Status block (63 of 109)
  - the module docstring

## Testing

- [x] `ruff check .`, `ruff format --check .` (573 files) and `mypy src/` (284 files) all clean.
- [x] `TZ=UTC pytest tests/ -n auto` against the Docker PostgreSQL 18 and Redis: **2103 passed, 2 failed**.
  Both failures were expected consequences of the new "please come in" message, and both are fixed in this
  PR:
  - the cross-tenant guard wanted a decision for the now site-scoped `notification`;
  - the recall restart test counted two messages where the call is now a third.

  Both files were re-run afterwards: 39 passed.
- [x] Issue 63's own suites, `pytest tests/unit/notifications tests/integration/notifications
  tests/unit/queue/test_queue_knows_no_transport.py tests/unit/sites/test_domain_events.py
  tests/unit/security`: **433 passed**.
- [x] **How to verify, step 1:** `pytest tests/unit/notifications tests/integration/notifications` is green
  (included above).
- [x] **How to verify, steps 2 and 3**, run as a script against the real lifecycle and ledger (a SQLite
  file, dispatch forced as shown):

  ```text
  STEP 2: a transport that fails twice then succeeds (inline dispatch, retry sweep at each due time)
    after the commit (attempt 1):
    ticket_next    sms      status=failed    attempts=1/5 next=02:40:46 cost=None  error=noop sms: provider down
    sweep at the due time, 60 s after the last failure (attempt 2):
    ticket_next    sms      status=failed    attempts=2/5 next=02:42:47 cost=None  error=noop sms: provider down
    sweep at the due time, 120 s after the last failure (attempt 3):
    ticket_next    sms      status=sent      attempts=3/5 next=- cost=0.2500 ZAR error=None
    provider saw 3 attempts, delivered 1: 'BK ClinicQ: ticket A002 at Hillbrow Community Health Centre, you are next. Please be ready at General consultation 1.'

  STEP 3: call a ticket while the SMS provider is down (background dispatch, provider takes 3 s then fails)
    call_next + commit returned in 64 ms
    ticket A001 status in a fresh session: called
    ticket_called  sms      status=failed    attempts=1/5 next=02:40:46 cost=None  error=noop sms: provider down
  ```

  The first run of this script put step 3 first. Its retry sweep in step 2 then also retried step 3's
  failed row with step 2's transport, which is what a sweep should do, so the steps were reordered.
- [x] New tests, all passing:
  - **Sends never block a transition:** `test_call_next_commits_while_the_sms_provider_is_down` (over
    HTTP), `test_a_provider_raising_anything_cannot_undo_the_call`,
    `test_a_call_that_rolls_back_sends_nothing_and_leaves_no_row` and
    `test_a_slow_provider_does_not_hold_the_call_in_background_mode` (a 2 s provider; the call returns in
    under 1 s).
  - **Retries and terminal statuses:** `test_a_send_that_fails_twice_then_succeeds_is_three_attempts_with_backoff`
    (waits 60 s then 120 s), `test_a_send_that_never_succeeds_stops_at_the_maximum_and_is_dead`,
    `test_a_patient_without_consent_is_recorded_suppressed_and_not_sent`,
    `test_a_patient_no_transport_can_reach_is_recorded_not_lost` and
    `test_a_replayed_event_records_and_sends_one_message`.
  - **Selection:** `test_a_free_transport_is_tried_before_sms`,
    `test_the_patients_preferred_transport_comes_before_the_chain`,
    `test_a_dead_free_transport_falls_back_down_the_chain_and_the_ledger_shows_it`, and 7 unit cases of
    `plan_transports`.
  - **End to end:** `test_call_next_tells_the_called_patient_and_the_one_now_first`.
  - **Guard:** `test_only_the_notices_module_imports_notifications`,
    `test_the_notices_module_calls_only_notify` and
    `test_the_guard_fails_on_a_queue_module_that_sends_an_sms_itself`.
- [x] Migration `0031`: `test_alembic_baseline.py` upgrades an empty PostgreSQL to head, round-trips
  downgrade to base and back, finds nothing for autogenerate to change, and checks every constraint name
  against the convention (all in the full run above).
- [ ] Screenshot: no template, stylesheet or script changes.

## Acceptance criteria

- [x] **Every send is recorded in the log with a terminal status:** sent, dead after the budget, suppressed
  for consent, suppressed when unreachable, and each fallback row. See the tests above and the ledger in
  steps 2 and 3.
- [x] **A failed send retries with backoff and stops at a documented maximum:**
  - backoff is `NOTIFICATION_RETRY_BASE_SECONDS * 2**(n-1)` (60 s, then 120 s, as measured above);
  - the maximum is `NOTIFICATION_MAX_ATTEMPTS` (5), after which the row is `dead`;
  - a transport with a fallback behind it gets one attempt, and that rule is documented on
    `_attempt_budget`.
- [x] **Transport selection honours patient preference before the fallback chain:**
  `test_the_patients_preferred_transport_comes_before_the_chain`, plus the unit cases for a preference that
  cannot reach the patient.
- [x] **The queue engine calls one function and knows nothing about transports:** only
  `src/modules/queue/notices.py` imports the notification service, and it calls only `notify`. A source
  guard enforces this and fails on a queue module that sends an SMS itself.
- [x] **Adapters are swappable in tests without touching a real provider:** `use_transports()` and
  `NoopTransport` are used by every new test, and no test needs a credential or a network.
- [x] **Sends never block a queue transition; dispatch is post-commit and best-effort:**
  - *Call next* returns `200` with the provider down, or raising anything;
  - a call that rolls back sends nothing;
  - a slow provider does not hold the call (64 ms above, against a 3 s provider).

## Risk and rollback

- **Behaviour change: patients now get two new messages.** "Please come in now" on every call and "you
  are next" to the first waiting patient, both gated by consent as before. Until #65 caps spend, that is
  more SMS per patient on a real gateway. Today the provider is the logging one, so nothing is spent.
- **Background delivery is new process state.** If the process dies with a delivery in flight, the row
  stays `queued` or `failed`, and the retry sweep (every 5 minutes, after the 60 s grace) delivers it. A
  message can be late in that case; it cannot be lost or sent twice, because both paths take the row
  `FOR UPDATE SKIP LOCKED` and only while it is untried.
- **Migration `0031`** adds only nullable columns, a unique constraint on a new column, two indexes and a
  new table. The previous release runs against it unchanged. The downgrade drops them, losing only the
  patient, cost and dedupe details of rows written since.
- **Rollback** is a revert of this PR and a downgrade to `0030`. The `sms_provider` argument removed from
  `run_recall_timers` had no caller outside tests.

Closes #63
