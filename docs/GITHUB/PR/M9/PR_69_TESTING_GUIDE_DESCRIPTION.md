# PR: Write how to test the patient app as a patient and the front desk would (Issue 69 follow-up / M9-69)

**Milestone:** [Milestone 9: Notifications & Patient PWA](https://github.com/Billykat7/clinicQ/milestone/9) ·
**Issue:** [#69](https://github.com/Billykat7/clinicQ/issues/69) (installable patient app), closed by PR #192

M9 shipped the patient ticket page as an installable app, but nothing said how a person tries it end to end.
The obvious questions are "is there a link on the home page?" and "where do I download the mobile app?". This
PR adds `docs/OPS/PATIENT_APP_TESTING.md`, a walkthrough of the whole journey on a local ClinicQ, as a patient
and as the front desk. It starts by saying what exists today and what does not.

**What the guide says is missing (found while writing it, not changed here):**

- **No web page lets a patient sign in and join.** The clinic page's **Join the queue** button is disabled by
  default (`PATIENT_JOIN_ENABLED=false`). Switched on, it links to `/discover/clinics/{slug}/join`, which
  answers **404**. Signing in with an SMS code and joining work through the API only.
- **No app-store download.** The app is the ticket page at `/t/{token}`, installed with the browser's
  **Add to home screen**, offered only on the patient's own ticket.
- **No appointments for a time.** Those are M11 (Issues 80 and 81). Today a patient joins a running queue.

Until a join page exists, the guide stands in for it with one console snippet. Pasted on a ClinicQ page, it
asks for a code, reads it from `/dev/outbox`, signs in, agrees to notifications, joins and opens the ticket.
Because it runs in the page, the browser holds the patient's session, so the owner's features (install,
cancel, message settings, push) all show.

## Changes

- **`docs/OPS/PATIENT_APP_TESTING.md`** (new):
  - 0: what works in the browser today, step by step;
  - 1: local setup (`ENVIRONMENT=development`, `SMS_ENABLED=true`, optional VAPID keys, demo data, the four
    seeded staff accounts);
  - 2: two browser profiles for patient and reception;
  - 3: find a clinic and join;
  - 4: reception calls, and the patient sees "You are next" and "Please come in now" with the matching
    messages;
  - 5: shared link, message settings, push, cancel, reception lookup;
  - 6: install and offline;
  - 7: real phones (Android over USB port forwarding; iPhone limits);
  - 8: troubleshooting.
  - Each step not checked by hand is marked **not checked**.
- **`docs/OPS/PATIENT_APP.md`**: links to the new guide.

## Testing

Run on 15 September 2026 against `main` on a fresh database `clinicq_pwa_guide` (migrations to `0037`,
`make seed-rbac`, `make seed-dev-data`), `ENVIRONMENT=development SMS_ENABLED=true PATIENT_JOIN_ENABLED=true`.

**The join button leads to a 404** (clicked on `/discover/clinics/hillbrow-chc`):

```text
link "Join the queue" href="/discover/clinics/hillbrow-chc/join"
{"detail":"Not Found","code":"http.not_found","request_id":"01a0a45f-0026-7551-8174-2d1bda9f330f"}
```

**The snippet, exactly as written in section 3**, run in Chromium on the clinic page with another number:

```text
"You are A004. /t/Kwx9nOh7WGRh-R1pmhE29Imn7tbcPavBTxtkuWbY4wg"
→ tab title "You are next · A004"
```

Without the CSRF header the join is refused, which the troubleshooting table lists:

```text
{"detail":"Invalid or missing CSRF token"}
```

**Reception calls; the patient's page follows with no reload, and the messages land in `/dev/outbox`**
(Call next made as `reception@clinicq.example`):

```text
tab title: "Ticket A002" → "You are next · A002" → "Come in now · A002"
page: "Please come in now / Go to General consultation."

+27825550202 | BK ClinicQ: ticket A002 at Hillbrow Community Health..., you are next. Please be ready at General consult....
+27825550101 | BK ClinicQ: ticket A001, please come in now to General consult... at Hillbrow Community Health....
```

**The owner's ticket page** (phone width): Live, place in line, wait range, Directions, Share, the install box,
Cancel my ticket, Message settings, and At reception with the code. With VAPID keys set, it also shows
`button "Tell me on this phone when it is my turn"`.

**The app's worker, caches and start page:**

```json
{ "scope": "http://localhost:8040/t/", "active": true, "controlled": true,
  "manifest": "http://localhost:8040/static/manifest.json",
  "caches": ["clinicq-patient-tickets", "clinicq-patient-shell-1.0.0"],
  "startUrl": "http://localhost:8040/t/ywtV5WiUJbTHkAOWNYoQ7VUsvfRyCx54My14Awp-FvM" }
```

**Offline:** the server was stopped and the ticket reloaded:

```text
Offline · A002
No connection. This is how your ticket stood the last time this phone could check: your place may have moved since.
You were number 1 in line / You were next. / Show this, or say your code FPY-FC7 / Last updated 11:23 · 51 s ago / Try again
```

**Cancel** from the page (Cancel my ticket, then Yes, cancel my ticket):

```text
Your ticket / Ended / A003 / This ticket was cancelled and is no longer in the queue.
```

**Reception:** `/dashboard` as the receptionist redirects to Hillbrow's dashboard, which links **Board**,
**Walk-in** and **Find ticket**. The lookup finds the patient's code:

```text
302 http://127.0.0.1:8040/dashboard/sites/01a0a45e-a374-75fa-89b5-f829354a9054
GET /api/v1/sites/{site}/tickets/lookup?code=FPY-FC7 → {"ticket":{"number":"A002","reference_code":"FPY-FC7","status":"waiting",...}}
```

**Not checked, and marked so in the guide:** pressing the install button by hand, push arriving in a desktop
browser, the Android USB port-forwarding route end to end, and any iPhone. The staff screens were checked over
HTTP with the receptionist's session, not by signing in through the browser.

`make milestone-progress`: `14 milestone(s): up to date`. This follow-up closes nothing, so it assumes no issue closed.

## Risk and rollback

- **Docs only.** No code, migration or configuration changes. Rollback is a revert.
- **The snippet uses the dev outbox**, which exists only with `ENVIRONMENT=development`, so it cannot sign
  anyone in on staging or production.
- **The guide goes stale when the join page ships.** Sections 0 and 3 then need the page in place of the
  snippet.

Refs #69
