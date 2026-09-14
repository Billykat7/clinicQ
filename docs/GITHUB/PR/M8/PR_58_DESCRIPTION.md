# PR: One server-side privacy projection that every waiting-room board response passes through (Issue 58 / M8-58)

**Milestone:** [Milestone 8: Waiting-room Display Monitor](https://github.com/Billykat7/clinicQ/milestone/8) ·
**Issue:** [#58](https://github.com/Billykat7/clinicQ/issues/58) · **Builds on:** #21 (consent and
`has_consent()`, M3) and #27 (display settings, M4), both merged · **Unblocks:** the board page (#56), its
stream (#57) and its announcements (#60), which all read this payload

This is non-negotiable 4, built before anything draws a board, so the board can never be built any other
way. A waiting-room screen now gets its data from **one function**,
`project_board()` in `src/modules/display/projection.py`, and nothing else may read a clinic's tickets
for a board:

- **Under `number_only`, a patient's name or reason is not in the payload.** It is not sent as `null`
  and not hidden by CSS. The `name` and `comment` keys do not exist anywhere in the response.
- **Consent is read when the board is rendered**, through `has_consent()`, so a withdrawal takes the
  name off the very next response.
- **A reason needs everything:** `full` mode, the clinic's "show the reason" switch, the patient's
  standing consent, **and** this visit's consent (`ticket.comment_consent`).
- **A template or a stream handed raw data refuses before anything renders**, and a payload with a key
  its mode forbids is refused before it is sent.
- **The proof** searches the JSON of every `/display` endpoint for a seeded patient's name and for the
  keys. Each endpoint is found from the application itself, so a new board endpoint cannot ship
  unsearched.

## Summary

- **The projection** (`src/modules/display/projection.py`, new):
  - `project_board(db, site, viewer=…)` makes two queries: the clinic's live queues, then today's
    waiting, called, called-again and in-progress tickets. It returns a `BoardState` of plain,
    frozen dataclasses: each queue's `now_serving` (the latest calls, newest first, at most 3), its
    `up_next` (the call order, at most 5) and its waiting count. No ORM object is inside.
  - Patients are read **only** when the mode could show a name, and only those on screen. Every shown
    ticket goes through `consent.board_projection()`, which asks `has_consent()` afresh.
  - `BoardState.payload()` is the JSON. A personal key is present only when it has a value. A queue's
    name is `label` on the wire, so `name` only ever belongs to a person.
- **Two runtime locks:**
  - `ensure_projected(context)` is an allowlist. The projection's dataclasses, text, numbers, times and
    enum members pass. A `Ticket`, a `Patient`, a `Site` or any other object raises
    `UnprojectedBoardDataError`, naming the key it was found under (`board['queues'][0]`).
  - `ensure_payload_private(payload, mode)` runs inside `payload()`. A `name` under `number_only`, or a
    `comment` outside `full`, raises `BoardPrivacyError` and nothing is sent.
- **Who is looking** (`src/modules/display/enums.py`, new): `BoardViewer.ANONYMOUS` gets
  `number_only` **whatever the site chose**. `BoardViewer.STAFF` (signed in, holding a role at this
  clinic) sees the site's own mode. Paired kiosks (#61) will join the second group.
- **The site guard** (`src/core/site_scope.py`): `displayed_select(model, site_id)` reads one clinic's
  rows, only while the clinic is publicly visible. It is the board's counterpart to `published_select`
  and, unlike it, reaches tickets, so only the projection module may call it.
- **Consent** (`src/modules/patients/consent.py`): `board_projection()` takes
  `visit_comment_consent`. A reason beside a full name now needs this visit's agreement as well as the
  standing one.
- **The route** (`src/web/display.py`, new): `GET /display/{site_id}/state` answers the payload with
  `Cache-Control: no-store`. A clinic that is unknown, draft or suspended gets the same 404 body.
  `board_template_response()` is the only way to render a `display/` template, and it checks the
  context first. #56's page renders through it.
- **Docs:** rule 4 in `docs/guideline.md` names its guard tests. `docs/PRODUCT/04-display-monitor.md`
  shows the wire shape per mode. The spec's owner note is settled (see *Design notes*).

## Design notes

**Removed, not hidden, and checked twice.** The projection leaves forbidden keys out, and
`payload()` then checks the serialised form against the mode. The second check exists for the day
somebody changes the first. The *Testing* section shows both locks failing when the code is
deliberately broken.

**An allowlist at the template door.** A blocklist of "dangerous types" fails open: the next model
somebody passes is not on it. The door lets through only the projection's own types and plain values,
so a query result, a SQLAlchemy row or a `SimpleNamespace` with a `name` on it is refused too.

**Why an anonymous board gets numbers only.** A clinic that chose `name_lite` agreed to show "Thabo M."
**in its waiting room**. A board address is a URL, and anyone who has it could otherwise watch names
from home. Until #61 pairs physical boxes with device tokens, only signed-in staff of that clinic see
the site's own mode. Every other viewer, including staff of another clinic, sees numbers. This takes
nothing from the default, because every clinic is `number_only` unless its manager changed it.

**What a reason needs, and a reading of the spec.** The documents word the comment rule three ways.
The spec says "only beside a ticket number, never beside a full name, and only with per-visit consent".
The milestone and the guideline say "never beside a full name *unless* per-visit consent". The
settings screen from #54 already tells managers "a reason, only beside a full name and with its own
agreement". This PR takes the strictest reading that satisfies all of them and keeps the promise #54
made:

- no reason under `number_only` (the acceptance criterion);
- no reason under `name_lite`;
- under `full`, a reason only beside a name, only with the clinic's switch, the standing
  `DISPLAY_COMMENT` consent, **and** `ticket.comment_consent` for this visit.

A reason beside a bare number is not offered. If the team wants it, it is a one-line change in
`board_projection()` plus a new question on the settings screen.

**Owner, settled.** The spec names A and the sprint plan puts this in D's lane. A owns the server rule
and its guard tests, because a guard-tested rule belongs with the backend lead. D builds the page, the
stream client and the announcements against the payload. Recorded in the spec's note.

**Out of scope:** the page (#56), the live stream (#57), styling (#56, #59), announcements (#60) and
device pairing (#61). The stream will call `ensure_projected` and `payload()` exactly as the JSON route
does, and #57 extends the endpoint sweep to it.

## Changes

- **New:**
  - `src/modules/display/__init__.py`, `enums.py`, `projection.py`;
  - `src/web/display.py`;
  - `tests/unit/display/test_board_privacy.py`;
  - `tests/integration/display/conftest.py`, `test_board_privacy_endpoints.py`.
- **`src/core/site_scope.py`:** `displayed_select()`.
- **`src/modules/patients/consent.py`:** `visit_comment_consent` on `board_projection()`.
- **`src/main.py`:** the display router.
- **Tests updated:**
  - `tests/unit/security/test_site_scoped_queries.py`: `displayed_select` is a scoped call;
  - `tests/integration/patients/test_consent.py`: standing consent alone no longer shows a reason.
- **Docs:** `docs/guideline.md`, `docs/PRODUCT/04-display-monitor.md`,
  `docs/GITHUB/ISSUES/M8/ISSUE_58_board_privacy_rendering.md`, and the progress:
  `docs/GITHUB/MILESTONES/M8_display_monitor.md`, `docs/GITHUB/README.md`, `README.md` and
  `docs/TEAM/WORKLOAD_SPLIT.md` (sprint 9).

No migration, no new setting, no template, stylesheet or script.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` clean (266 files)
- [x] Whole suite as CI runs it, in UTC with PostgreSQL and Redis:

  ```text
  $ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest tests/ -q --no-cov -n auto --dist loadscope
  1976 passed, 1 skipped, 9 xfailed in 210.92s
  ```

- [x] The new and changed tests:

  ```text
  tests/unit/display/test_board_privacy.py::test_a_board_template_handed_raw_data_refuses_before_rendering[ticket]
  tests/unit/display/test_board_privacy.py::test_a_board_template_handed_raw_data_refuses_before_rendering[patient]
  tests/unit/display/test_board_privacy.py::test_a_board_template_handed_raw_data_refuses_before_rendering[site]
  tests/unit/display/test_board_privacy.py::test_a_board_template_handed_raw_data_refuses_before_rendering[ticket-nested-in-a-list]
  tests/unit/display/test_board_privacy.py::test_a_board_template_handed_raw_data_refuses_before_rendering[any-other-object]
  tests/unit/display/test_board_privacy.py::test_the_error_names_where_the_raw_data_was_found
  tests/unit/display/test_board_privacy.py::test_projected_data_and_plain_values_pass_the_door
  tests/unit/display/test_board_privacy.py::test_a_number_only_payload_with_a_name_in_it_is_refused_not_sent
  tests/unit/display/test_board_privacy.py::test_a_name_lite_payload_with_a_reason_in_it_is_refused_not_sent
  tests/unit/display/test_board_privacy.py::test_a_number_only_payload_has_no_personal_key_at_all
  tests/unit/display/test_board_privacy.py::test_only_the_projection_reads_a_boards_tickets_or_builds_a_board_ticket
  tests/unit/display/test_board_privacy.py::test_a_display_template_is_rendered_only_through_the_guarded_renderer
  tests/unit/display/test_board_privacy.py::test_the_projection_asks_for_consent_on_every_shown_ticket
  tests/unit/display/test_board_privacy.py::test_the_source_guards_fail_on_the_shapes_they_exist_to_catch
  tests/integration/display/test_board_privacy_endpoints.py::test_under_number_only_no_board_endpoint_carries_a_name_or_a_comment_key_for_anyone
  tests/integration/display/test_board_privacy_endpoints.py::test_name_lite_shows_a_short_name_to_the_clinics_own_screen_and_numbers_to_everyone_else
  tests/integration/display/test_board_privacy_endpoints.py::test_withdrawing_display_consent_takes_the_name_off_the_next_update
  tests/integration/display/test_board_privacy_endpoints.py::test_a_reason_needs_full_mode_the_clinic_switch_and_both_consents
  tests/integration/display/test_board_privacy_endpoints.py::test_the_board_lists_the_newest_calls_first_and_the_waiting_line_in_call_order
  tests/integration/display/test_board_privacy_endpoints.py::test_a_clinic_with_no_public_board_answers_exactly_like_one_that_does_not_exist
  tests/integration/patients/test_consent.py::test_the_comment_needs_its_own_consent_and_the_display_mode
  21 passed in 7.98s
  ```

- [x] **The guards fail when the code is broken on purpose.** Three mutations, each reverted after the run:

  ```text
  1. board_template_response() without ensure_projected(context):
     FAILED …test_a_board_template_handed_raw_data_refuses_before_rendering[ticket]
     FAILED …[patient]  FAILED …[site]  FAILED …[ticket-nested-in-a-list]  FAILED …[any-other-object]
     5 failed, 9 passed

  2. effective_mode() ignoring who is looking (every viewer gets the site's mode):
     FAILED …test_name_lite_shows_a_short_name_to_the_clinics_own_screen_and_numbers_to_everyone_else
     AssertionError: anonymous  assert 'name_lite' == 'number_only'

  3. patients read, and names projected, under every mode (the payload lock is the only thing left):
     src.modules.display.projection.BoardPrivacyError: a number_only board payload carries ['name']; it was not sent
  ```

- [x] **On a real server, on PostgreSQL.** The seeded dev database has Hillbrow Community Health Centre
  with three named patients in Triage. Each agreed to show their name, their reason, and this visit's
  reason. The first was called. The display mode is changed through the manager's own API:

  ```text
  $ PUT /api/v1/sites/…/settings/display {display_mode: number_only, display_show_comment: false} -> 200
  anonymous  GET /display/…/state: HTTP 200, display_mode=number_only, bytes=1795
     personal keys anywhere: none
     names or reasons in the body: none
     Triage: now_serving=[{"number": "T001", "status": "called", "called_at": "2026-09-14T19:08:32.817793+02:00"}]
     Triage: up_next[:3]=[{"number": "T002", "status": "waiting"}, {"number": "T003", "status": "waiting"}, {"number": "T004", "status": "waiting"}]
  manager    GET /display/…/state: HTTP 200, display_mode=number_only, bytes=1795
     personal keys anywhere: none
     names or reasons in the body: none

  $ PUT /api/v1/sites/…/settings/display {display_mode: name_lite, display_show_comment: false} -> 200
  manager    GET /display/…/state: HTTP 200, display_mode=name_lite, bytes=1850
     personal keys anywhere: ['name']
     Triage: now_serving=[{"number": "T001", "status": "called", "called_at": "…", "name": "Nomvula Z."}]
     Triage: up_next[:3]=[{"number": "T002", "status": "waiting", "name": "Thabo M."}, {"number": "T003", "status": "waiting", "name": "Lerato K."}, {"number": "T004", "status": "waiting"}]
  anonymous  GET /display/…/state: HTTP 200, display_mode=number_only, bytes=1795
     personal keys anywhere: none
     names or reasons in the body: none

  $ PUT /api/v1/sites/…/settings/display {display_mode: full, display_show_comment: true} -> 200
  manager    GET /display/…/state: HTTP 200, display_mode=full, bytes=1968
     Triage: now_serving=[{"number": "T001", "status": "called", "called_at": "…", "name": "Nomvula Zwelithini-Qwabe", "comment": "Persistent migraine aura"}]
     Triage: up_next[:3]=[{"number": "T002", …, "name": "Thabo Mokoena", "comment": "Repeat prescription"}, {"number": "T003", …, "name": "Lerato Khumalo", "comment": "Rash on both arms"}, {"number": "T004", "status": "waiting"}]

  (Nomvula withdraws DISPLAY_NAME consent)
  manager    GET /display/…/state: HTTP 200, display_mode=full, bytes=1897
     Triage: now_serving=[{"number": "T001", "status": "called", "called_at": "…"}]
  ```

  T004 is a walk-in with no patient record: it is a number in every mode.

## Acceptance criteria

- [x] Under `number_only`, no board response contains a name or comment field at all: every `/display`
      endpoint, as anyone, as the clinic's manager and as another clinic's receptionist, searched for
      the name, the reason and both keys (test; real server)
- [x] A comment never renders alongside a full name without recorded per-visit consent: standing
      consent and the clinic's switch without `ticket.comment_consent` show no reason (tests)
- [x] Withdrawing consent removes the name from the board on the next update: the next response has no
      `name` key (test; real server). The live push of that update is #57's.
- [x] The projection is applied in one place that every board response passes through: `project_board()`.
      Only it may call `displayed_select` or build a `BoardTicket`, and a `display/` template renders
      only through the checked renderer (source guards, each with a failing fixture)
- [x] The guard test fails if a template is given raw ticket data: five shapes refused, and removing the
      check fails five tests (mutation 1)
- [x] The rule is documented as a project non-negotiable: rule 4 in `docs/guideline.md` now names its
      guards

## Risk and rollback

No migration and no setting. One behaviour changes for callers of `board_projection()`: a reason now
also needs `visit_comment_consent=True`. Nothing in the application showed a reason on a board before
this PR, so no screen loses one. The settings preview (#54) calls the pure rule with its own sample
answers and is unchanged.

The new route is public and read-only, uncached, and reveals nothing an anonymous caller could not see
in the waiting room: numbers only.

Rollback is a revert of this PR. #56 and #57 depend on it, so a revert after they merge would take the
board with it.

**Known limits:**

- Until #61, a clinic's own board cannot show its chosen mode on an unattended box, because the box is an
  anonymous viewer and gets numbers. Staff previews do show it.
- Each shown ticket asks `has_consent()` once or twice, so a `full` board of 4 queues and 8 tickets each
  makes up to 64 small indexed reads per render. `number_only`, the default, makes none.

Closes #58
