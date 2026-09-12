# PR: Every clinic starts by showing ticket numbers only, and changing that is deliberate (Issue 27 / M4-27)

**Milestone:** [Milestone 4: Clinics, Queues & Configuration](https://github.com/Billykat7/clinicQ/milestone/4) ·
**Issue:** [#27](https://github.com/Billykat7/clinicQ/issues/27) · **Builds on:** #21 (consent), #23 (PR #138), #24 (PR #139), #25 (PR #140)

> **Merge order:** after #23, #24 and #25. This branch is stacked on them, so the Conventions check
> fails on their commits until they merge. Every test job passes.

This is where **non-negotiable 4** starts. The waiting-room board is the sharpest privacy surface in
the product: a name next to a stated symptom, on a screen a room full of strangers can read. The
rule is that every new clinic is created `number_only` and **no code path may change that default**
— so the largest piece of this PR is the guard that makes breaking it fail the build.

## Summary

- **Five columns on `site` (migration `0010`):** `display_mode`, `display_show_comment`,
  `board_language`, `announce_audio`, `reason_retention_days`. Each carries its safe value as a
  **server default**, so a row inserted by a restore or a hand-written `INSERT` gets it too.
- **The guard test is the deliverable.** `tests/unit/sites/test_display_defaults.py` walks the
  source of `src/`, `scripts/` and `tests/factories.py` and fails on any code that constructs a
  `Site` naming `display_mode` or `display_show_comment` — wherever it is, including a path added
  tomorrow. Beside it, the four paths that exist today (API, factory, onboarding, seed) are
  exercised rather than read, and two fixtures prove the walk can fail.
- **Changing the mode takes a clinic-manager grant, an explicit confirmation and an audit row.**
  A change that newly puts a name or a reason on a public screen is refused **409** without
  `confirm_public_display` — so a client that renders no warning cannot make it.
- **A reason beside a *full* name takes a second, separate confirmation.** It is the sharpest
  combination the product can produce, and per-visit patient consent still gates the rendering
  itself (Issue 58, using Issue 21's `DISPLAY_COMMENT` purpose).
- **The warning says what will be on the screen, in plain language**, and the screen reads it from
  the server (`GET /sites/{id}/settings/display-options`) rather than writing its own.
- **The retention window is validated against a stated ceiling: 90 days.** Interim, with the
  reasoning written down (see below) so Issue 95 can argue with it rather than guess at it.
- **`BoardLanguage`**, the eleven written official languages, with ISO codes a `lang` attribute
  and a text-to-speech voice can use unchanged.

## Design notes

**The interim retention ceiling is 90 days, and here is the reasoning.** The issue asks for a
ceiling "validated against the policy ceiling the M13 data map will define (#95)" and for the
interim one to be stated. `reason_text` is what a patient typed about why they are at a clinic:
health information, in their own words. POPIA s 14 says personal information may not be kept longer
than is necessary for the purpose it was collected for; the purpose here is one visit, and the
longest operational use anyone has named for it is a quarter's worth of clinic-level review. Ninety
days covers that with nothing left over, and the default a clinic gets is 30. The constant lives in
`src/modules/sites/settings.py` with that paragraph next to it, and it is the one line Issue 95
changes.

**Why a source walk and not only behavioural tests.** A test of the API's creation path proves the
API. The failure this rule actually has is the **next** path somebody writes — an import script, a
bulk onboarding tool, a fixture — and no amount of testing today's paths catches that. So the guard
reads source, discovers every `Site(...)` construction, and treats naming the privacy fields as the
finding. It has one allow-list of three files (the enum that defines the default, the model that
reads the constant, the migration that installs it), each of which is a *definition* of the default
rather than a choice made at a call site.

**Why the confirmation is a field in the request, not a step in the screen.** The issue says
"requires an explicit confirmation". A confirmation dialog is a property of one client; a field the
server refuses the change without is a property of the product. The screen renders a warning and a
checkbox, and a `curl` gets exactly the same 409 with the warning in the message.

**Only an increase in exposure needs confirming.** Going back to `number_only`, or switching the
comment off, is always allowed: the safe direction never needs permission to travel in. That is why
`apply_display_settings` compares the *requested* warning with the *current* one rather than looking
only at what was asked for.

**409, not 403, for a missing confirmation.** The caller *may* do this — they hold the grant. What
is missing is their agreement, which is a conflict with the current state rather than an
authorization failure. A 403 would send an API client off looking for a permission problem.

**`sites.display` is its own resource, so a receptionist can see what the board is set to** without
being able to change it. The RBAC manifest already declared it (Issue 18); this PR adds routes under
it, not permissions, so there is no snapshot change.

**A bug the tests were green through, and the browser was not.** `GET /display-options` was first
written without a site in its path, which meant it needed a `business`-tier grant — and a clinic
manager's role is held **at a site**, so it resolves only on a route that names one. Every test
passed (they asked as the platform admin) and the page 403'd for the only role that opens it,
rendering "The settings could not be loaded." Driving it in a real browser is what found it. The
endpoint now lives under `/{site_id}/settings/display-options`, and
`test_the_options_endpoint_is_reachable_by_the_role_that_opens_the_page` asks as the manager *and*
the receptionist so it cannot come back.

**Out of scope:** the board's server-side projection and consent gating (Issues 56, 58), which is
where "a name is not in the payload at all" is enforced; and the purge job that uses the retention
window (Issue 95).

## Changes

- **`alembic/versions/0010_site_display_settings.py`** (new): five columns, each with its safe
  server default and a backfill that is that default.
- **`src/database/models/site.py`:** the five columns and their enum accessors.
- **`src/modules/sites/settings.py`** (new): the ceiling and its reasoning, the warning text,
  `warning_for()`, `validate_retention_days()` and `apply_display_settings()`.
- **`src/modules/sites/router.py`:** three routes (`GET`/`PUT` `/settings/display`, `GET`
  `/settings/display-options`), all behind the site guard. **`schemas.py`:** four models.
- **`src/commons/enums.py`:** `BoardLanguage` (+ `SITE_DEFAULT_BOARD_LANGUAGE`) and
  `NAME_REVEALING_DISPLAY_MODES`, so "a mode that reveals a name" is a set rather than two members
  named at a call site.
- **`src/templates/dashboard/settings_display.html`**, **`src/static/js/site-display-settings.js`**
  (both new) and the page route in **`src/web/routes.py`**, gated twice: a role **at this clinic**
  (another clinic's id renders not-found, never a 403) and then the `sites.display` grant.
  **`src/static/css/admin.css`:** `.radio-card`, `.input-suffix` and `.alert` / `.alert-warn`,
  the last three repeating `.field` to beat the existing radio rule rather than using `!important`.
- **`tests/`:** `unit/sites/test_display_defaults.py` (11 cases, the guard),
  `integration/sites/test_display_settings_api.py` (13 cases), and the `sites.display` cross-tenant
  case replacing the two `PENDING` entries that named this issue.

## Testing

- [x] `ruff check` / `ruff format --check` clean; `mypy src/` clean (202 files); the template
      punctuation check clean; `scripts/lint_surface_gates.py` clean.
- [x] `make test`: **1365 passed**, 27 skipped, 9 xfailed.
- [x] `make test-postgres`: **25 passed**, including migration `0010` down and up and `alembic
      check` finding no drift.
- [x] **The guard was tested with the mistake it exists to catch.** Two fixtures: a creation path
      that sets `display_mode=DisplayMode.FULL.value` is a finding naming the field and
      non-negotiable 4; the same path without it is not. Both privacy fields are covered.
- [x] **How to verify, end to end on a real server.** A throwaway database on PostgreSQL 18 +
      PostGIS 3.6, migrated and seeded, then driven through the real app. Transcript, verbatim:

```text
  every seeded clinic:  display_mode number_only: 11
  column default in the database: 'number_only'::character varying | nullable: NO

  GET  /settings/display         -> {"display_mode": "number_only", "display_show_comment": false,
                                     "reason_retention_days": 30,
                                     "reason_retention_ceiling_days": 90}
       warning shown             -> The board will show ticket numbers only, for example A014.
                                    No patient's name appears on the screen, and no name is
                                    sent to it.

  PUT  full, no confirmation     -> 409  This change puts patient information on a public screen.
                                         Confirm it explicitly: The board will show each patient's
                                         full name beside their number, for example A014 Thandi
                                         Mokoena. Anyone in the waiting room…
       and nothing moved         -> number_only
  PUT  full, confirmed           -> 200  full
  PUT  + comment, one confirm    -> 409  Showing a reason beside a full name means anyone reading
                                         the board learns why a named person is at the clinic.
                                         Confirm that this screen cannot be seen from a public area.
  PUT  + comment, both confirmed -> 200  show_comment: True
  PUT  back to number_only       -> 200  (no confirmation needed: the safe direction)
  PUT  retention 180 days        -> 422  Input should be less than or equal to 90
  PUT  retention 90 days         -> 200  90

  who may:
    receptionist GET             -> 200
    receptionist PUT             -> 403
    manager, another clinic GET  -> 404

  GET  /settings/display-options -> number_only(confirm=False), name_lite(confirm=True),
                                    full(confirm=True) | 1 to 90 days | 11 languages

  the page:
    GET /dashboard/sites/<A>/settings/display (manager) -> 200
    GET /dashboard/sites/<B>/settings/display (manager) -> 404

  audit trail:
    manager@clinicq.example | display settings: display_mode: number_only -> full
    manager@clinicq.example | display settings: display_show_comment: False -> True
    manager@clinicq.example | display settings: display_mode: full -> number_only;
                              display_show_comment: True -> False
    manager@clinicq.example | display settings: reason_retention_days: 30 -> 90
```

- [x] **Screenshot** — `/dashboard/sites/{site_id}/settings/display`, signed in as
      `manager@clinicq.example` against a migrated, seeded PostgreSQL database, with *Number
      and full name* **and** the comment checkbox selected: the amber warning describes the
      screen the clinic would actually get, and **both** confirmation checkboxes have appeared,
      the second carrying the full-name sentence. Neither is ticked, so this is exactly the
      state in which the server refuses the save with `409`. Files in
      `docs/GITHUB/PR/M4/assets/pr27/`.

| Light | Dark |
|---|---|
| ![The waiting-room screen settings, light](https://github.com/Billykat7/clinicQ/blob/7f3f15960d6e7a78c3ae3a0ae5ac01b7cc999892/docs/GITHUB/PR/M4/assets/pr27/settings-display-light.png?raw=true) | ![The waiting-room screen settings, dark](https://github.com/Billykat7/clinicQ/blob/7f3f15960d6e7a78c3ae3a0ae5ac01b7cc999892/docs/GITHUB/PR/M4/assets/pr27/settings-display-dark.png?raw=true) |

      That same browser run is what found the `display-options` gate bug described above.

## Acceptance criteria

- [x] **A newly created site always has `display_mode = number_only`.** All eleven seeded clinics,
      and the API, factory, onboarding and seed paths each asserted individually.
- [x] **Enabling `full` mode requires an explicit confirmation and is audited.** 409 without it,
      200 with it, and an audit row reading `display_mode: number_only -> full` with the manager
      and the clinic on it.
- [x] **`display_show_comment` cannot be enabled together with `full` name mode without per-visit
      patient consent.** Two halves, and this PR owns one of them: the standing decision takes a
      **second, separate** confirmation here, and the per-visit consent gate is Issue 58's, using
      Issue 21's `ConsentPurpose.DISPLAY_COMMENT`. Said plainly because half of this criterion is
      in another milestone's issue.
- [x] **The warning text states, in plain language, what will appear on a public screen.** Written
      as a description of the screen ("The board will show each patient's full name beside their
      number, for example A014 Thandi Mokoena"), served from the server, and asserted in both the
      unit and the integration tests.
- [x] **The retention window is validated against the policy ceiling in the data map.** *Partly
      (Issue 27).* Validated against **90 days**, the interim ceiling this PR chose, with the
      reasoning recorded next to the constant. The data map (Issue 95) sets the real one, and that
      is a one-line change here.
- [x] **A guard test fails if any code path sets a non-default display mode at creation.** The
      source walk, with its failure demonstrated.

## Risk and rollback

**Migration `0010`** adds five columns with defaults and drops nothing, so the previous release runs
unchanged on the new schema and the migration is reversible — a downgrade loses only the clinics'
own display choices. No behaviour changes for anything that existed before: nothing rendered a board
yet, and the defaults are what the demo data already assumed.

**Follow-ups noticed:** the settings page is reachable only by typing its URL, because the
role-aware navigation and site switcher are Issue 48 — a manager has no link to it until M7; and
`announce_audio` and `board_language` are stored but nothing reads them until Issues 56 and 60, so
they are currently settings with no observable effect (deliberate, and named here so a reviewer of
M8 knows where they came from).

Closes #27
