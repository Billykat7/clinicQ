# PR: A walk-in is a name and the Enter key, with a stub for a 58 mm printer (Issue 51 / M7-51)

**Milestone:** [Milestone 7: Clinic Dashboard](https://github.com/Billykat7/clinicQ/milestone/7) ·
**Issue:** [#51](https://github.com/Billykat7/clinicQ/issues/51) · **Builds on:** #40 (the join
service), #44 (cancellation), #48 (the shell, PR #170), #49 (the cards, PR #173) and #50 (request keys,
PR #175), all merged

Reception has a **Walk-in** screen (`g w` from anywhere in the clinic). On open, the name field has
focus and the queue this desk used last is already chosen, so a walk-in is a name and **Enter**.

The keyboard-only times, first key press to the number on screen, typing at 8 keys a second:

| Flow | Seconds |
|---|---|
| Initials, then Enter | **0.42** |
| A full name, then Enter | **1.93** |
| A name, then another queue | **0.98** |
| A name, a phone number and the patient's yes to messages | **2.53** |

All four are well under the 10-second target, and the mouse was never used.

The ticket goes through Issue 40's join with `source=walk_in`, so it takes the next number in the same
sequence as a phone join. The number appears in large type to show the patient. **Print stub**
(Alt+P) prints it on a 58 mm thermal roll. The desk can **undo** its last walk-in (Alt+U) while the
patient is still at the counter, and the undo is audited.

## Summary

- **The intake page** (`src/web/dashboard/walkin.py`, `dashboard/walkin.html`, new):
  - A name or initials (required, staff only, never on the public board).
  - The queue, as one radio group moved with the arrow keys. It shows the open queues with how many
    are waiting in each, and preselects the queue last used at this desk (remembered per clinic in
    the browser).
  - An optional phone number, the patient's answer to being messaged, and an optional private reason.
  - `dashboard-walkin.js` sends the form to `POST /api/v1/sites/{site}/queues/{queue}/tickets` with an
    `Idempotency-Key`.
  - After a ticket is issued, the form clears except for the queue, and focus returns to the name
    field for the next patient.
- **A nav destination** `walk_in`, "Walk-in", shortcut `w`, gated on `queues.tickets` `update` at
  `assigned`, the grant the walk-in API checks. The front desk and the manager see it; a clinician and
  a read-only role do not. It has its own rail icon.
- **One press, one ticket.** The walk-in route now takes the `Idempotency-Key` header through Issue
  50's `run_once()`. A double Enter, or a retry after a lost answer, answers `200` with the ticket
  already issued. The form also ignores Enter while a request is in flight. A request whose answer
  never came keeps its key for that same form, so pressing Enter again cannot issue a second ticket.
- **A phone number enables later notifications; nothing is sent here.**
  - With a phone, the walk-in is that patient's ticket, as before.
  - The new `notifications_consent` field records the patient's answer, asked at the desk, as their
    `notifications` consent: channel `walk_in`, the staff member as `recorded_by`, the clinic as
    `site_id`. It is in the same transaction as the ticket.
  - The desk reads out the consent wording shown under the box (Issue 21's text). The box is switched
    on only once a number is typed, and the API refuses a yes without a number (`422`).
  - No notification is created; delivery is M9's.
- **Undo the last walk-in** (`src/modules/queue/walk_ins.py`, new; `POST
  /api/v1/sites/{site}/tickets/{ticket}/undo-walk-in`):
  - `recent_walk_ins()` reads the walk-ins **this person** issued today, newest first. Who issued a
    ticket is taken from the join's own audit row, where that fact already lives, not stored a second
    time.
  - `undo_walk_in()` takes back only the newest, only while it waits, and only within
    `QUEUE_WALK_IN_UNDO_SECONDS` (default 120, 10–600).
  - Otherwise it refuses with `409`: `ticket.walk_in_undo.not_latest`,
    `ticket.walk_in_undo.window_closed`, or `ticket.transition.stale`.
  - The ticket is cancelled through the lifecycle with the reason `joined_by_mistake`. The audit row
    reads `… cancelled by staff via walk_in, reason joined_by_mistake, undo walk-in`, and the number is
    never reused. (`cancel_ticket()` gains an optional `why` for those last words.)
- **The recent list** (`dashboard/_walkin_recent.html`, new, with a `/walk-in/recent` fragment):
  - It shows each walk-in's number, name, status, whether a phone was taken, the time issued (in
    Johannesburg time) and a **Stub** link.
  - **Undo** appears on the newest with the server's deadline, counted down for display.
  - The page fetches the list again after every issue and undo, so what can be undone is always the
    server's answer.
- **The ticket stub** (`print/ticket_stub.html`, `ticket-stub.css`, `ticket-stub.js`, new;
  `/walk-in/tickets/{ticket}/stub`):
  - A plain page on the board layout. It shows the clinic, the queue and room, the number, where the
    patient stands with the wait range, the reference code and the time issued.
  - **On paper**, `@page { size: 58mm auto }`: black on white, 48 mm printable width, and the page ends
    where the content does, so a thermal printer cuts there.
  - **On screen**, the same page is the large number to turn towards the patient when no printer is
    attached.
  - Opened with `?print=1` (as Alt+P does), it asks the browser to print at once.
  - It carries **no name, phone number or reason**. It is gated like the intake page. Another clinic's
    ticket, or one no longer in the day, is not found.
- **Contract:** `contracts/queue.yaml` documents the undo route and its three codes, the key and
  `notifications_consent` on the walk-in route, and its `409` (join refusals or
  `ticket.request.key_reused`).
- **Docs:** the dashboard product doc's walk-in paragraph, the M7 Status row, the README Status block,
  sprint 7's row and the generated bars. `.env.example` and the RBAC snapshot are regenerated.

## Design notes

**No walk-in path.** The page adds no rule to issuing a ticket. It calls the join route that already
existed, so a walk-in is refused exactly when a join would be (a closed clinic or queue, a full queue)
and numbered in the same sequence. The test proves the sequence directly: a phone join, a walk-in and a
phone join are T001, T002 and T003.

**Fast because nothing has to be chosen.** Every design choice on the form comes down to "how many
keys". The name field has focus on load. The queue is one tab stop with the last one used already
chosen. Enter submits from any field, including the queue choice and the consent box. Nothing optional
sits between the name and the key that issues. The four timed flows above were typed through Playwright
at 125 ms a key, a realistic pace for someone talking to a patient, with no mouse event.

**Who issued it is read from the audit trail.** "Undo *your* last walk-in" needs to know who issued
each ticket. The join has always written a `create` audit row with the actor's id, so the recent list
reads from there. The alternative, an `issued_by` column, would store the same fact twice and need a
migration. The read goes through the site guard (`scoped_select` on both the audit rows and the
tickets).

**Undo is a cancellation, never a deletion.** Deleting the ticket would leave a gap in the day's numbers
that the property tests (Issue 47) forbid, and would erase what happened. Cancelling it with a reason
keeps the sequence whole and the trail true. It is limited to the newest walk-in, while it waits and
within two minutes, which is when a mistake is noticed with the patient still at the counter. Anything
older is an ordinary cancellation with its own route.

**The stub prints only what a stranger may read.** A stub is left on chairs and in bins, so it carries
the number, the queue and the reference code, never a name, number or reason. The test searches the
rendered stub for the name and phone it was issued with.

**A zero that is not an O.** The display face draws `0` and `O` alike, so "A034" read "AO34" on the
first render of the stub. The number and the reference code now use the UI face, whose zero is narrow,
on screen and on paper.

**Deviations from the issue:**

- "Capturing a phone number triggers notifications for that ticket" is met as far as this issue can go.
  The number links the patient, and their consent to messages is recorded at the desk, which is what
  M9's notification service will check. No message is sent here, as the issue's out-of-scope note and
  the milestone plan say.
- "Optional SMS of the ticket number" is likewise left to M9.
- No 58 mm printer was attached. The print rendering is shown from Chromium's print emulation and a PDF
  at 58 mm, and the on-screen number is the stated fallback.

## Changes

- **New:**
  - `src/web/dashboard/walkin.py`, `src/modules/queue/walk_ins.py`;
  - `src/templates/dashboard/walkin.html`, `src/templates/dashboard/_walkin_recent.html`,
    `src/templates/print/ticket_stub.html`;
  - `src/static/js/dashboard-walkin.js`, `src/static/js/ticket-stub.js`,
    `src/static/css/ticket-stub.css`;
  - `tests/integration/dashboard/test_walk_in.py`.
- **`src/modules/queue/router.py`:** the walk-in route runs once per key and records the consent; the
  undo-walk-in route. **`src/modules/queue/schemas.py`:** `WalkInIn.notifications_consent`.
  **`src/modules/queue/service.py`:** `describe_ticket()`. **`src/modules/queue/cancellation.py`:**
  `why`.
- **`src/core/nav_registry.py`** (the `walk_in` destination), **`src/main.py`** (its router),
  **`src/templates/partials/clinic_icons.html`**, **`src/static/css/dashboard.css`**.
- **`src/core/config.py`**, **`.env.example`:** `QUEUE_WALK_IN_UNDO_SECONDS`.
- **`contracts/queue.yaml`**, **`tests/snapshots/rbac_decisions.txt`**.
- **Tests updated:** `tests/unit/security/test_nav_registry.py` and
  `tests/integration/dashboard/test_dashboard_shell.py` (the new destination in each menu).
- **Docs:** `docs/PRODUCT/05-clinic-dashboard.md`, `docs/GITHUB/MILESTONES/M7_clinic_dashboard.md`,
  `README.md`, `docs/GITHUB/README.md`, `docs/TEAM/WORKLOAD_SPLIT.md`.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` clean (262 files)
- [x] Whole suite as CI runs it, in UTC with PostgreSQL and Redis:

  ```text
  $ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest tests/ -q --no-cov -n auto --dist loadscope
  1947 passed, 1 skipped, 9 xfailed, 56 warnings in 216.30s
  ```

- [x] The new tests:

  ```text
  tests/integration/dashboard/test_walk_in.py::test_a_walk_in_takes_the_next_number_in_the_sequence_phone_joins_use
  tests/integration/dashboard/test_walk_in.py::test_a_double_enter_issues_one_walk_in
  tests/integration/dashboard/test_walk_in.py::test_a_phone_number_links_the_patient_and_records_their_answer_to_messages
  tests/integration/dashboard/test_walk_in.py::test_only_the_desks_last_walk_in_can_be_undone_within_the_window_and_it_is_audited
  tests/integration/dashboard/test_walk_in.py::test_an_undo_after_the_window_or_once_called_is_refused_and_changes_nothing
  tests/integration/dashboard/test_walk_in.py::test_the_intake_page_offers_the_open_queues_and_the_callers_own_recent_walk_ins
  tests/integration/dashboard/test_walk_in.py::test_the_stub_shows_the_number_and_where_the_patient_stands_and_never_who_they_are
  7 passed
  ```

- [x] **In a browser, keyboard only** (Playwright's Chromium at 1366×768 against a local PostgreSQL
  database; no mouse event is sent at any point):

  ```text
  From the front desk: g, w  -> the Walk-in screen, focus in "Name or initials"
  Typing at 125 ms a key; time from the first key press to the number on screen:
    "TM", Enter                                               0.42 s   T029   1 request   focus back in the name
    "Thandi Mokoena", Enter                                   1.93 s   T030   1 request   focus back in the name
    "Sipho N", Tab, ArrowDown, Enter                          0.98 s   A031   1 request   focus back in the name
    "Lindiwe K", Tab, Tab, "0825550151", Tab, Space, Enter    2.53 s   A032   1 request   focus back in the name
  The queue remembered for the next walk-in: General consultation
  Double Enter: 1 request, 1 ticket
  The phone walk-in's consent: "notifications true via walk_in"
  Alt+U on "Undo 1:58": "A033 was undone and taken out of the line."
    A033 cancelled, joined_by_mistake; audit "A033: waiting → cancelled (cancelled by staff via walk_in,
    reason joined_by_mistake, undo walk-in)"; no undo left in the list; focus back in the name
  Alt+P after the next walk-in: the stub opens with ?print=1:
    "Hillbrow Community Health Centre · General consultation · YOUR NUMBER A034 · 32 people ahead of you
     ~235–545 min (approximate) · Reference ZYK-2Y5 · Issued 14 Sep 2026, 17:25 · Keep this ticket…"
  Print emulation at 58 mm (219 CSS px): the stub fits the width; a 58 mm PDF is one page
  Page errors: none
  ```

### Screenshots

The walk-in screen at 1366×768 after a walk-in: the number to show the patient, and the desk's recent
walk-ins with the undo countdown:

![Walk-in issued](https://github.com/Billykat7/clinicQ/blob/0b70c69caeaa104f4839d62f620141ae433221b0/docs/GITHUB/PR/M7/assets/pr51/walkin-issued.png?raw=true)

After Alt+U:

![Walk-in undone](https://github.com/Billykat7/clinicQ/blob/0b70c69caeaa104f4839d62f620141ae433221b0/docs/GITHUB/PR/M7/assets/pr51/walkin-undone.png?raw=true)

The stub on a 58 mm roll (print emulation), and the same page on screen when no printer is attached:

![Stub printed at 58 mm](https://github.com/Billykat7/clinicQ/blob/0b70c69caeaa104f4839d62f620141ae433221b0/docs/GITHUB/PR/M7/assets/pr51/stub-print-58mm.png?raw=true)

![Stub on screen](https://github.com/Billykat7/clinicQ/blob/0b70c69caeaa104f4839d62f620141ae433221b0/docs/GITHUB/PR/M7/assets/pr51/stub-on-screen.png?raw=true)

## Acceptance criteria

- [x] A walk-in ticket is issued in under 10 seconds of interaction: 0.42–2.53 s in four keyboard-only
      flows (browser)
- [x] The walk-in takes the next number in the shared sequence, not a separate one: T001 (phone),
      T002 (walk-in), T003 (phone) (test)
- [ ] The stub prints correctly on a 58 mm thermal printer, and degrades to an on-screen number if none
      is attached. **Partly met.** The on-screen number is shown. The 58 mm layout is shown in print
      emulation and a one-page 58 mm PDF. No physical 58 mm printer was available to print on.
- [ ] Capturing a phone number triggers notifications for that ticket. **Met for this issue's scope.**
      The number links the patient, and their consent to messages is recorded at the desk (test).
      Sending is M9's, and nothing is sent here (test).
- [x] Undo is available for the most recent ticket and is audited: only the newest of the person's
      walk-ins, while waiting and within two minutes, cancelled with an audit row naming the undo; the
      number is never reused (tests, browser)
- [x] The form is fully keyboard-operable with no mouse: navigation, issuing, the queue, phone and
      consent, printing and undo all by keyboard (browser)

## Risk and rollback

No migration. The walk-in route keeps its behaviour for callers that send neither the key nor
`notifications_consent`. The new route only cancels a ticket the caller issued minutes ago, through the
lifecycle. The stub page reads a ticket through the site guard and prints nothing that names the patient.

Rollback is a revert of this PR.

**Known limits:**

- Not printed on a physical 58 mm thermal printer (see above). Thermal printers differ in printable
  width; the stylesheet assumes about 48 mm.
- The recent list is per person. Two receptionists sharing one browser share one sign-in, and so one
  list.
- The consent wording is still marked draft for the privacy review in M13.

Closes #51
