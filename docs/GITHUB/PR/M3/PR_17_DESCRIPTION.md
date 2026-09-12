# PR: A patient is a phone number and a 6-digit code: patient identity with no password, and no code in any log (Issue 17 / M3-17)

**Milestone:** [Milestone 3: Identity, Auth, RBAC & Consent](https://github.com/Billykat7/clinicQ/milestone/3) ·
**Issue:** [#17](https://github.com/Billykat7/clinicQ/issues/17) · **Builds on:** #15 (PR #129), #16 (PR #130)

> **Merge order:** after #129 and #130. Until they are merged this branch also carries their
> `Issue 15:` and `Issue 16:` commits, so the Conventions check fails on those; re-run it after.

ClinicQ had no patient model. This PR adds one, in the shape of `src/modules/widgets/`: a patient
is a verified mobile number, created the first time a 6-digit SMS code is verified, found again
however the number is written, and never asked for a password on any channel. Building it on the
kernel's pieces, as the issue asks, turned up three places where an OTP was not as safe as it
looked, and each is fixed for staff and patients alike:

- **The email OTP took unlimited guesses.** The shared store had no attempt limit, so a staff
  sign-in code could be guessed in parallel for its whole 10 minutes. From a clean `main` worktree:
  `20 wrong guesses -> [400]`, `then the right code -> 200`. On this branch the code locks after 5
  and the right one is then refused (`-> 400`).
- **Codes were written to the log.** The logging SMS provider logged every message's text and
  number, and the no-SMTP email fallback logged the code itself.
- **Codes were stored in the database.** The notification ledger kept every SMS's context, code
  included, and every email's rendered body.

## Summary

- **`src/modules/patients/`:** `Patient` model (migration `0003`: unique E.164 `phone_e164`, optional
  `whatsapp_id` and `display_name`, **no password column**), schemas, service, session helpers,
  router and RBAC manifest (`patients`, `patients.self`; grants arrive with the other roles in #18).
- **Endpoints:** `POST /api/v1/patients/otp/request` (202, the same answer for every number, with
  a plain-language notice of why the number is collected), `POST /otp/verify` (the patient, and a
  session), `GET`/`PATCH /patients/me`, `POST /patients/logout`.
- **One normaliser:** `src/commons/phone.normalize_phone`. `0821234567`, `27821234567`,
  `+27821234567`, `+27 (0)82 123 4567` and `0027 82 123 4567` are one number; a number with no
  country, or the wrong length, is a 422 that never quotes it back.
- **One OTP store, extended, not copied** (`src/core/otp_store.py`): codes keyed by
  `(kind, identifier)` for email and phone, stored only as an HMAC, single-use, expiring after
  `OTP_TTL_MINUTES`, **locked after `OTP_MAX_VERIFY_ATTEMPTS` (5) wrong guesses**, with a resend
  cooldown (`OTP_RESEND_COOLDOWN_SECONDS`, 60) and a per-number budget (`OTP_RATE_LIMIT_PER_PHONE`).
- **The code goes through the `SmsProvider`** (`notifications.send_sms`), so it honours the
  notification rules; the logging provider lets it work before any SMS account exists.
- **No code in a log or a row:** the logging provider logs that an SMS was accepted and its length,
  nothing else; development reads codes at `GET /dev/outbox` (a route that exists only in
  development); the ledger stores secret-bearing templates with a placeholder and never retries
  them.
- **Patient sessions** are their own token type (`patient`), in an httpOnly `SameSite=Lax` cookie
  (`Secure` outside development), CSRF-bound like the staff session; signing out bumps a
  `session_version` so every earlier token is refused. A staff token opens no patient route and a
  patient token no staff one.
- **USSD and WhatsApp:** `patient_for_gateway()` resolves the patient from the gateway's number
  **with no OTP**, a deliberate decision written down in the package docstring and as decision 9 in
  `docs/GITHUB/ISSUES/README.md`.

## Design notes

**Decision 9: the USSD path trusts the gateway MSISDN.** The mobile network authenticated that
number when it connected the session; a USSD session cannot be started from someone else's SIM, and
Meta verified a WhatsApp number when it registered. An SMS code on top adds a second channel, a cost
per session and a failure point to prove what the network proved, and shuts out feature phones on
weak coverage, the people USSD exists for. The trust holds only under three conditions M10 must
keep: the gateway's webhook is authenticated (a secret or signature, and an IP allow-list); the trust
never becomes a web session (only a code starts one, and `patient_for_gateway` issues none); and the
accepted risk is the one an SMS code accepts anyway, a lost or stolen phone.

**No patient until the code is verified.** Asking for a code creates nothing, so an unverified
number is never somebody's record (data minimisation), and the request's answer is the same for a
new number and a known one.

**One number, one patient, even in a race.** `phone_e164` is unique; `get_or_create_patient` inserts
inside a savepoint, and the loser of a race reads the winner's row, so neither request fails.

**Patient sessions have no refresh token.** A patient's visit is a clinic day, so the session is one
12-hour token (`PATIENT_SESSION_HOURS`), with server-side sign-out through `session_version`. A
counter rather than a sign-out timestamp: the first draft compared a timestamp with the token's
`iat`, and the test for "a kept cookie is refused after sign-out" caught a token issued in the same
second slipping through.

**Consent wording is F's.** The one sentence patients see here (why the number is collected, in
`PHONE_NOTICE`) is a draft for F (Data & Research) to review; consent itself is Issue 21.

**The OTP store is still process-local**, as the kernel's was: the image runs one worker, codes last
minutes, and a restart means requesting a new code. More than one worker needs it moved to Redis
first; this is recorded as a known issue for the v0.3.0 note.

**Out of scope:** consent (Issue 21), dependants (Issue 84), the USSD and WhatsApp menus (73, 75),
the patient web pages (M9). "Joins a queue" in the first criterion waits for queues (M6): what this
PR shows is the identity that join will use.

## Changes

- **New:** `src/commons/phone.py`, `src/database/models/patient.py`,
  `alembic/versions/0003_patients.py`, `src/modules/patients/` (`__init__`, `schemas`, `service`,
  `sessions`, `router`, `rbac_manifest`), `src/modules/notifications/dev_outbox.py`.
- **`src/core/otp_store.py`:** rewritten as one store for every subject kind; the email sign-in's
  function names kept over it.
- **`src/modules/notifications/`:** `service.py` withholds secret fields in the ledger and delivers
  them once from memory; `sms.py`'s logging provider no longer logs text or number.
- **`src/core/email_send.py`:** the no-SMTP OTP fallback goes to the dev outbox, not the log.
- **`src/core/security.py`:** the `patient` session token type; `src/core/csrf_middleware.py`
  treats the patient cookie as a session cookie.
- **`src/commons/enums.py`:** `PatientChannel`, `GATEWAY_TRUSTED_CHANNELS`, `OtpSubjectKind`,
  `OtpVerification`, `NOTIFICATION_SECRET_FIELDS`, `TokenType.PATIENT_SESSION`,
  `AuditEntityType.PATIENT`, `BoundedContext.PATIENTS`.
- **`src/core/config.py`, `.env.example`:** `OTP_RATE_LIMIT_PER_PHONE`, `OTP_MAX_VERIFY_ATTEMPTS`,
  `OTP_RESEND_COOLDOWN_SECONDS`, `PATIENT_SESSION_HOURS`, `PATIENT_SESSION_COOKIE_NAME`.
- **`src/web/dev.py`:** `GET /dev/outbox`.
- **Tests:** `tests/unit/commons/test_phone.py` (25), `tests/integration/patients/test_patient_otp.py`
  (17); `PatientFactory` now builds and persists the real model (Issue 8's plan).
- **`tests/integration/database/test_credentials_at_rest.py`** (from #15): its log capture now runs
  ahead of the app's redaction filter (see *Testing*).
- **Docs:** decision 9 in `docs/GITHUB/ISSUES/README.md`; the spec's file list.

## Testing

- [x] `ruff check`, `ruff format --check` and `mypy src/` clean (170 files).
- [x] `make test`: **1105 passed**, 17 skipped, 9 xfailed. PostgreSQL and Redis: **17 passed**,
      including the migration round trip and "autogenerate finds nothing to change" with `0003`.
- [x] **The log test bites, and finding that it did not at first mattered.** Replacing the logging
      provider with `main`'s version (log the text) must fail the "never in a log" test. The first
      run *passed*: the app's `RedactionFilter` rewrites each record in place, and the test's
      capture ran after the app's handlers, so it saw `[REDACTED:otp]`. The capture now runs first
      and snapshots each record as it arrives, and the mutant fails:
      `the code reached a log record: src.modules.notifications.sms: SMS (logging provider) to
      +27821234567 … your sign-in code is 955936.` The same weakness was in #15's credentials test;
      fixed here too, and it still passes on PostgreSQL.
- [x] **How to verify, on a real server** (`uvicorn`, PostgreSQL migrated to `0003`, development,
      the logging SMS provider, `OTP_RESEND_COOLDOWN_SECONDS=0` so the demo need not wait a minute):

```text
1. request as 0821234567          -> 202 {"detail":"A 6-digit code is on its way by SMS.",
                                          "expires_in_seconds":600, ...}   (code read from /dev/outbox)
2. verify as +27821234567         -> 200 {"id":"01a09268-e40f-…","phone":"+27 ** *** 4567", ...}
3. GET /patients/me (cookie)      -> 200 (the same id)
4. request and verify as 27821234567, another device -> 200 (the same id)
   id                                   | phone_e164   | last_channel | verified
   01a09268-e40f-7057-9ae3-2576bb4ee746 | +27821234567 | web          | t          (1 row)
5. wrong code five times:  attempts_left 4, 3, 2, 1, then "Too many wrong codes. Ask for a new one."
   the right code:         {"code":"patients.otp.locked","attempts_left":0}
6. the server log:  occurrences of the code: 0;  of the number: 0
   "SMS accepted by the logging provider [log-34287f15-…], 83 characters (see /dev/outbox)"
7. the ledger row:  otp_sign_in | sent | max_attempts 1 | {"code": "[withheld: one-time code]",
                                                          "ttl_minutes": 10}
$ \d clinicq.patient  → id, phone_e164 (UNIQUE), whatsapp_id (UNIQUE), display_name,
                        phone_verified_at, last_channel, last_seen_at, session_version, timestamps,
                        is_deleted; no password, no pin, no hash.
```

- [ ] Not shown: a screenshot. No template, stylesheet or script changes; the patient pages are M9.

## Acceptance criteria

- [x] A patient signs in with only a phone number and a 6-digit OTP; no password exists: no column,
      and the verify endpoint refuses a `password` field (422). *Joining a queue* waits for queues
      (M6); this is the identity it will use.
- [x] OTPs expire within the configured window and are single-use (tests with a controlled clock:
      one second past the TTL is `expired`; a second use is refused).
- [x] More than N verification attempts locks the code and requires a fresh request (5; shown above
      and tested, including that a new code then works). Also true for the staff email code now.
- [x] `+27821234567`, `0821234567` and `27821234567` resolve to the same patient record: one row,
      shown above; unit-tested with nine spellings.
- [x] OTP values never appear in logs, proven by a test that captures every record unredacted and
      is shown to fail on `main`'s provider; they are not in the database either.
- [x] A USSD session is trusted via the gateway MSISDN without a second OTP challenge, and this is
      documented as a deliberate decision (decision 9; the package docstring), with a test that the
      MSISDN and web forms are one patient, no SMS is sent, and no web session is issued.

## Risk and rollback

**Migration `0003`** only adds the `patient` table; the previous release ignores it. Reversible.
Behaviour a developer will notice: the logging SMS provider and the no-SMTP email fallback no
longer print message text or codes; read them at `GET /dev/outbox`. Staff email OTP codes now lock
after 5 wrong guesses. Codes sent while SMTP or SMS is unavailable are not retried later (they would
arrive expired). Rollback is a revert; drop the table if needed.

**Follow-ups noticed:** the OTP store must move to Redis before the app runs more than one worker;
SMS provider exceptions from a real gateway must not quote the number (the fake's does, in tests
only); API responses show `phone_verified_at` in UTC, as every kernel timestamp does, not in
Africa/Johannesburg (worth a project-wide decision in M9). **A note on this branch's own testing:**
before the email-OTP test pinned the email settings, one local run used the developer's `.env`,
which names a real SMTP relay, and may have attempted to send a code to `nurse@clinicq.example`, a
reserved domain that cannot receive mail. The test now pins them.

Closes #17
