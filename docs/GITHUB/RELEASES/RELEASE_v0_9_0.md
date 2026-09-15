# Release v0.9.0: Notifications & Patient PWA

**Date:** 2026-09-15 · **Milestone:** M9 · **Issues closed:** 63–71

A pre-release. v0.8.0 put the call on the waiting-room TV, but a patient still had to be in the room to see
it. This release **reaches the patient wherever they are waiting**:

- the queue tells them "you are next" and "please come in now" by web push, WhatsApp or SMS, cheapest first;
- they follow their place in line on a live ticket page from a link, with no account;
- they choose how and when they are told, and STOP means stop on every channel;
- the ticket page installs as an app that still shows the last known place in line with no signal;
- every ticket carries a QR and a short code that reception scans or types.

The channels that let a patient without a smartphone join and hear back (USSD and WhatsApp menus, M10) are
still to come.

Four rules shape the release, and each is enforced where a bug cannot get round it:

- **A message can never hold or undo a queue move.** The ledger row is written in the move's own
  transaction and delivered only after it commits. The queue calls one function and imports no transport,
  which a source guard checks.
- **Nothing reaches a patient past the gate.** Consent, then the patient's preferences, quiet hours and
  opt-out, are checked before every send and every retry. A guard fails if a transport can be called any
  other way.
- **Nothing is lost silently.** Every failure path, including errors nobody planned for, ends in a terminal
  ledger status. The operators see delivery and cost per transport, and the team is alerted when a
  transport's failure rate crosses its threshold.
- **SMS cannot overspend.** Sandbox by default, per-clinic and per-patient daily caps that alert, a kill
  switch with no deploy, and every message fitted to one segment.

All nine pull requests merged on 15 September 2026, each after its checks were green and before the next
branched from `main`: #186 (63) → #187 (68) → #188 (64) → #189 (65) → #190 (66) → #191 (67) → #192 (69) →
#193 (70) → #194 (71). The tag is cut from `main` after #194 merges.

## What shipped

- **The notification service, transports and delivery ledger** (Issue 63, PR #186; migration `0031`).
  - `service.notify()` is the queue's only call (`src/modules/queue/notices.py`). It records a ledger row
    with the patient, clinic, event, a dedupe key and the cost, in the move's transaction.
  - **Delivery after the commit:** on a worker pool (`NOTIFICATION_DISPATCH=background`), with the
    retry sweep as the safety net. A test proves *Call next* commits while the SMS provider is down.
  - **Transports:** web push, WhatsApp and SMS behind one interface. The patient's preferred transport
    comes first, then free ones, then SMS. A transport with a fallback gets one attempt; the last one
    retries with backoff up to `NOTIFICATION_MAX_ATTEMPTS`.
  - **One queue event, one message:** a unique dedupe key built from the ticket and the event.
- **The patient ticket page** (Issue 68, PR #187; migration `0032`).
  - `/t/{token}`: a 43-character unguessable link, never an id. It shows the number, the place in line, a
    wait range that counts down, "you are next" and "please come in now" filling the top of the screen,
    directions and call buttons, and two-tap cancel for the ticket's own patient only.
  - **Live** over a server-sent stream in the board's format, with its own budget of 500 streams, and a
    poll fallback. **The data's age is always shown**, and "Not live" appears when updates stop.
  - Contract: `GET /api/v1/tickets/{page_token}` in `contracts/queue.yaml`.
- **Web push** (Issue 64, PR #188; migration `0033`).
  - VAPID with `py-vapid` 1.9.4 and `http-ece` 1.2.1, keys from `python -m scripts.generate_vapid_keys`
    held as secrets.
  - **Asked only when the patient presses the button on their own ticket**, never on load. Declining falls
    back to SMS, and the page says so.
  - **The payload is the ticket number and clinic name only**, encrypted so only the phone can read it.
  - **A subscription the push service calls gone** (404/410) is deleted on that first failure.
  - **Endpoints are allowed only on known push services** (SSRF). `docs/OPS/WEB_PUSH.md` sets out what
    iPhones do and do not do.
- **The SMS gateway with cost caps** (Issue 65, PR #189; migration `0034`).
  - An Africa's Talking adapter, **sandbox by default** (`SMS_SANDBOX=true`), with the cost of each message
    recorded.
  - **Daily caps:** 300 per clinic (settable per clinic at `PUT /api/v1/sites/{id}/sms-budget`) and 8 per
    patient. Hitting a cap stops the message (`suppressed`) and alerts the team once a day.
  - **Kill switch:** `PUT /api/v1/notifications/sms/kill-switch` stops every SMS on the next message, with
    no deploy.
  - **Delivery receipts** come in on a secret callback URL (the gateway does not sign), applied once each.
  - **One segment:** look-alike characters normalised to GSM 7-bit, every queue message fitting 160
    characters with the longest names the database allows, and more than `SMS_MAX_SEGMENTS` refused.
- **Versioned, per-language templates and their editor** (Issue 66, PR #190; migration `0035`).
  - The words live in `src/locales/<language>/notifications.toml`, and every version sent is stored
    unchanged. Each ledger row names the version and language it used, so a retry repeats the first
    attempt's words.
  - **A broken template is refused at registration:** an unknown or unsafe blank, no ticket number, a web
    push naming more than the number and clinic, or an SMS over one segment.
  - **The operators' console and editor** at `/admin/notification-templates` has a live preview and an SMS
    counter.
- **Preferences, quiet hours and STOP** (Issue 67, PR #191; migration `0036`).
  - **By the ticket link, no account:** stop all messages, a preferred channel, a language, quiet hours
    and which messages to skip, from **Message settings** on the ticket page.
  - **Quiet hours hold everything except** "you are next", "please come in now" and "you were called
    again". An opt-out stops those too.
  - **Replying STOP to any SMS** (`/api/v1/webhooks/sms/africastalking/{token}/inbound`) stops every
    channel at once, including messages already waiting. START undoes it.
  - **A source guard** fails if a transport can dispatch without passing the gate.
- **The installable patient app** (Issue 69, PR #192).
  - A manifest (scope `/t/`, standalone, PNG and maskable icons) and the patient service worker extended
    with an offline shell, separate from the board's.
  - **With no network**, a `/t/` page shows the last known state of the ticket with its age, kept for at
    most five tickets and 18 hours.
  - **A new deploy is picked up on the next launch:** the release version is written into the worker, and
    every page asks for an update.
  - **"Add to home screen" is offered only to the patient who joined**, on their own ticket; the browser's
    own banner is always held back.
  - **Lighthouse 11.7.1's PWA category passes.**
- **The ticket QR and reception lookup** (Issue 70, PR #193).
  - **One code in two forms:** a QR saying `CLINICQ:K7M-4QP`, and the code spelt out to say aloud. The
    ticket page, its offline copy and the printed stub draw it from the same data.
  - **Find ticket** at `/dashboard/sites/{site}/lookup` (shortcut `f`) takes a desk scanner's keystrokes or
    a typed code, and opens that ticket. A code works only at its own clinic on its own day, an ended
    ticket's code is used up, and a transferred ticket's code follows the visit.
  - API: `GET /api/v1/sites/{site_id}/tickets/lookup`. `docs/OPS/RECEPTION_LOOKUP.md` covers scanner setup.
- **The notifications contract, failure-path tests and delivery health** (Issue 71, PR #194; migration
  `0037`).
  - `contracts/notifications.yaml` documents every notification route with its error responses, checked
    by Issue 30's drift test. `test_notifications_contract.py` drives each documented error over HTTP.
  - **Every failure path ends terminal, re-verified with the real adapters:** a provider timeout, a provider
    error, a malformed number, a revoked push subscription and an unplanned error. The last one found a real
    hole: an error before the transport was reached escaped the attempt, left the row queued forever and
    stopped the sweep. `attempt_or_record` now records it as a failed attempt.
  - **A replayed queue event sends exactly one message:** the move, "you are next", the recall sweep and
    the post-commit delivery, each replayed.
  - **Delivery by transport** on `/admin/notifications` (`GET /api/v1/notifications/delivery-stats`):
    sent, delivered, failed, not sent, waiting, failure rate and cost.
  - **The failure-rate alert** (`notification_failure_watch`, every 5 minutes) tells the team once per
    transport per hour when at least 25% of 20 or more attempted messages were dead-lettered.
  - **No notification test can reach a real provider.** A fixture fails any HTTP request that leaves the
    machine.

## Migrations

Seven revisions, `0031` to `0037`, applied in order by `scripts/db/deploy-sequence.sh`. Each adds nullable
or defaulted columns or new tables, so the release before this one runs unaffected on the new schema.
Only `0032` writes to existing rows on the way up. Each is reversible, with the losses below.

- **`0031_notification_patient_cost`**: `notification` gains `patient_id`, `site_id`, `event`, `dedupe_key`
  (unique), `fallback_of_id`, `cost` and `cost_currency`; creates `patient_notification_preference`.
  **The downgrade loses** the patient, clinic, cost and dedupe details of every patient message.
- **`0032_ticket_page_token`**: adds `ticket.page_token` (unique) and **gives every ticket still in its day a
  token** on the way up. **The downgrade drops it and every shared ticket link stops working.**
- **`0033_push_subscription`**: creates `push_subscription`. **The downgrade makes every browser
  subscribe again.**
- **`0034_sms_budget`**: adds `site.sms_daily_cap`; creates `platform_switch`, `sms_cap_alert` and
  `sms_delivery_event`. **The downgrade loses** the kill switch state, each clinic's cap and the receipt
  history.
- **`0035_notification_template_versions`**: creates `notification_template_version`; adds
  `notification.template_version_id` (`RESTRICT`) and `language`, and
  `patient_notification_preference.language`. **The downgrade loses** edited template versions and which
  words each message used.
- **`0036_patient_preferences_quiet_hours`**: adds quiet hours, `muted_events`, `opted_out_at` and
  `opted_out_via` to `patient_notification_preference`; creates `sms_inbound_event`. **The downgrade loses
  every patient's opt-out and quiet hours**: after it, a patient who replied STOP would be messaged again.
- **`0037_notification_failure_alert`**: creates `notification_failure_alert`. The downgrade loses only the
  record of past alerts.

## Upgrade notes

- **Patients start receiving messages.** A patient who has agreed to notifications (Issue 21) is told "you
  are next" and "please come in now" from the moment this is deployed. With `SMS_PROVIDER` left at its
  default (`logging`), an SMS is only written to the log, though the ledger records it as `sent`. Web push is
  off until VAPID keys are set.
- **SMS is sandbox until switched.** Going live needs `SMS_PROVIDER=africas_talking`, a real
  `AFRICAS_TALKING_USERNAME` and `AFRICAS_TALKING_API_KEY` as secrets, and `SMS_SANDBOX=false`, in that
  order (`docs/OPS/SMS_GATEWAY.md`). Set `SMS_WEBHOOK_TOKEN` and register both callback URLs (receipts and
  inbound replies) in the gateway dashboard, **or STOP replies are not received**.
- **Web push needs keys.** Generate once with `python -m scripts.generate_vapid_keys` and set
  `WEB_PUSH_VAPID_PUBLIC_KEY`, `WEB_PUSH_VAPID_PRIVATE_KEY` and `WEB_PUSH_VAPID_SUBJECT` as secrets.
  Rotating the key pair makes every browser subscribe again.
- **Twenty-two new settings**, all with defaults (`.env.example` regenerated):
  - SMS: `AFRICAS_TALKING_USERNAME`, `AFRICAS_TALKING_API_KEY`, `SMS_SANDBOX`, `SMS_SITE_DAILY_CAP`,
    `SMS_PATIENT_DAILY_CAP`, `SMS_MAX_SEGMENTS`, `SMS_WEBHOOK_TOKEN`;
  - dispatch: `NOTIFICATION_DISPATCH`, `NOTIFICATION_DISPATCH_WORKERS`,
    `NOTIFICATION_DISPATCH_GRACE_SECONDS`, `NOTIFICATION_COST_CURRENCY`;
  - the failure alert: `NOTIFICATION_FAILURE_ALERT_RATE`, `NOTIFICATION_FAILURE_ALERT_MIN_ATTEMPTS`,
    `NOTIFICATION_FAILURE_ALERT_WINDOW_MINUTES`, `NOTIFICATION_FAILURE_WATCH_MINUTES`;
  - web push: the three `WEB_PUSH_VAPID_*` keys, `WEB_PUSH_ALLOWED_HOSTS`, `WEB_PUSH_REQUIRE_HTTPS`,
    `WEB_PUSH_TTL_SECONDS` and `WEB_PUSH_TIMEOUT_SECONDS`.

  Run `make check-config` after copying.
- **One new scheduler job:** `notification_failure_watch`, every 5 minutes under advisory lock 771. The
  existing retry sweep now records an unplanned error on its row and carries on, rather than stopping.
- **New public routes** (no sign-in, each protected otherwise):
  - `/t/{token}`, its stream and `GET /api/v1/tickets/{page_token}`, by the unguessable link;
  - `/t/`, `/t/offline`, `/patient-sw.js` and `/static/manifest.json`;
  - `GET`/`PUT /api/v1/notifications/patient-preferences/{page_token}`, by the same link;
  - `GET /api/v1/notifications/web-push/key`;
  - the two SMS callbacks, by their secret path.
- **New staff and operator surfaces:**
  - **Find ticket** in the front desk's and the manager's menu (not a nurse's);
  - the template console and editor (`logs`, publishing `logs` update);
  - the delivery panel on `/admin/notifications`;
  - the kill switch and SMS budget APIs.
- **Put `/t/{token}/stream` behind a proxy that does not buffer it,** as for the board's stream.
- **The ticket page's service worker needs a secure origin** (HTTPS or `localhost`), like the board's.

## Known issues

- **Nothing that needs a person or a device has been done.** Each is left open, with a place to record it:
  - **a real Android phone receiving "you are next" by web push** within 5 seconds (Issue 64,
    `docs/OPS/WEB_PUSH.md`), and **installing the app** from Chrome and launching it standalone (Issue 69,
    `docs/OPS/PATIENT_APP.md`);
  - **the ticket page on a real phone on 3G** (Issue 68). Only emulated Slow 3G and 320 px were measured;
  - **a real desk scanner** reading a phone and a stub, and **a code read aloud** to a teammate (Issue 70,
    `docs/OPS/RECEPTION_LOOKUP.md`);
  - **a live Africa's Talking account:** no real SMS, receipt or STOP reply has passed through the gateway
    (Issues 65, 67);
  - **nothing on an iPhone.** A Home Screen web app keeps storage apart from Safari, so iPhone patients should
    expect SMS.
- **Only English is written.** A patient who prefers isiZulu, isiXhosa, Afrikaans or Sesotho gets English
  until Issue 77 (M10) adds the words, and no fluent speaker has reviewed anything (Issue 66).
- **Agreements the issues asked for were not obtained, and are recorded as not obtained:**
  - F's confirmation of Issue 66's order of work (the registry now, the other languages with #77);
  - B and C's agreement on Issue 70's lane;
  - whether a regulator or the gateway requires a confirmation SMS after STOP (Issue 67 sends none).
- **Anyone holding a ticket link can change how that patient is told,** including stopping messages. The
  page says so when the link is shared (Issue 67). The worst case is a patient not told they are next by
  someone they shared the link with; the page itself still shows it.
- **Preferences by USSD and WhatsApp** are in Issue 67's scope but arrive with those channels (M10).
- **WhatsApp reaches nobody yet.** The transport is in the chain, but with no provider configured it has no
  address for anyone, so the chain skips it and patients get web push or SMS until Issue 76 connects it.
- **Kept ticket states stay on a phone** for up to 18 hours (at most five tickets): the number, clinic,
  place in line and code, never a name.
- **The delivery panel's "Over the alert rate"** applies the alert's minimum attempts to whatever window is
  shown. The alert itself always measures the last `NOTIFICATION_FAILURE_ALERT_WINDOW_MINUTES`.
- **Everything from v0.8.0's list that M9 did not touch still stands.**
  - Nothing from the display soak or the kiosk checks has been redone.
  - The notification bell answers 403 for clinic roles.
  - The consent wording is a draft for the M13 review.
  - The credential in the repository's history is still not rotated, and nothing is provisioned.
  - **Only `v0.2.0` has ever been tagged**: `v0.1.0` and `v0.3.0`–`v0.8.0` have release notes but no tags,
    and they should be cut in order before this one.

## Verification

Run on the Issue 71 branch based on `main` after #193, which is `main` as #194 will leave it. It used
PostgreSQL 18 + PostGIS and Redis in Docker, both required rather than skippable, and Playwright's
Chromium:

```text
TZ=UTC pytest -q -n auto --dist loadscope tests
                                      2378 passed, 2 failed, 1 skipped, 9 xfailed in 495 s
  the two failures, run again alone   2 passed (a dashboard Call next test and the patient app's
                                      deploy test, both browser tests, while a second suite ran alongside)
notification, template and contract suites with every provider variable empty
                                      284 passed
ruff check . / ruff format --check    clean
mypy src/                             clean (302 files)
```

CI ran every shard on every pull request, including the browser shard, and each merged with every check
green. Each pull request carries its own evidence, and it is worth reading beside the suite:

- **#186:** *Call next* committing while the SMS provider is down, a slow provider not holding a request, a
  rolled-back move sending nothing, and the chain's order and costs on the ledger.
- **#187:** the page following three calls to "please come in now" with no reload, "Not live" and the data's
  age after a dead router, two-tap cancel, and 320 px and emulated Slow 3G screenshots.
- **#188:** no permission prompt on load, a subscription stored on "allow", SMS on "decline", and a push
  decrypted as a browser would, carrying only the number and clinic.
- **#189:** the sandbox by default, caps stopping and alerting once, the kill switch taking effect on the next
  message, receipts applied once, and every message in one segment with the longest names.
- **#190:** a template refused at registration, the preview per channel, and a retry after an edit repeating
  the words it was first sent with.
- **#191:** STOP by SMS stopping a message already waiting, quiet hours holding all but the three "come now"
  messages, settings saved from a shared link, and the gate guard failing on a transport that skips it.
- **#192:** the offline page showing the last known place in line with its age, a deploy picked up on the
  next launch, the bounded cache, the install offer, and Lighthouse 11.7.1's PWA score of 1. Its browser job
  failed three times in CI before a real bug (worker fetches left hanging) was found and fixed.
- **#193:** a scan's keystrokes opening that ticket, the page's QR read back by a real QR reader,
  yesterday's code refused with its day, and the stub carrying the same code.
- **#194:** every documented error driven over HTTP, each failure path ending terminal with the real
  adapters (including the hole it found), replays sending one message, the delivery panel, and the failure
  alert posted once against PostgreSQL through the scheduler's entry point.
