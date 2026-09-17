# PR: A patient with an address and no phone is told their ticket was called (Issue 220 / M15-220)

**Milestone:** [Milestone 15: Clinic Onboarding & Patient Sign-in](https://github.com/Billykat7/clinicQ/milestone/15) ·
**Issue:** [#220](https://github.com/Billykat7/clinicQ/issues/220) · **Builds on:** #63 (the notification
service and its transports), #219 (`patient.email`), #67 (preferences and quiet hours)

[Issue 219](https://github.com/Billykat7/clinicQ/issues/219) let a patient sign in with an email address
and gave them a record with **no phone number**. The fallback chain was `WEB_PUSH → WHATSAPP → SMS`, so
such a patient matched nothing in it: they joined a queue, held a ticket, and were never told it was
called. `NotificationChannel.EMAIL`'s docstring still said *"for account holders only: a patient has no
email address on record."*

With this PR:

- **`EmailTransport`** carries a patient notification, free, and is second in the chain — after web push,
  ahead of WhatsApp. Both are free, so the order between them is about which is likelier to arrive: we
  hold an address for every patient who signed in with one, and only sometimes a WhatsApp id. **SMS
  stays last**, because it is the one that costs money.
- **14 email templates**, one per patient event, each with a real subject line. Not an SMS body in an
  envelope: an email has no segment to fit, so they say the same things with room to be plain about
  them — and still say nothing a corridor screen may not.
- **A patient may prefer email**, and a dependant's email goes to their proxy, exactly as their number
  does.

**Found and fixed while doing it — the part worth reviewing closely:**

`attempt()` special-cased `channel is EMAIL` and went **straight to SMTP**, bypassing the transport
registry entirely. That was written when the only email was an account holder's, and it had two
consequences once a patient could have an address:

1. **A patient's email could not be swapped in a test**, so a test that exercised it **sent real mail**.
   One did, through the `SMTP_HOST` in a local `.env`, while this issue was being written.
2. A patient's email would have carried the **staff** unsubscribe header and been classified by the
   account-holder path's error handling rather than its own.

The branch is now `channel is EMAIL and notification.patient_id is None` — the account holder keeps the
direct path (its own ledger row, its own preferences, its unsubscribe header); a patient's email is a
patient transport like every other. And `tests/conftest.py` now makes `deliver_smtp` **raise** in every
test, so this cannot happen again quietly.

**Not done here, and not claimed:**

- **No cap or cost accounting for email.** `sms_budget` exists because SMS costs money per message;
  email is free and uncapped here.
- **No HTML body.** The templates are plain text, as every other patient channel is.
- **No delivery-status webhook.** The Message-ID is recorded, so a bounce webhook could correlate to it
  later; nothing consumes one yet.
- **English only.** The other four languages have no locale file yet (Issue 77, M10); an email falls
  back to English exactly as the other channels do.

## Summary

- **`src/modules/notifications/transports/email.py`**: `EmailTransport` (`free=True`), addressing
  `PatientAddresses.email`, `configured` exactly when `SMTP_HOST` is set — so a deployment with no SMTP
  server offers no address and the chain passes over it without recording a failure, the way web push
  does without VAPID keys. `_is_permanent` maps RFC 5321 5.1.x replies and the words common servers put
  beside them to `PermanentTransportError`; a 4.x.x, a timeout or a refused connection stays a
  retryable `TransportError`.
- **`PatientAddresses.email`**, filled by `_addresses` from `reachable_patient`, so a dependant's email
  goes to their proxy's address for the same reason and through the same seam as their number.
- **`PATIENT_TRANSPORT_CHAIN`** becomes `WEB_PUSH → EMAIL → WHATSAPP → SMS`, and
  `NotificationChannel.EMAIL`'s docstring no longer claims a patient has no address.
- **`template_registry`**: `EMAIL` joins `PATIENT_CHANNELS`, and `TITLED_CHANNELS` replaces the
  hardcoded "only a web push has a title" rule — an email needs a subject and an SMS still must not
  have one, each with its own sentence when it is wrong.
- **`src/locales/en/notifications.toml`**: the 14 email templates, and the regenerated
  `notifications.lock.json`.
- **`patient_preferences`**: `EMAIL` joins `PREFERABLE_CHANNELS`.
- **`service.attempt()`**: the account-holder/patient split described above.
- **`tests/conftest.py`**: an autouse fixture that turns any SMTP send in a test into a loud failure.

## How it was checked

- [x] **The reason the issue exists, asserted directly**
      (`test_a_patient_with_an_address_and_no_number_is_told_their_ticket_was_called`): a patient with
      no push subscription, no WhatsApp id and no number gets one `sent` row on `email`, at zero cost,
      carrying the ticket number. Beside it,
      `test_without_the_email_adapter_a_patient_with_only_an_address_reaches_nothing` keeps the old
      behaviour on record as the reason: **the plan is empty**.
- [x] **Order** (`test_email_comes_after_push_and_before_the_other_free_transport`):
      `[WEB_PUSH, EMAIL, WHATSAPP, SMS]`, and a patient who holds both an address and a number is
      reached free — `test_a_patient_with_both_is_reached_free_and_never_costs_an_sms`.
- [x] **Not configured** (`test_email_is_left_out_when_the_deployment_cannot_send_mail`): with no
      `SMTP_HOST` the chain skips it rather than failing it, so the message still reaches SMS.
- [x] **A refused address** (`test_an_address_the_server_refuses_falls_back_rather_than_retrying_it`):
      one attempt, not retried, and SMS carries it.
- [x] **The gate still runs first**
      (`test_an_email_only_patients_message_still_waits_out_their_quiet_hours`): a non-urgent message in
      quiet hours stays `queued` with a future `next_attempt_at` and nothing is sent. Consent and
      opt-out are unchanged, and already covered.
- [x] **A dependant** (`test_a_dependants_email_goes_to_the_person_who_acts_for_them`): asserted at the
      `reachable_patient` seam — the double reports its configured address whoever the patient is, so
      only the real redirection can be tested — plus the row's own recipient.
- [x] **Every patient template renders on every patient channel**: the existing guard now reads
      `registry.PATIENT_CHANNELS` instead of a literal list of three, so it covers email and cannot rot
      the same way again. 56 versions register (14 templates × 4 channels).
- [x] **One behaviour deliberately changed**, and its test updated rather than deleted:
      `preferred_channel: "email"` used to be a 422 because a patient had no address. It is a valid
      choice now; `"letter"` stands in as the channel that still is not one.
- [x] `TZ=UTC pytest tests/unit tests/integration` green with `TEST_DATABASE_URL` set; `ruff check`,
      `ruff format --check` and `mypy src` (334 files) clean.

## Acceptance criteria

- [x] **A patient with an address and no number receives a queue notification by email**, and the ledger
  records `channel=email` with its provider id.
- [x] **A patient with both is reached push → email → SMS**, so a reachable patient never costs an SMS.
- [x] **A dependant's email goes to their proxy's address**, never to the dependant.
- [x] **A switched-off category is not emailed, and quiet hours hold an email** as they hold an SMS.
- [x] **A permanently rejected address ends that attempt at once and the next transport is tried**; a
  refused connection retries with backoff.
- [x] **`tests/contract` still passes: no existing phone-patient delivery changes its channel** — the
  full suite is green, and email is only ever chosen when the patient has an address.

## Risk and rollback

- **This one is not behind a flag**, and it changes the chain for **every** patient who has an address.
  Today that is only patients created by [#219](https://github.com/Billykat7/clinicQ/issues/219), whose
  flag is off, so in practice no existing patient is affected — but if `patient.email` is ever
  backfilled, those patients start receiving email ahead of SMS. That is the intended saving; it is
  worth knowing it is automatic.
- **Email delivery is synchronous inside the attempt**, like the account-holder path it replaces for
  patients. A slow SMTP server slows that attempt, not the queue transition: sends already happen after
  the commit.
- **Rollback:** revert. No migration, no data change. The `sites`/`patient` tables are untouched, and
  the locale file's email blocks are inert once `EMAIL` leaves `PATIENT_CHANNELS`.

Closes #220
