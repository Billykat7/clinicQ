# PR: The system always knows whether a clinic is open, and tells people when it shuts (Issue 24 / M4-24)

**Milestone:** [Milestone 4: Clinics, Queues & Configuration](https://github.com/Billykat7/clinicQ/milestone/4) ·
**Issue:** [#24](https://github.com/Billykat7/clinicQ/issues/24) · **Builds on:** #23 (PR #138)

> **Merge order:** after #23. This branch is stacked on it, so the Conventions check fails on
> #23's commits until that one merges. Every test job passes.

A clinic that shows as open when its doors are locked sends a patient on a trip for nothing. This
brings the weekly schedule, the country's public-holiday calendar, an ad-hoc closure with a reason,
and **one** answer to "is it open" that discovery, the board and all four channels read.

## Summary

- **Four tables (migration `0008`)**, in the order of precedence they resolve in: `site_opening_hours`
  (one row per *span*, so a lunch break and a split shift are the same shape), `public_holiday`
  (the country's, deliberately **not** site-scoped), `site_holiday_rule` (one clinic's answer for
  one holiday) and `site_closure` (a reason, a window, who said so, whether it was lifted).
- **`is_open_now()` and `next_open_at()` are pure functions of their inputs** — an `OpeningSchedule`
  and a moment, no session and no clock. 39 unit tests pin them, including the one the issue asks
  for by name: **a span that crosses midnight in `Africa/Johannesburg`**, checked from both sides
  and from a caller in UTC.
- **Public holidays are computed, not typed in.** `scripts/db/holidays.py` derives Easter, the
  twelve proclaimed days, and section 2(1) of the Public Holidays Act 36 of 1994 — a holiday on a
  **Sunday** earns the Monday after it. Seeded for this year and next.
- **A closure raises an event and sends nothing.** `SiteClosureAnnounced` goes onto a new in-process
  bus (`src/core/domain_events.py`) *after the commit*; who holds a ticket is the queue's question
  (Issue 39) and reaching them is the notification service's (Issue 63).
- **A closure stops new joins on every channel at once**, because
  `src/modules/sites/availability.py:join_gate()` takes the clinic and the moment and **has no
  parameter for the channel**. There is nowhere for a per-channel exception to live.
- **`GET /sites/{id}/open`** returns the hours answer and the join decision in one payload, so a
  screen can never show "open" from one call and "not taking patients" from another.

## Design notes

**Why a span per row rather than `opens`/`closes` on a day.** A clinic that shuts for lunch has two
spans; one that runs a 22:00–02:00 after-hours service has one span that crosses midnight. Modelled
as a day with two columns, the first needs a second pair of columns and the second needs a flag, and
every reader has to know about both. Modelled as spans, a weekday with no rows is simply closed,
`closes_at <= opens_at` is the whole midnight case, and `TimeSpan.on(day)` is the only place it is
handled.

**Midnight is where this kind of code goes wrong, so it is where the tests are.** A schedule read a
day at a time answers "closed" at 00:30 on Tuesday, because Tuesday has no spans of its own and the
one that matters started on Monday. `_open_intervals` therefore starts a day early, and
`test_midnight_is_johannesburg_midnight_and_not_the_servers` asserts the same instant expressed in
UTC gives the same answer — a comparison that took the server's clock zone would be right by
accident on a laptop and wrong in the container.

**`is_open_now` and `next_open_at` cannot disagree.** `next_open_at` returns the earliest instant
**at or after** the moment asked about, which makes `next_open_at(s, m) == m` exactly equivalent to
`is_open_now(s, m)`. That is asserted at every hour of a real day, so a screen showing "closed" next
to "opens at 09:00" when it is 09:30 is not a bug that can be written.

**The public-holiday calendar is the country's, not a clinic's.** `public_holiday` has no `site_id`:
16 June is 16 June for everyone, and a per-clinic copy would let two clinics disagree about the
calendar. Setting a rule for a date that is not a public holiday is a **404** with the reason — a
clinic closing on an ordinary Tuesday is an ad-hoc closure, and conflating the two would let one
clinic invent holidays for the whole country. A holiday with **no** rule means closed, which is the
safe default, and the payload says `has_rule: false` so a screen can show that it is a default
rather than a decision.

**Why an event bus at all, and why it publishes after the commit.** Closing a clinic is the sites
module's business; telling twenty waiting patients is the notification service's. Wiring one
directly to the other makes a clinic unable to close while the SMS gateway is down. The bus has
three properties and each has a test: a failing subscriber is logged and the next one still runs
(the event describes something that *already happened*), nothing is published until the transaction
commits (a subscriber that reads the database must find the row), and a rollback publishes nothing.

The first version attached a **self-removing listener per event**, and removing one from inside the
`after_commit` dispatch raised `RuntimeError: deque mutated during iteration` — a real 500, caught
by the integration tests, on any request publishing two events. The queue now lives on
`Session.info` with two permanent listeners.
`test_two_events_in_one_transaction_both_arrive` is the regression test.

**"On every channel at once" is a property of the shape, not of four careful implementations.**
`join_gate(site, schedule, moment)` has no channel parameter, so web, USSD, WhatsApp and walk-in
cannot get different answers. The test asserts both halves: the gate refuses after a closure, and
its signature is exactly those three parameters — and it names all four `TicketSource` members so
that a fifth channel brings this test with it.

**A suspended clinic is refused before its hours are considered**, and with the *same* sentence a
never-verified one gets. Which of the two it is, is the clinic's business and the platform's, not a
stranger's. Issue 29 owns the transitions.

**Out of scope:** discovery's rendering of "closed, opens 07:00" (Issue 32 renders what
`GET /open` returns); notification delivery itself (Issue 63), which subscribes to the event this
raises; and the join routes (Issue 40), which call the gate this adds.

## Changes

- **`alembic/versions/0008_site_hours.py`** (new), **`src/database/models/site_hours.py`** (new):
  `SiteOpeningHours`, `PublicHoliday`, `SiteHolidayRule`, `SiteClosure`.
- **`src/modules/sites/hours.py`** (new): the pure half (`TimeSpan`, `ClosedPeriod`,
  `OpeningSchedule`, `open_periods`, `is_open_now`, `next_open_at`, `open_state`) and the loader
  (`schedule_for`), separated by a section comment.
  **`hours_service.py`** (new): every write, plus `seed_public_holidays`.
  **`availability.py`** (new): `join_gate`, the one gate every channel asks.
- **`src/core/domain_events.py`** (new): `DomainEvent`, `subscribe`, `publish`,
  `publish_after_commit`, and the two site-closure events.
- **`src/modules/sites/router.py`:** seven routes — `/open`, `GET`/`PUT` `/hours`, `GET`
  `/holidays`, `PUT` `/holidays/{date}`, `GET`/`POST` `/closures`, `DELETE` `/closures/{id}`.
  All behind the site guard; reading is the receptionist's grant, changing is the manager's.
  **`schemas.py`:** eleven models, with the overlap and "both or neither" rules on them.
- **`scripts/db/holidays.py`** (new): `easter_sunday`, `holidays_for`, `holidays_between`.
  **`scripts/db/seed_dev_data.py`:** `seed_opening_hours` and `seed_holidays`; `seed()` now returns
  a report per step and `main()` prints them in order.
- **`tests/`:** `unit/sites/test_opening_hours.py` (23 cases), `test_public_holidays.py` (23),
  `test_domain_events.py` (5), `integration/sites/test_opening_hours_api.py` (20). Three new
  cross-tenant cases (`siteopeninghours`, `siteholidayrule`, `siteclosure`) and the M4 fixture's
  clinics are now `verified`, with the reason written next to it.

## Testing

- [x] `ruff check` / `ruff format --check` clean; `mypy src/` clean (197 files).
- [x] `make test`: **1319 passed**, 27 skipped, 9 xfailed.
- [x] `make test-postgres`: **25 passed**, including migration `0008` down and up and `alembic
      check` finding no drift.
- [x] **Easter is checked against published dates**, not restated: 2024-03-31, 2025-04-20,
      2026-04-05, 2027-03-28 and 2030-04-21, so an arithmetic slip in the Gregorian algorithm
      cannot pass.
- [x] **How to verify, end to end on a real server.** A throwaway database on PostgreSQL 18 +
      PostGIS 3.6, migrated and seeded, then driven through the real app. Transcript, verbatim
      (run on Saturday 12 September, which is why the clinic starts closed — the seeded week is
      weekdays only, and `next_open_at` names Monday):

```text
  python -m scripts.db.seed_dev_data
    Clinics: 11 created ·  Opening hours: 11 created
    Public holidays: 27 created ·  Staff accounts: 4 created

  weekly hours seeded: 0-4, 07:00-19:00        ← the dataset's own times, weekdays only
  Sunday rule applied: 2026-08-10 (Mon) National Women's Day (observed) <- National Women's Day
                       2027-03-22 (Mon) Human Rights Day (observed)     <- Human Rights Day
                       2027-12-27 (Mon) Day of Goodwill (observed)      <- Day of Goodwill

  GET  /open before anything     -> {"is_open": false, "next_open_at": "2026-09-14T07:00:00+02:00",
                                     "accepting_joins": false,
                                     "refusal": "Hillbrow Community Health Centre is closed at the moment."}
  PUT  /hours (manager)          -> 200   days returned: 7
  PUT  /hours (receptionist)     -> 403
  PUT  /hours overlapping spans  -> 422   The spans 07:00:00-13:00:00 and 12:00:00-16:00:00 overlap.

  GET  /holidays                 -> 14 in the window; first: 2026-09-24 Heritage Day
                                    has_rule false, is_open false      ← closed by the safe default
  PUT  /holidays/2026-09-24      -> 200   {"is_open": true, "opens_at": "08:00:00",
                                           "closes_at": "11:00:00", "has_rule": true}
  PUT  half a rule               -> 422   Give both opens_at and closes_at to open on this holiday…
  PUT  an ordinary Tuesday       -> 404   2026-09-15 is not a public holiday.

  POST /closures (manager)       -> 201   {"reason": "The water is off.", "ends_at": null}
       event published           -> SiteClosureAnnounced | reason: The water is off.
                                    | by: manager@clinicq.example        ← and nothing was sent
  GET  /open after the closure   -> {"accepting_joins": false, "closure_reason": "The water is off."}
       refusal                   -> Hillbrow Community Health Centre is closed: The water is off.
  POST /closures (receptionist)  -> 403
  DELETE /closures/{id}          -> 200   lifted_at set, the row kept
  DELETE the same one again      -> 404

  another clinic:  GET /sites/<B>/{hours,holidays,closures,open}  -> 404, 404, 404, 404

  audit trail:
    update | manager@clinicq.example | site_id set | set opening hours for 5 weekday(s)
    update | manager@clinicq.example | site_id set | 2026-09-24: open (Heritage Day)
    create | manager@clinicq.example | site_id set | closed the clinic: The water is off.
    update | manager@clinicq.example | site_id set | lifted the closure early
```

- [ ] Screenshot: no UI in this PR. The manager's hours screen is Issue 54; discovery's
      "closed, opens 07:00" is Issue 32.

## Acceptance criteria

- [x] **`is_open_now()` respects weekly hours, holidays and ad-hoc closures in that order of
      precedence.** All three levels in one schedule, checked at four moments in
      `test_an_adhoc_closure_beats_a_holiday_rule_which_beats_the_weekly_schedule`.
- [x] **A closure immediately stops new joins for that site across all four channels.** One gate
      with no channel parameter; the test asserts the refusal and the signature, and names every
      `TicketSource` so a fifth channel brings it along.
- [x] **Patients holding a ticket at a closing site are notified with the reason.** *Partly (Issue
      24).* The closure **raises the event carrying the reason**, asserted published after the
      commit and shown in the transcript. The delivery half belongs to the notification service
      (Issue 63), which is where the subscriber will be registered; nothing is sent from here on
      purpose, so a clinic can close while the SMS gateway is down.
- [x] **Discovery shows 'closed, opens 07:00 tomorrow' rather than hiding the clinic entirely.**
      `GET /open` returns `is_open`, `next_open_at` and the closure's own reason together; Issue 32
      renders them. Shown in the transcript, naming Monday 07:00 from a Saturday.
- [x] **All time comparisons use `Africa/Johannesburg`, proven by a test crossing midnight.**
      Two tests: 22:00–02:00 covering 00:30 the next morning, and the same instant expressed in UTC
      giving the same answer.
- [x] **Holiday data is seeded for the current and next year.** 27 rows, computed rather than typed,
      with the Sunday rule visible in the transcript.

## Risk and rollback

**Migration `0008`** adds four tables and changes nothing existing, so the previous release runs
unchanged on the new schema and the migration is reversible (proven by the round-trip test). One
behaviour change: `make seed-dev-data` now also writes opening hours and holidays, and prints a line
per step rather than two fixed lines — the opening-hours step skips any clinic that already has
rows, so it cannot overwrite a manager's real decision.

The event bus is new and nothing subscribes to it yet, so publishing is currently a no-op with a
log line — deliberate, and the reason Issue 63 has something to attach to.

**Follow-ups noticed:** an open-ended closure has no reminder, so a clinic can leave itself shut
indefinitely with nothing nagging about it (worth a sweep in M13's job set); the holiday seed cannot
know about a one-off holiday a President proclaims, which a platform admin has to add by hand —
there is no screen for that yet, and it belongs with the admin console in Issue 29.

Closes #24
