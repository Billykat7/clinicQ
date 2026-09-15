# PR: Every ticket carries one QR and short code that reception scans or types to open it (Issue 70 / M9-70)

**Milestone:** [Milestone 9: Notifications & Patient PWA](https://github.com/Billykat7/clinicQ/milestone/9) ·
**Issue:** [#70](https://github.com/Billykat7/clinicQ/issues/70) · **Builds on:** #39 (the reference code),
#51 (the stub), #68 and #69 (PRs #187 and #192), all merged · **Unblocks:** #71, #83

"What was your number again?" slows every front desk. With this PR:

- **Every ticket shows one code in two forms:** a QR, and six characters to say aloud (`K7M-4QP`, spelt out
  as "Kilo 7 Mike, 4 Quebec Papa").
- **The same code is on the ticket page, on the page's offline copy (no signal needed) and on the printed
  stub.** All three draw the QR from the same data.
- **Reception opens the ticket with Find ticket.** It is one focused field: a desk scanner types the QR and
  presses Enter, or a receptionist types the code. That exact ticket opens, and the field is ready for the
  next patient.
- **A code works only at its own clinic, on its own day:** yesterday's code is refused, naming the day. An
  ended ticket's code is used up, and a transferred ticket's code opens the leg the visit is in now.
- **A code cannot be turned into another patient's ticket.** It is random and unrelated to the ticket's
  number, id or neighbours, another clinic's code is "not found" in the same words as a made-up one, and it
  never opens the patient's ticket page.

**Which lane, and who agreed it.** The spec names B (Integrations) as owner, and the sprint plan puts this
issue in C's lane (Frontend/Patient) in sprint 8, with "agree before sprint 8". **That agreement is not
recorded anywhere, and I could not obtain it.** This PR was built across both lanes (the lookup service and
API, and the patient-facing QR), so B and C should confirm who owns it from here, before #83 builds kiosk
check-in on it.

**Not done here, and not claimed:**

- **No real desk scanner has scanned a phone or a stub (How to verify, step 1).** What was checked: the page
  receives exactly the keys a keyboard-mode scanner sends, and a real QR reader (Chromium's `BarcodeDetector`,
  on macOS) reads the QR the page draws as its code.
- **Nobody has read a code aloud to a teammate (step 2).** The alphabet has no look-alikes and every
  character has one distinct spoken form, both tested, but a person-to-person check is still to do.
  `docs/OPS/RECEPTION_LOOKUP.md` has a place to record both.

## Summary

- **The code** (`src/modules/queue/ticket_codes.py`, new):
  - `qr_for(code)` draws `CLINICQ:K7M-4QP` as the smallest QR (version 1, medium error correction,
    alphanumeric mode, a four-module quiet margin), returned as one SVG path of unit rows. It is cached, since
    a code's QR never changes.
  - `read_code(raw)` accepts the scanned form or the typed form in any case, with or without the dash or a
    space. Anything else is `None`, including a `0` where a code can have none.
  - `spoken(code)` spells a code with the ICAO alphabet and digits.
  - `lookup(db, access, raw, today)` searches only `scoped_select(Ticket, access)`. Then: expired if
    `service_day` is not today; follow transfers to the open leg; ended if that leg is terminal.
  - `lookup_view` adds where the ticket stands and a sentence, for both the API and the page.
  - Refusals are domain errors with `queue.lookup.<reason>`: `not_a_code` 422, `not_found` 404, `expired`
    422 naming the day, `ended` 409.
- **The API:** `GET /api/v1/sites/{site_id}/tickets/lookup?code=` (`queueTicketLookup`, `queues.tickets`
  `read`) returns `TicketLookupOut`: the ticket, queue and room, `waiting_ahead`, `wait`, the spoken code,
  `followed_from` and `message`.
- **The page data:** `TicketPageOut.reference_qr` (`payload`, `size`, `path`) and `reference_spoken`.
- **The surfaces:**
  - `queue/_qr.html`, a macro drawing the SVG black on white in every theme, with crisp edges;
  - the ticket page's **At reception** block (QR, code, spoken form);
  - the offline page, where `ticket-offline.js` fills the same block from the kept state's `reference_qr`;
  - the stub, with a 26 mm QR on paper and the spoken form.
- **Reception** (`src/web/dashboard/lookup.py`, `dashboard/lookup.html`): **Find ticket** at
  `/dashboard/sites/{site}/lookup`. It is a nav destination (`ticket_lookup`, shortcut `f`, `queues.tickets`
  `read` at `assigned`, so the front desk and the manager, not a nurse) with its own rail icon. It is a plain
  `GET` form with an autofocused field, showing the ticket card or one sentence with a heading for each
  refusal.
- **The contract:** the new path with every refusal code, and `TicketQrOut`, `TicketLookupOut`, plus the two
  page fields, in `contracts/queue.yaml`. `test_queue_contract.py` learns the `queue.lookup.` family from the
  enum, as it does `queue.join.`.
- **Docs:** `docs/OPS/RECEPTION_LOOKUP.md` covers the code, what a lookup answers, and setting up a desk
  scanner (keyboard mode, Enter suffix, QR enabled, matching keyboard layout).

## Design notes

**The QR carries the code, not a link.** The patient's ticket page link (#68) already exists, and putting it
in the QR would let anyone who photographs a stub left on a chair follow that patient's ticket. The QR says
only `CLINICQ:` and the printed code, which opens nothing without a staff session at that clinic. The prefix
lets a scan of some other QR be recognised as "not a ticket code".

**The QR is data, drawn by the page.** The offline page (#69) has no network and the browser has no QR
library, but it does have the state the phone kept. Sending the modules as an SVG path in the page data means
the page, the offline copy and the stub draw the same symbol from the same source. A unit test reads each
path back into modules and compares them with the QR library's matrix.

**A keyboard scanner needs no code.** Desk scanners type what they read and press Enter. A focused field in a
`GET` form handles that with no script, no camera permission and nothing to install. It works the same for a
receptionist typing, and #83's kiosk can use the same service.

**"Single-use per visit" at the desk.** The code resolves only while its ticket, or the leg a transfer moved it
to, is open and today's. Once the visit ends, the code is used up. Kiosk check-in (#83) will use the same rule.

## Changes

- **New:**
  - `src/modules/queue/ticket_codes.py`
  - `src/web/dashboard/lookup.py`, `src/templates/dashboard/lookup.html`, `src/templates/queue/_qr.html`
  - `docs/OPS/RECEPTION_LOOKUP.md`
- **Changed:**
  - `src/modules/queue/router.py`, `schemas.py` (`TicketQrOut`, `TicketLookupOut`, page fields),
    `ticket_page.py`
  - `src/web/dashboard/walkin.py` (the stub's QR and spoken code), `src/core/nav_registry.py`,
    `src/main.py`
  - `src/templates/queue/ticket.html`, `patient/offline.html`, `print/ticket_stub.html`,
    `partials/clinic_icons.html`
  - `src/static/js/ticket-offline.js`, `src/static/css/ticket.css`, `ticket-stub.css`, `dashboard.css`
  - `contracts/queue.yaml`
- **Tests, new:**
  - `tests/unit/queue/test_ticket_codes.py` (37)
  - `tests/integration/queue/test_ticket_codes.py` (6)
  - `tests/e2e/dashboard/test_ticket_lookup.py` (2)
  - `tests/e2e/patient/test_ticket_qr.py` (1)
- **Tests, updated:**
  - `tests/e2e/patient/test_patient_app.py`: the offline page draws the kept QR;
  - `tests/integration/contracts/test_queue_contract.py`: the `queue.lookup.` family;
  - `tests/snapshots/rbac_decisions.txt`: the seven new Find ticket decisions;
  - `tests/unit/security/test_nav_registry.py` and `tests/integration/dashboard/test_dashboard_shell.py`:
    Find ticket in the desk's and the manager's menus, not the nurse's.
- **Docs:** the Issue 70 spec (files), the M9 status row and progress bars (`--assume-closed 70`), and the
  README Status block (70 of 109).

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (300 files) clean.
- [x] `TZ=UTC pytest tests/ -n auto` on the Docker PostgreSQL 18 and Redis, with browsers: **2272 passed,
  1 failed, 1 skipped, 9 xfailed**. The failure was the RBAC decision snapshot, which lists the seven new
  decisions for Find ticket: receptionist, clinic manager and platform admin allowed; nurse, patient, user
  and admin denied. Those are the intended grants, so I regenerated it (`make rbac-snapshot`) and it
  passes (4 passed).
- [x] An earlier run failed to start under xdist because the unit test drew random codes when it was
  collected, so each worker saw different tests. It now uses a seeded generator.
- [x] **How to verify, step 1, as far as software goes** (`tests/e2e/dashboard/test_ticket_lookup.py`):
  signed in as the receptionist, the field has focus on load. Typing the ticket's `CLINICQ:…` payload and
  Enter, as a keyboard scanner does, opens that ticket ("T002 is waiting in Triage, 1 ahead."), and the
  field is empty and focused again. The same test opens the stub and finds the same QR path and code.
- [x] **The QR reads back with a real reader** (`tests/e2e/patient/test_ticket_qr.py`, and by hand on the
  development server): Chromium's `BarcodeDetector` read the ticket page's QR as `CLINICQ:V3D-M2C`, the page
  data's payload for code V3D-M2C ("Victor 3 Delta, Mike 2 Charlie"). Linux Chromium has no reader, so in CI
  that test checks the block's size and skips the read, and the unit tests compare every module instead.
- [ ] **How to verify, step 1, with a desk scanner, and step 2, reading a code aloud to a teammate:** not done
  (above).
- [x] **How to verify, step 3 (yesterday's code):** the same browser test, with the code typed in lower
  case, shows "Code from another day. Code 7ZB-YKV was for Monday 14 September 2026. A ticket code works only
  on the day it was issued: the patient needs a new ticket today."
- [x] Unit (`test_ticket_codes.py`, 37 passed):
  - for 23 codes, the drawn path, read back into modules, is the QR library's version 1 alphanumeric
    symbol for the payload;
  - the alphabet has no look-alikes, and the spoken forms are one per character and distinct;
  - 13 scanned, typed and wrong inputs.
- [x] Integration (`test_ticket_codes.py`, 6 passed):
  - scan and typed lookups open the exact ticket;
  - the page data's QR and code equal the stub's, and the stub HTML draws that path;
  - yesterday's code gets 422 `expired` naming the day;
  - a transfer is followed with `followed_from`, and a cancelled ticket gets 409 `ended`;
  - 30 codes are unique; a number, an id and a `0` typo open nothing; clinic B's desk gets the made-up
    code's words for clinic A's code; a patient and a signed-out caller are refused;
  - the reception page's found and refused views, gated to the clinic's staff.
- [x] Offline (`tests/e2e/patient/test_patient_app.py`): with the router cut, the offline page's QR is the
  kept `reference_qr` path.

| On the ticket page | Offline, 320 px | The printed stub |
|---|---|---|
| ![The At reception block with the QR, code UAS-T33 and its spoken form](https://github.com/Billykat7/clinicQ/blob/9f68b5081b3046526a66717086ace7bdad9612df/docs/GITHUB/PR/M9/assets/pr70/ticket-code.png?raw=true) | ![The offline page showing the ticket and the same QR block from kept data](https://github.com/Billykat7/clinicQ/blob/9f68b5081b3046526a66717086ace7bdad9612df/docs/GITHUB/PR/M9/assets/pr70/offline-last-known.png?raw=true) | ![The ticket stub with the QR, reference and spoken code](https://github.com/Billykat7/clinicQ/blob/9f68b5081b3046526a66717086ace7bdad9612df/docs/GITHUB/PR/M9/assets/pr70/stub-with-qr.png?raw=true) |

| Reception: a scan opens the ticket | Reception: yesterday's code |
|---|---|
| ![Find ticket showing T002 waiting in Triage with its details](https://github.com/Billykat7/clinicQ/blob/9f68b5081b3046526a66717086ace7bdad9612df/docs/GITHUB/PR/M9/assets/pr70/reception-lookup-found.png?raw=true) | ![Find ticket refusing a code from the day before, naming the day](https://github.com/Billykat7/clinicQ/blob/9f68b5081b3046526a66717086ace7bdad9612df/docs/GITHUB/PR/M9/assets/pr70/reception-lookup-yesterday.png?raw=true) |

## Acceptance criteria

- [x] **Scanning the QR at reception opens that exact ticket:** the page data's payload, typed as a keyboard
  scanner sends it with Enter, opens that ticket's id. The QR the page draws reads back as that payload with
  a real QR reader. Not yet tried with a physical scanner (above).
- [ ] **The short code is readable aloud without ambiguity:** no `0 O 1 I L`, one distinct spoken form per
  character, and typed forms read back in any case or spacing, all tested. Not ticked, because nobody has
  read one aloud to a teammate yet.
- [x] **The QR renders offline from cached data:** with the router cut, the offline page draws the QR path
  from the phone's kept state, and it equals the kept `reference_qr`.
- [x] **A code from a previous day is rejected with a clear message:** "Code … was for Monday 14 September
  2026. A ticket code works only on the day it was issued: the patient needs a new ticket today.", over the
  API and on the page.
- [x] **The code cannot be reverse-engineered into another patient's ticket:** random and unique codes; a
  ticket's number or id opens nothing; another clinic's code gets the made-up code's words; patients and
  signed-out callers cannot look codes up; the QR never contains the page link.
- [x] **Both the printed stub and the on-screen ticket carry the same code:** the stub's QR path and code
  equal the page data's, over HTTP and in the browser.

## Risk and rollback

- **The ticket page and stub grow a block.** The page keeps its order: the code sits where the reference
  line was, and the 320 px test still passes.
- **A new menu entry for the desk and the manager.** Nothing else in the menu moves.
- **No migration.** The reference code has existed since #39.
- **Rollback** is a revert. The code stays in the database and on the page as the plain reference line it
  was.

Closes #70
