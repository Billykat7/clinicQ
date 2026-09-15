# PR: Check in at the door on a paired tablet, by scanning a QR or typing a phone number (Issue 83 / M11-83)

**Milestone:** [Milestone 11: Appointments, Check-in & Patient Care Extras](https://github.com/Billykat7/clinicQ/milestone/11) ·
**Issue:** [#83](https://github.com/Billykat7/clinicQ/issues/83) · **Builds on:** #61 (device pairing, the board's
own mechanism), #70 (the QR ticket code), #81 (booking and conversion, PR #210), #40 (the join service), all
merged · **Unblocks:** nothing waits on this one

The queue at 07:30 is not for the nurse, it is for reception, to say "I am here". This PR takes that sentence
at the door. With this PR:

- **A tablet by the door is paired exactly like a waiting-room screen**: it opens `/display`, shows a
  six-character code, and the manager types it in — choosing **Check-in tablet** instead of board. It then
  opens the check-in screen by itself and shows it all day.
- **A patient checks in three ways**: the QR on their ticket page or booking held up to a scanner, the
  six-character reference, or their phone number on a digits-only keypad.
  - **A ticket they already hold** is stamped arrived. Nothing about their place changes.
  - **A booking** becomes a ticket **there and then**, through #81's one conversion path, so they take the
    next number in the same sequence as everyone else.
- **The board updates** because the check-in goes through the same hook every queue write calls.
- **The screen clears itself after 20 seconds**, leaving nothing personal for the next patient in the door,
  and it shows no name, lists nobody and answers `no-store`.
- **Offline it says "This screen is offline. Please see reception."** rather than seeming to work.
- **There is nothing to browse away to**: the page has no link and no form, and the box is locked to one
  address by the kiosk-mode browser the setup guide installs.
- **Walk-ins at the door**, where the clinic switches them on: the open queues as buttons, with the phone
  number optional.

**Not done here, and not claimed:**

- **A tablet switched on with no network** shows the browser's own error page: unlike the board (#62), the
  check-in screen keeps nothing offline. A check-in nobody recorded is worse than no check-in, and the
  patient must go to reception in that case anyway. This is written in `docs/OPS/KIOSK_SETUP.md`.
- **No camera scanner in the page.** The tablet takes a scan from a USB or Bluetooth barcode scanner (which
  types the code and presses Enter, like the front desk's lookup since #70). A camera-based reader in the
  browser is not built.
- **Arrival is not a status.** `ticket.arrived_at` is a fact for the front desk, not a queue state, and no
  screen reads it yet: the dashboard column belongs with the desk's own work, not here.

## Summary

- **The model** (migration `0044`):
  - `display_device.kind` (`board` or `check_in`; every existing device is a board);
  - `ticket.arrived_at`;
  - `site.kiosk_walk_ins_enabled`.
- **Check-in** (`src/modules/appointments/checkin.py`):
  - `check_in` resolves what was read — a ticket of this clinic today, then a booking of this clinic today,
    then a patient by phone number — and stamps the arrival once;
  - `start_walk_in` issues a walk-in at the door through `join_queue`, with the patient's own answer about
    messages recorded as their consent;
  - refusals `not_a_code`, `not_found`, `ended`, `too_early` and `not_offered`, each with the sentence the
    screen shows.
- **One conversion path** (`conversion.py`): the ~25 lines the sweep ran per booking are now `convert_one`,
  which the sweep and the tablet both call. The sweep's behaviour is unchanged.
- **The pages** (`src/web/kiosk.py`, `templates/kiosk/checkin.html`, `static/js/checkin.js`,
  `static/css/kiosk.css`):
  - `GET /display/check-in` (the idle screen), `POST …/arrivals`, `POST …/walk-ins`, `GET …/state`;
  - they sit under `/display` so the device cookie, which is scoped to that path, works unchanged, and the
    router is mounted **before** the board's, whose `/display/{site_id}` would otherwise swallow them.
- **Pairing** (`display/devices.py`, `display/router.py`, `src/web/display.py`): `pair_device` takes a kind,
  the API carries it, and `/display` sends a paired device to its own page — board or check-in.
- **The manager's screen** (`dashboard/settings_devices.html`): *What this device is* on the pairing form, a
  *What it is* column in the list, and the clinic's *Walk-ins at the check-in tablet* switch, saved with
  `PUT /api/v1/sites/{id}/settings/kiosk` (documented in `contracts/sites.yaml`).
- **The guide** (`docs/OPS/KIOSK_SETUP.md`, Part D): the tablet, the scanner, the kiosk-mode command, the
  pairing, and what happens offline.

## Design notes

**The tablet is a device, not a user.** It holds no session and no role, so every read is narrowed by the
clinic its device row is paired to. A code from another clinic is the same "not found", to the letter, as a
code that never existed — the test asserts the two bodies are equal. The eleven reads are entered in
`tests/unit/security/test_site_scoped_queries.py` with that reasoning.

**Checking in moves nobody up the line.** A ticket is a place whether or not anyone has arrived (#81's late
rule). So `arrived_at` changes no status and no order; it tells the front desk the patient is in the
building. Stamped once: a second scan answers "You are already checked in", with the same number.

**A booking is converted, not duplicated.** The tablet calls the same `convert_one` the sweep calls, so
`uq_ticket_appointment` and the conditional `booked → converted` update guard the tablet exactly as they
guard the sweep. A patient who scans twice, or scans after the sweep has already converted them, gets their
one number.

**An hour, not all day.** A booking may be checked in from an hour before its time (or from the clinic's own
conversion lead time, if that is earlier — by then it is a ticket anyway). Earlier than that the screen says
*Your time is Wed 16 Sep 14:00. Please check in when it is closer.* A place given six hours early is a place
taken from whoever is there now.

**One change, one event.** A check-in that issues a ticket lets the join's own `QueueChanged` carry it,
rather than publishing a second one for the arrival stamp in the same transaction.

**Large targets, digits only.** Every button is at least 88 px tall (WCAG 2.2 AA asks 44), nothing is smaller
than 24 px, and the number is 5 rem. The only typing is digits, on the screen's own keypad; the scanner's
field is visually hidden and holds the focus, so a scan works whatever was last touched.

## Changes

- **New:**
  - `src/modules/appointments/checkin.py`, `src/web/kiosk.py`
  - `src/templates/kiosk/checkin.html`, `src/static/js/checkin.js`, `src/static/css/kiosk.css`
  - `alembic/versions/0044_kiosk_checkin.py`
  - `tests/integration/appointments/test_kiosk_checkin.py` (15)
- **Changed:**
  - Appointments: `conversion.py` (`convert_one`, `lead_minutes`).
  - Models and enums: `display_device.py`, `ticket.py`, `site.py`; `src/commons/enums.py`
    (`DisplayDeviceKind`).
  - Display: `devices.py` (`pair_device(kind=…)`), `router.py` (the kind on the API), `src/web/display.py`
    (`home_for`).
  - Sites: `router.py` and `schemas.py` (the kiosk walk-in switch).
  - Dashboard: `src/web/dashboard/settings.py`, `settings_devices.html`.
  - Wiring: `src/main.py` (the kiosk router, before the board's).
  - Contracts: `contracts/sites.yaml`.
- **Tests, updated:**
  - `test_board_privacy_endpoints.py`: the two new `/display` routes are in the privacy sweep, so no board
    endpoint can ship unsearched.
  - `test_site_scoped_queries.py`: eleven reasoned entries for the tablet's reads and `convert_one`.
- **Docs:** `docs/OPS/KIOSK_SETUP.md` (Part D), `docs/OPS/PATIENT_APP_TESTING.md`, the Issue 83 spec (files),
  the M11 status row and bars (`--assume-closed 83`), the README Status block (79 of 111), and sprint 9's row.
  GitHub's own milestone had drifted — issues 82 to 85 were sitting on Milestone 6 — and they are back on
  Milestone 11, which is where `make milestone-progress` reads its numbers from.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (326 files) are clean.
- [x] **Migration:** `alembic check` reports "No new upgrade operations detected". `downgrade 0043` and
  `upgrade head` both run cleanly, and 0044 ran on the demo database with #81's and #82's data in it.
- [x] **Full suite:** `TZ=UTC pytest tests/ -n auto --dist loadscope` on the Docker PostgreSQL 18 and Redis,
  with browsers, gave **2624 passed, 1 failed, 1 skipped, 9 xfailed**.
  - The one failure was `test_a_day_offline_leaves_the_heap_the_listeners_and_the_layout_where_they_were`
    (#62's board resilience), whose listener count read 45 in one sample against 39 in the baseline: a
    measurement taken while the board was reconnecting.
  - `pytest tests/e2e/display/test_board_resilience.py` straight after: **7 passed**. Nothing in this PR
    touches the board's own page or script.
- [x] **How to verify, on a running server.** A Playwright script drove the demo world on PostgreSQL
  (`clinicq_m11_demo` at 0044, the server on port 8050): a tablet at 800×1180 and a board at 1280×720, both
  paired through the real pairing flow.

  ```text
  1. paired: check_in demo check_in -> /display/check-in
  2. booking for +27825550829 in 20 minutes, reference J38-568
  3. scanned to checked in in 0.14 s: C004 Chronic medication collection
  4. board shows: yes - C004
  5. back to idle after the timeout; number on screen: False
  6. offline: This screen is offline. Please see reception.
  7. switch: 200
  8. walk-in at the door: T002 Triage
  9. an unknown code: We cannot find that here today. Please see reception.
  ```

  - **Step 3** is the issue's fifteen seconds: the scan was typed into the field, Enter pressed, and the
    number was on the screen 0.14 s later. A patient holding their phone up to a scanner adds a second or
    two, not fifteen.
  - **Step 5** waited out the real 20-second timeout and searched the whole page for the number: gone.
  - **Step 6** switched the browser context offline, as unplugging the network does.
  - **Step 7 and 8**: the manager turned walk-ins on through the API the settings page uses, and the next
    patient took `T002` by pressing *Triage*.
- [x] **Over HTTP, on a paired device** (`test_kiosk_checkin.py`, 15 passed, with the tablet's clock fixed at
  09:00 so every booking is a fixed distance away):
  - **A booked patient scans their QR:** one ticket naming the booking, waiting, arrived, in Triage, and one
    `QueueChanged` for the board.
  - **A second scan:** the same number, `already: true`, and still one ticket.
  - **A ticket already held:** stamped arrived, still waiting, and the board told.
  - **A phone number:** the same place, found by the number the patient joined with.
  - **Nothing personal:** the patient's name appears nowhere in the answer, whose keys are exactly the nine
    the screen draws, and the answer is `no-store`.
  - **Refusals:** another clinic's booking and an unknown code give the identical 404 body; an unpaired
    device and a **board** device are both 401; nonsense is 422 `not_a_code`.
  - **Too early:** a booking five hours away is 409 `too_early` and no ticket exists.
  - **Cancelled:** a released booking is 409 `ended`, "Please see reception."
  - **Walk-ins:** 409 `not_offered` until the clinic switches them on; then a walk-in ticket, arrived, with
    the phone number kept and one `notifications` consent recorded.
  - **Each device to its own screen:** a check-in tablet opening `/display` lands on `/display/check-in`, a
    board on its board, a board opening the check-in page is sent back, and a stranger sees neither.
  - **The queue buttons** appear only when the clinic's switch is on.
  - **The state route** answers a paired tablet and 401s anyone else.
  - **Nothing to browse away to:** the rendered page has no `<a>`, no `<form>` and no `target=`.

| The idle screen (walk-ins on) | Checked in, with the number | The board a moment later | Offline |
|---|---|---|---|
| ![Check in at Hillbrow Community Health Centre, with Type my phone number and the open queues](https://github.com/Billykat7/clinicQ/blob/0fb79e727439509219745ae5968ef2808340f850/docs/GITHUB/PR/M11/assets/pr83/kiosk-idle-800.png?raw=true) | ![C004, Chronic medication collection, 3 people are ahead of you](https://github.com/Billykat7/clinicQ/blob/0fb79e727439509219745ae5968ef2808340f850/docs/GITHUB/PR/M11/assets/pr83/kiosk-checked-in-800.png?raw=true) | ![The waiting-room board showing the checked-in number among the queues](https://github.com/Billykat7/clinicQ/blob/0fb79e727439509219745ae5968ef2808340f850/docs/GITHUB/PR/M11/assets/pr83/kiosk-board-1280.png?raw=true) | ![The idle screen with a banner: This screen is offline. Please see reception.](https://github.com/Billykat7/clinicQ/blob/0fb79e727439509219745ae5968ef2808340f850/docs/GITHUB/PR/M11/assets/pr83/kiosk-offline-800.png?raw=true) |

## Acceptance criteria

- [x] **A booked patient checks in by scanning their QR in under 15 seconds:** 0.14 s from the scan to the
  number on the demo server, and the whole flow needs one movement.
- [x] **Checking in moves the patient to waiting and updates the board:** the booking becomes a waiting ticket
  and the board showed the same number without being reloaded. A patient who was already waiting keeps that
  place; nobody is moved.
- [x] **The kiosk returns to its idle screen after a short timeout, leaving no personal data on screen:** 20
  seconds, asserted on the server and in the test suite; answers are `no-store` and nothing is stored in the
  browser.
- [x] **The screen is usable by someone with limited dexterity and no reading glasses:** every target at least
  88 px, nothing below 24 px, the number at 5 rem, digits-only typing, and no timed interaction other than the
  clearing.
- [x] **The kiosk cannot be used to browse away from the check-in app:** no link, no form and no `target` in
  the page (tested), and the box runs Chromium in `--kiosk` from the setup guide's service.
- [x] **An offline kiosk shows a clear "please see reception" message rather than failing silently:** the
  banner appears within 15 seconds of losing the network and clears when it comes back. A tablet booted with
  no network at all shows the browser's error page, which is written down rather than papered over.

## Risk and rollback

- **Every existing paired device is a board** (`kind` defaults to `board`), and the board's own routes are
  untouched.
- **`convert_one` is a refactor of the sweep's body**; the appointments and booking suites pass unchanged.
- **The kiosk router is mounted before the display router**, so `/display/check-in` is not read as a clinic
  id. A clinic id can never be `check-in`.
- **Rollback:** revert, then `alembic downgrade 0043`. Device kinds, arrival times and the walk-in switch are
  dropped; every device then shows the board again.

Closes #83
