# PR: The clinic manager's settings screens: profile and map pin, hours, queues, services, staff, display (Issue 54 / M7-54)

**Milestone:** [Milestone 7: Clinic Dashboard](https://github.com/Billykat7/clinicQ/milestone/7) ·
**Issue:** [#54](https://github.com/Billykat7/clinicQ/issues/54) · **Builds on:** #22, #24, #25, #26,
#27 and #28 (the APIs these screens use) and #48 (the dashboard shell, PR #170), all merged

A clinic manager can now run their own clinic from **Clinic settings** without a developer. There is
one tab per job, each at its own URL:

| Tab | What the manager does | API it saves through |
|---|---|---|
| **Profile** | Name, sector, address and contact; a Leaflet map pin they can drag, click or look up | `PUT /sites/{id}`, `POST /sites/{id}/geocode` (Issue 23) |
| **Hours and closures** | The ordinary week, public holidays, one-tap temporary closure and lifting it | `/hours`, `/holidays/{date}`, `/closures` (Issue 24) |
| **Queues** | Add, rename, reorder, deactivate, bring back | `/queues` (Issue 25) |
| **Services** | Add, change, choose the queues that handle it, deactivate | `/services` (Issue 26) |
| **Staff** | Invite, withdraw an invitation, give or take a role here, rooms, switch an account off or on | `/staff…` (Issues 22, 28) |
| **Waiting-room screen** | The display mode with its privacy warning, now beside a **live preview** | `/settings/display` (Issue 27) |

**No business rule was added.** Every save is a request to an API that already checks the manager's
grant at this clinic, validates the change and writes the audit row in the same transaction. The screens
only decide three things, all about what is *offered*:

- **whose settings:** another clinic's id is the not-found page, on every tab;
- **which tabs:** each is shown and opened only with the grant its API writes need;
- **what the forms start from:** the server's current values.

**Found and fixed along the way:**

1. **The whole signed-in shell turned green whenever the mouse moved.** The public site's `.app:hover`
   tile colours leaked onto `body.app`, and `body` is hovered whenever the pointer is on the page. It
   showed up as tinted screenshots. `admin.css` now resets the hover and focus states too.
2. **Pages inside the #48 frame printed their title twice.** The display and payment pages kept their
   own page head inside the frame that already renders one. Both now sit under the settings tabs with
   only their lead sentence.
3. **The preview needed the board's display rule as a pure function.** `board_projection()` reads
   consent from the database, so the rule inside it moved to `project_entry()`, which
   `board_projection()` now calls, unchanged. The preview's made-up patients sit beside it in the
   patients module, because a guard test forbids reading a patient's name anywhere else.

## Summary

- **`src/web/dashboard/settings.py`** (new): `SettingsSection` (a `StrEnum`), `SETTINGS_TABS` (each tab's
  resource and verb), a route per tab, the settings home redirecting to the first tab the caller may
  open (gated like its menu link, `clinic_settings`), and an unknown tab redirecting there too
  (`list-view-ui-pattern.mdc`). The display and payment pages moved here from `routes.py`.
- **Templates:** `settings_base.html` (the tab bar and the one confirmation dialog),
  `settings_profile.html`, `settings_hours.html`, `settings_queues.html`, `settings_services.html` and
  `settings_staff.html`, plus the reworked `settings_display.html` and `settings_payment.html`.
- **`src/static/js/dashboard-settings.js`** (new): declarative plumbing with no rule in it.
  - `form[data-api]` sends the form as the JSON body the API takes; `button[data-api]` sends a request.
  - `data-confirm` asks first, in the words on the button.
  - A success reloads, so the screen shows the server's state; a refusal shows the server's words
    beside the control.
  - It also does the list pattern: sortable headers, a row that opens the record in the slideover
    beside the list (filled from the row's `data-record`), and moving queues up or down then saving the
    order in one request.
- **`src/static/js/settings-map.js`** (new): Leaflet from `/static/vendor/leaflet/` on OSM tiles. The pin
  writes the latitude and longitude fields and the fields move the pin; the form saves the fields.
  "Find" asks the clinic's own geocoding route and offers its candidates.
- **The live preview:** `display_preview()` in `src/modules/patients/consent.py` renders four made-up
  patients through `project_entry()` for every mode, with and without the reason; the page shows the set
  matching the form as it changes (`site-display-settings.js`).
- **Lists** (queues, services, staff) follow `docs/IDE/RULES/list-view-ui-pattern.mdc` in its
  server-rendered form: a `GET` filter bar (status and kind, category or role), click-to-sort columns,
  a row that opens beside the list, "Add" opening the same panel empty.
- **Destructive actions ask first**, each saying what happens: closing the clinic, lifting a closure,
  deactivating a queue or a service, taking a role away, switching an account off, withdrawing an
  invitation.
- **Docs:** the dashboard product doc, the M7 Status row, the README Status block, sprint 9's row and the
  generated bars.

## Design notes

**Tabs gated by the API's own grant, not by a new one.** Profile, hours and services write
`sites.profile`; queues `queues:delete` (the queue router's manage grant); staff `sites.staff:update`;
the screen `sites.display:read`, which a receptionist holds (Issue 27 lets them see the setting, and the
API refuses their save). A receptionist therefore opens only the waiting-room screen tab, a nurse no tab,
and the settings link itself stays the manager's (`sites.settings:update`), so a URL the menu does not
offer is refused.

**Why a reload after every save.** Each tab's state is small, and the server may change more than the
field that was edited. It fills defaults, drops another clinic's queue id from a service, and removes a
public clinic's payment profile. Reading the page again shows exactly what was stored, instead of
teaching the browser what the server does.

**The map is a way to choose a point, not the rule.** The saved value is the two coordinate fields.
The server refuses a point outside the operating country, and discovery reads the stored point directly.
A PostgreSQL test saves a new pin and finds the clinic 0 m away through `GET /api/v1/clinics/nearby`,
where a moment before it was not within 2 km.

**Temporary closure is "until you lift it".** The closure API takes an optional end time, but a
datetime typed into a browser has no reliable time zone. A clinic that has just lost power rarely knows
when it reopens anyway, so the tab closes the clinic until the manager lifts it, and says so.

**Deviations from the issue's file list:** the list pages are `settings_queues.html` and
`settings_services.html` rather than folded into the profile, and the tests live in
`tests/integration/dashboard/test_manager_settings.py` as listed.

**Out of scope:** platform-admin verification (Issue 29) and payment profiles (Issue 37, already a tab
behind its feature switch).

## Changes

- **New:** `src/web/dashboard/settings.py`, `src/templates/dashboard/settings_{base,profile,hours,queues,services,staff}.html`,
  `src/static/js/dashboard-settings.js`, `src/static/js/settings-map.js`,
  `tests/integration/dashboard/test_manager_settings.py`.
- **`src/modules/patients/consent.py`:** `project_entry()` (the rule `board_projection()` now calls),
  `PreviewSample`, `PREVIEW_SAMPLES`, `display_preview()`.
- **`src/web/dashboard/routes.py`:** the settings routes moved out; **`src/main.py`:** the settings
  router registered.
- **`src/templates/dashboard/settings_display.html`, `settings_payment.html`,
  `src/static/js/site-display-settings.js`:** inside the tabs, with the preview.
- **`src/static/css/admin.css`:** the `body.app:hover` reset; **`src/static/css/dashboard.css`:** the
  settings layouts, map, hours table, lists, panels and preview.
- **`tests/integration/sites/test_payment_profile_api.py`:** patches the page's new module.
- **Docs:** `docs/PRODUCT/05-clinic-dashboard.md`, `docs/GITHUB/MILESTONES/M7_clinic_dashboard.md`,
  `README.md`, `docs/GITHUB/README.md`, `docs/TEAM/WORKLOAD_SPLIT.md`.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` clean (250 files)
- [x] Whole suite as CI runs it, in UTC with PostgreSQL and Redis, on this branch after merging `main`:

  ```text
  $ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest tests/ -q --no-cov -n auto --dist loadscope
  1903 passed, 1 skipped, 9 xfailed, 57 warnings in 226.33s
  ```
- [x] The new tests:

  ```text
  tests/integration/dashboard/test_manager_settings.py::test_the_manager_opens_every_tab_and_the_bare_url_opens_the_first
  tests/integration/dashboard/test_manager_settings.py::test_another_clinics_settings_are_not_found_on_every_tab
  tests/integration/dashboard/test_manager_settings.py::test_tabs_follow_the_grants_at_the_clinic
  tests/integration/dashboard/test_manager_settings.py::test_each_change_a_screen_makes_lands_and_is_audited
  tests/integration/dashboard/test_manager_settings.py::test_removing_the_clinics_last_manager_is_refused_and_says_why
  tests/integration/dashboard/test_manager_settings.py::test_the_display_preview_is_the_boards_own_rule_for_every_mode
  tests/integration/dashboard/test_manager_settings.py::test_moving_the_pin_moves_the_clinic_in_discovery_at_once   (PostgreSQL)
  7 passed
  ```

  `test_each_change_a_screen_makes_lands_and_is_audited` sends each change in the body shape the screen
  sends, and asserts **each one** adds an audit row naming the manager. That covers 20 changes:

  - the profile and pin, the week, a holiday, a closure and lifting it;
  - adding, renaming, reordering and deactivating a queue;
  - adding, changing and deactivating a service;
  - inviting someone and withdrawing the invitation;
  - giving and taking a role, rooms, switching an account off and on;
  - the display mode.

- [x] **The consent guard caught the first draft of the preview.**
  `test_nothing_outside_the_patients_module_reads_a_patients_name` failed with `Found:
  ['src/web/dashboard/settings.py']` while the made-up patients lived in the web module. They now live
  beside the rule, and the guard passes.
- [x] **In a browser** (Playwright's Chromium at 1366×768 against a local PostgreSQL database with the
  demo clinics), every write the screens sent was recorded:

  ```text
  landed on /dashboard/sites/…/settings/profile;
  tabs: Profile · Hours and closures · Queues · Services · Staff · Waiting-room screen
  Profile: map click moved latitude -26.192710 -> -26.191855; Save -> PUT /sites/… 200; reloaded value -26.191855
  Hours: "Close the clinic" -> dialog "Close the clinic to new patients now? Joins stop on web, USSD,
         WhatsApp and at the desk until you lift the closure." -> Cancel: 0 closures (nothing sent)
         -> again, Yes: POST /closures 201; "Power failure: … Since 14 Sep 14:53, until it is lifted"
         -> Lift, Yes: DELETE /closures/… 200; 0 closures
  Queues: Add a queue (Dental, D, Room 5) -> POST /queues 201;
          open its row, Deactivate -> "Deactivate Dental? New patients can no longer join it; its
          tickets are kept." -> Yes: DELETE /queues/… 200; row status "Deactivated" (still listed)
  Staff: invite new.reception@… as Receptionist -> POST /staff/invitations 201; row "Pending, 17 Sep 2026, Withdraw"
  Waiting-room screen, preview as the form changes (nothing saved):
    Ticket numbers only      T012 · T013 · T014 · P015
    Number and a short name  T012 Thandiwe M. · T013 Sipho D. · T014 · P015
    Full name + reason       T012 Thandiwe Mokoena Chest pain · T013 Sipho Dlamini · T014 · P015
    and the privacy warning beside it, word for word from the API
  another clinic's /settings/profile -> 404;  no horizontal scroll at 1366×768
  ```

  The only console errors were the kernel notification bell's `403` on
  `/api/v1/notifications/center/unread-count` for clinic roles, on every page, and the deliberate 404.
  The bell was already broken before this PR; see *Known limits*.

### Screenshots (1366×768)

The profile with the map pin:

![Profile and map picker](https://github.com/Billykat7/clinicQ/blob/c15819a91921e0f11f392c2ef33c66e0346206b6/docs/GITHUB/PR/M7/assets/pr54/profile-map.png?raw=true)

Closing the clinic asks first; closed, with the reason and "Lift the closure":

![Closure confirmation](https://github.com/Billykat7/clinicQ/blob/c15819a91921e0f11f392c2ef33c66e0346206b6/docs/GITHUB/PR/M7/assets/pr54/closure-confirm.png?raw=true)

![Clinic closed](https://github.com/Billykat7/clinicQ/blob/c15819a91921e0f11f392c2ef33c66e0346206b6/docs/GITHUB/PR/M7/assets/pr54/hours-closed.png?raw=true)

Queues: adding one in the panel beside the list, and the list with a deactivated queue kept:

![Adding a queue](https://github.com/Billykat7/clinicQ/blob/c15819a91921e0f11f392c2ef33c66e0346206b6/docs/GITHUB/PR/M7/assets/pr54/queue-add.png?raw=true)

![The queues list](https://github.com/Billykat7/clinicQ/blob/c15819a91921e0f11f392c2ef33c66e0346206b6/docs/GITHUB/PR/M7/assets/pr54/queues-list.png?raw=true)

Staff: the invitation sent, and a nurse's roles and rooms:

![Staff and a staff member's panel](https://github.com/Billykat7/clinicQ/blob/c15819a91921e0f11f392c2ef33c66e0346206b6/docs/GITHUB/PR/M7/assets/pr54/staff-panel.png?raw=true)

The waiting-room screen: full name with reason chosen, the live preview beside the warning:

![Display mode with live preview](https://github.com/Billykat7/clinicQ/blob/c15819a91921e0f11f392c2ef33c66e0346206b6/docs/GITHUB/PR/M7/assets/pr54/display-preview.png?raw=true)

## Acceptance criteria

- [x] A manager can change hours, queues and staff without developer involvement: the week, holidays,
      closures, queues, services, invitations, roles, rooms and accounts, each through its tab (test and
      browser)
- [x] The map picker sets a coordinate that discovery immediately reflects: saved pin, then the nearby
      search finds the clinic 0 m away (PostgreSQL test)
- [x] The display-mode control shows a live preview and a plain-language privacy warning: every mode
      rendered through the board's rule, switched as the form changes, beside the API's warning (test and
      browser)
- [x] Every settings change is audited: each of the 20 changes the screens make adds an audit row naming
      the manager (test)
- [x] Settings are strictly scoped to the manager's own site: another clinic's id and an unknown id are
      404 on every tab (test and browser); tabs follow the grants at the clinic (test)
- [x] Destructive actions (deactivating a queue, removing staff) require confirmation: every one asks in
      its own words, Cancel sends nothing (browser); the API still refuses what it must, such as removing
      the last manager (test)

## Risk and rollback

No migration and no API change. `board_projection()` keeps its behaviour and its tests, now through a
pure `project_entry()`. The `admin.css` hover reset affects only the signed-in shell's background and
text colour while hovered, which is the fix. Rollback is a revert of this PR.

**Known limits:** a day with more than two sessions is kept by the API but only two are shown to edit.
A closure is "until lifted" from this screen. The notification bell's `403` for clinic roles is a
separate kernel issue, raised as its own task. The confirmation dialogs were checked in the browser run
above; Issue 55's Playwright suite will cover them in CI.

Closes #54
