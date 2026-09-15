# PR: "You are next" arrives on the patient's phone by web push, asked for only after a button press (Issue 64 / M9-64)

**Milestone:** [Milestone 9: Notifications & Patient PWA](https://github.com/Billykat7/clinicQ/milestone/9) ·
**Issue:** [#64](https://github.com/Billykat7/clinicQ/issues/64) · **Builds on:** #63 (the notification
service, PR #186) and #68 (the ticket page, PR #187), both merged · **Unblocks:** #71 (contract and failure
tests)

Web push is free and instant, and it works without an app store, so it is the first transport a patient
notification tries. With this PR:

- **On their ticket page, a patient can press *Tell me on this phone when it is my turn*.** The browser's
  permission prompt appears only then, never when a page loads.
- **"You are next" and "please come in now" reach the phone as an encrypted push.** Only that browser can
  read it; the push service carries it without seeing the words. A push says the ticket number and the clinic
  and nothing else.
- **A dead subscription is removed at its first failed send**, and that message goes by SMS instead.
- **A patient who declines gets SMS**, and the page tells them so.

**Not done here, and not claimed:**

- **No real Android phone has received a push yet.** The criterion "an installed PWA receives a push within
  5 seconds of Call Next" is left unticked. It needs a person, a phone and staging over HTTPS.
  `docs/OPS/WEB_PUSH.md` §4 has the procedure and an empty record. What is proven here is the server's half,
  against a local push service: the message is sent 0.07 s after *Call next*, encrypted, signed and readable
  by the browser.
- **iPhones are not claimed to work the same way.** iOS allows web push only from a Home Screen web app, and
  that needs the manifest from #69, so iPhone patients get SMS until then. The limits are written down in
  `docs/OPS/WEB_PUSH.md` §3.
- **No queue-position updates are pushed.** The issue lists them among the payloads. A push for every place
  a patient moves up would buzz a phone a dozen times a morning, while the ticket page already shows the
  position live. Only the moments that need action are pushed: next, called, recalled, missed, transferred
  and cancelled.
- **No keys are set on any environment.** Generating and storing them is an operator step (§1 of the doc).
  Until then web push is off everywhere and nothing changes for patients.

## Summary

- **Libraries, chosen deliberately and pinned** (`requirements.txt`): `py-vapid==1.9.4` signs the VAPID token
  (RFC 8292), and `http-ece==1.2.1` encrypts the payload (RFC 8291, `aes128gcm`). Both depend only on
  `cryptography`, which the project already has.
  - The POST is made with the `httpx` already in the tree.
  - `pywebpush` does the same three steps, but it would add `aiohttp` and `requests` to the image.
  - `pyproject.toml` tells mypy the two libraries ship no type information.
- **Keys as secrets** (`src/core/config.py`): `WEB_PUSH_VAPID_PUBLIC_KEY`, `WEB_PUSH_VAPID_PRIVATE_KEY` and
  `WEB_PUSH_VAPID_SUBJECT`.
  - The configuration guard refuses one set without the others, and a subject that is not `mailto:` or
    `https:`.
  - Also new: `WEB_PUSH_TTL_SECONDS` (900: "you are next" is no use an hour later),
    `WEB_PUSH_TIMEOUT_SECONDS` (5), `WEB_PUSH_ALLOWED_HOSTS` and `WEB_PUSH_REQUIRE_HTTPS`. The last must be
    true in production.
  - `python -m scripts.generate_vapid_keys --subject mailto:…` prints a new pair and writes nothing.
    `.env.example` is regenerated with the settings empty.
- **Subscriptions** (`push_subscription`, migration `0033`; `src/modules/notifications/webpush.py`):
  - one row per browser endpoint, unique by the endpoint's SHA-256;
  - `subscribe` refreshes a known endpoint, and moves it to the current patient if another patient had it;
  - `targets_for` skips expired rows; `forget` and `mark_sent` are the other two operations.
- **The endpoint is checked (SSRF).** The server POSTs to whatever a browser hands it. `endpoint_allowed`
  accepts only `https` to Google's, Mozilla's, Apple's or Microsoft's push services (and their subdomains),
  with no user info and no other port. It is checked when a subscription is stored and again before each send.
- **The sender** (`VapidSender`): a fresh server key and salt per message, a VAPID token valid 12 hours,
  `TTL`, `Urgency: high`, `Content-Encoding: aes128gcm`. The push service's answer decides what happens:
  - `404`/`410`: `SubscriptionGoneError`, and the subscription is forgotten;
  - `400`/`413`: permanent failure;
  - a timeout, `429` or `5xx`: retryable.
- **The transport** (`transports/webpush.py`): it reaches a patient who has a subscription while web push is
  configured.
  - `payload_for` builds `{title, body, url, tag}` (`PAYLOAD_KEYS`) and nothing else.
  - The web push words live in `templates._PUSH_WORDS`, with `{number}` and `{clinic}` as their only blanks.
  - The link is the ticket's page, added to the context by `queue/notices.py`.
  - A transport's `send` now receives the patient's addresses, because a push subscription id alone does not
    say which keys to encrypt to.
  - `transport_errors.py` holds the two error classes, so the sender can raise them without an import cycle.
- **The service** (`service.attempt`) deletes a subscription the push service calls gone, records
  `last_sent_at` on success, and falls back down the chain as #63 built.
- **Routes** (`src/modules/notifications/router.py`):
  - `GET /notifications/web-push/key`: public, listed in the route-gate guard with its reason;
  - `POST` and `DELETE /notifications/web-push/subscriptions`: the patient's own session only. `422` for a
    refused endpoint or bad keys, `409` while web push is off.
- **The page:**
  - `TicketPageOut.push_key` and `push_subscribe_url` are set only for the ticket's own signed-in patient,
    while the ticket is active and web push is on;
  - `ticket.html` shows the button and every sentence push.js may say;
  - `src/static/js/push.js` asks for permission on the press, registers the worker, subscribes and posts;
  - `src/static/patient-sw.js` (served at `/patient-sw.js`, scope `/t/`) shows the notification and opens
    the ticket when it is tapped. It is separate from the board's worker, and #69 adds the offline shell to it.
- **Docs:** `docs/OPS/WEB_PUSH.md` covers the keys and their rotation cost, what a patient sees, a platform
  table with iOS limits stated plainly, and the Android check with its empty record.

## Design notes

**Why the prompt waits for a button.** Browsers penalise sites that prompt on load (Chrome quietly blocks
prompts from sites people keep dismissing), and a patient who has just joined does not yet know why they would
want notifications. The button says what they get. A patient who had already allowed notifications on this
phone is re-subscribed silently, without a prompt. A patient who declined is told they will get SMS, and the
service's chain delivers it: with no subscription, web push has no address and is never tried.

**Following is not owning, again.** A phone with only the ticket link gets no button, because a subscription
belongs to a patient, not a ticket. A family member who could subscribe would receive that patient's messages
on every later visit. The routes require the patient's own session, and the page data offers push only to that
session.

**What a push may say.** A lock screen is public, so the payload has the ticket number and the clinic's name.
The queue is left out because a queue can be called "HIV clinic", and the room, reason and name are left out
too. `test_push_payload.py` renders every patient message for web push from a context full of those values and
fails if any of them appears.

**A 503 is not a dead phone.** A push service outage sends that message by SMS, because a free transport gets
one attempt when a fallback is behind it (#63), but the subscription is kept. Only `404` and `410` delete it,
which is what "dead" means in RFC 8030.

**Known limit:** a patient with two subscribed phones, whose newest subscription is gone, gets that message by
SMS rather than on the second phone. The next message uses the second phone, because the dead one has been
removed.

## Changes

- **New:**
  - `src/modules/notifications/webpush.py`, `transport_errors.py`
  - `src/database/models/push_subscription.py`, `alembic/versions/0033_push_subscription.py`
  - `src/static/js/push.js`, `src/static/patient-sw.js`
  - `scripts/generate_vapid_keys.py`, `docs/OPS/WEB_PUSH.md`
- **Changed:**
  - `transports/webpush.py` (the real transport), `transports/base.py`, `sms.py`, `whatsapp.py`, `noop.py`
    (the `patient` argument), `registry.py`
  - `service.py`, `templates.py`, `schemas.py`, `router.py`
  - `queue/notices.py` (`page_url`), `queue/ticket_page.py`, `queue/schemas.py`, `templates/queue/ticket.html`,
    `ticket.css`, `web/ticket.py`, `main.py`
  - `config.py`, `requirements.txt`, `pyproject.toml`, `.env.example`
- **Tests, new:**
  - `tests/integration/notifications/test_web_push.py` (8)
  - `tests/unit/notifications/test_push_payload.py` (7)
  - `tests/e2e/patient/test_push_permission.py` (3)
- **Tests, updated:** `tests/e2e/patient/conftest.py` (web push on), `test_api_route_gates.py`, and the
  exploding test transport in `test_patient_notifications.py` (the new `send` signature).
- **Docs:** the Issue 64 spec (library decision, files), the M9 status row and progress bars
  (`--assume-closed 64`), and the README Status block (65 of 109).

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (289 files) clean.
- [x] `TZ=UTC pytest tests/ -n auto` on the Docker PostgreSQL 18 and Redis, with browsers: **2140 passed,
  1 failed**. The failure was #63's exploding test transport, whose `send` lacked the new `patient` argument.
  It is fixed in this PR and its file re-runs green (41 passed in `tests/integration/notifications`).
- [x] **How to verify, step 1, the server's half** (`test_call_next_pushes_a_message_only_the_browser_can_read_within_5_seconds`).
  A local push service receives the POST; the test holds the browser's private key and verifies the VAPID
  signature:

  ```text
  push received 0.067 s after Call next
  decrypted by the browser: {"title": "Please come in now", "body": "Ticket T001 at Hillbrow Community Health Centre.", "url": "/t/cmexZ6fhlY48U045Gous4YNdwbUP1wXJMZuQCJqVtAo", "tag": "clinicq-ticket-T001"}
  ```

  The same test asserts that the ticket number is **not** readable in the bytes the push service received,
  and checks the headers: `Content-Encoding: aes128gcm`, `Urgency: high`, `TTL: 900`, and a VAPID token for
  that push service's origin, signed by the configured key.
- [x] **How to verify, step 2: no prompt on load** (`test_push_permission.py`, real Chromium). The page is open
  and live for 1.5 s with `requestPermission` called **0** times. One press calls it **once**, the
  subscription is stored, and `/patient-sw.js` is the active worker for `/t/`. Declining says "SMS instead" and
  stores nothing; a follower's page has no push block. By hand on the development server: `granted asks on
  load: 0 after press: 1`, and the same for `denied`.
- [x] **How to verify, step 3: an expired subscription** (`test_a_dead_subscription_is_removed_at_the_first_failed_send_and_sms_takes_over`).
  The push service answers `410`; the subscription row is gone; the web push row is `dead` with `410` in its
  error after 1 attempt; an SMS row with `fallback_of_id` pointing to it is `sent`.
- [x] Also: subscribing only to allowed push services (the cloud metadata address, a look-alike host, user
  info and `file://` are refused with 422; `http`, another port and an IP address are refused under
  production defaults); a 503 keeps the subscription; declining gets SMS; the key route and 409 with web push
  off; only the owner is offered push.
- [ ] **On a real Android phone:** not done (see above and `docs/OPS/WEB_PUSH.md` §4).

| The button, before anything is asked | Allowed | Declined |
|---|---|---|
| ![Ticket page with the Tell me on this phone when it is my turn button](https://github.com/Billykat7/clinicQ/blob/12c221fe91792c630f5ad05fe9a953a71262a06e/docs/GITHUB/PR/M9/assets/pr64/push-button-before.png?raw=true) | ![After allowing: this phone will be told when you are next and when you are called](https://github.com/Billykat7/clinicQ/blob/12c221fe91792c630f5ad05fe9a953a71262a06e/docs/GITHUB/PR/M9/assets/pr64/push-granted.png?raw=true) | ![After declining: we will send you an SMS instead](https://github.com/Billykat7/clinicQ/blob/12c221fe91792c630f5ad05fe9a953a71262a06e/docs/GITHUB/PR/M9/assets/pr64/push-denied.png?raw=true) |

## Acceptance criteria

- [ ] **An installed PWA receives a push within 5 seconds of Call Next:** not ticked, because no real phone was
  used. The server sends the push 0.07 s after *Call next* to a local push service, which proves the half the
  code controls. The rest (Google's push service, the phone's battery settings) needs the Android check in
  `docs/OPS/WEB_PUSH.md` §4, and "installed" needs the manifest from #69.
- [x] **A dead or expired subscription is removed automatically:** at the first `404`/`410` (test above). A
  subscription past the expiry its browser gave is never used.
- [x] **The permission prompt appears only after a patient joins a queue:** the button is on the ticket page,
  which exists only after joining, and the prompt waits for the press (0 asks on load, 1 after the press, in
  Chromium).
- [x] **Payloads carry no clinical detail beyond the ticket number and clinic name:** `PAYLOAD_KEYS`, the
  words with only `{number}` and `{clinic}`, and a test for every message with a queue called "HIV clinic",
  a room, a reason and a name in its context.
- [ ] **Push works on Android Chrome and desktop; iOS behaviour is documented with its limitations:** half done.
  The iOS limits are documented (`docs/OPS/WEB_PUSH.md` §3), and the protocol is proven against a local push
  service in Chromium. Neither Android Chrome nor a desktop browser has received a real push from Google's or
  Mozilla's push service: that is the §4 check.
- [x] **Declining push falls back to SMS if a phone number is present:** every patient has a verified number
  (Issue 17). With no subscription web push is not tried and SMS is sent (test), and the page tells the patient.

## Risk and rollback

- **Off until keys are set.** Nothing changes for patients or clinics on deploy.
- **Two new dependencies**, both small, pure Python on top of `cryptography`, and pinned. `http-ece` ships as a
  source distribution, built at install time like any pure-Python package.
- **Migration `0033`** adds one table; the previous release ignores it. The downgrade drops it, and browsers
  would subscribe again.
- **The transport `send` signature gained an optional `patient` argument.** Every in-tree transport is updated,
  and one test double outside the tree needed the same change.
- **Rollback** is a revert and a downgrade to `0032`. Patients then get SMS, as before this PR.

Closes #64
