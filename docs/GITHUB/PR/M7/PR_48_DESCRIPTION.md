# PR: The dashboard shell, navigation from each person's grants at their clinic, and the site switcher (Issue 48 / M7-48)

**Milestone:** [Milestone 7: Clinic Dashboard](https://github.com/Billykat7/clinicQ/milestone/7) ·
**Issue:** [#48](https://github.com/Billykat7/clinicQ/issues/48) · **Builds on:** #18 (RBAC) and #28
(staff-to-site and room assignment), both merged · **Opens M7**

Every staff screen of M7 now has one frame to live in. It resolves **which clinic**, **who is signed
in** and **what they may open there** once, on the server, and hands the page the answer. Nothing in a
template names a role: the receptionist, the nurse and the clinic manager see different screens because
their grants differ, and a guard test fails the build if a template ever decides by a role name.

Three things the issue did not spell out turned out to matter:

1. **Navigation has to be resolved per clinic, not per account.** The existing rail reads
   `User.role`, a single-role mirror. A person who is a receptionist at one clinic and the manager of
   another would have seen the same menu at both, including links the clinic's own API then refuses.
   The frame now evaluates the nav registry with the roles held **at the clinic in the URL**, the same
   role set `require_site_access` enforces with.
2. **The nav-gate table could not store the tier the front desk needs.** A receptionist's grant on
   `queues.tickets` reaches the whole clinic (`assigned`); a nurse's grant on the same resource reaches
   only their own queues (`own`). The front desk must open for the first and not the second, so its
   gate requires `assigned`. `nav_gate_overrides.scope` still only allowed `own` and `business` (Issue
   #171 added the middle tier to `role_permission` and missed this table), and `make seed-rbac`
   failed: `CHECK constraint failed: ck_nav_gate_overrides_ck_nav_gate_overrides_scope`. Migration
   `0025` widens the constraint.
3. **A nurse needed a screen a receptionist does not get,** and no existing grant separates them
   without a role check (a grant that cascades from `queues` reaches both). The room view is gated on a
   new resource tree, `visits.notes`, which only `nurse_doctor` holds. It is where Issue 53's private
   visit notes will be written, so the screen appears for exactly the people who may write in it.

## Summary

- **Registry** (`src/core/nav_registry.py`): three clinic destinations, each gated on the grant its
  own API enforces:

  | key | label | link | gate |
  |---|---|---|---|
  | `board` | Front desk | `/dashboard/sites/{site_id}/board` | `queues.tickets:read` at `assigned` |
  | `room` | My room | `/dashboard/sites/{site_id}/room` | `visits.notes:update` at `own` |
  | `clinic_settings` | Clinic settings | `/dashboard/sites/{site_id}/settings` | `sites.settings:update` at `assigned` |

  A destination whose `href` carries `{site_id}` is **site-scoped** (`href_at(site_id)`), and each has a
  keyboard `shortcut`. `destination_for_path()` treats `{site_id}` as one segment, so the RBAC simulator
  still resolves a pasted clinic URL.
- **Per-clinic visibility** (`src/core/nav_visibility.py`): `nav_visibility_for_roles()` (the union of
  several roles; `nav_visibility_for_role()` is now its one-role case) and `nav_visibility_at_site()`.
- **The frame** (`src/web/dashboard/shell.py`): `ClinicShell` holds the clinic, the other clinics the
  person works at with their switch links, the person's name, their roles at this clinic in words
  ("Clinic manager"), and the screens they may open here.
- **The pages** (`src/web/dashboard/routes.py`): `open_clinic_page()` applies signed in → your clinic
  (404 otherwise) → your screen (403 otherwise). A clinic home (`/dashboard/sites/{id}?section=…`)
  reopens the same screen at another clinic, or that clinic's first screen when the screen is not the
  caller's there. The front desk and the room render today's counts through the site guard; Issues 49
  and 53 replace those bodies. The display and payment settings pages moved here unchanged in
  behaviour and now render inside the frame.
- **`/dashboard`** sends clinic staff to the clinic they last worked in (an httpOnly preference cookie,
  checked against their assignments on every request) or their first. An account with no clinic, such
  as the operator, keeps the kernel landing page.
- **Templates:** `dashboard/base.html` (one slim bar: clinic and switcher, the screens as text tabs, the
  person and their role, the shortcuts button), `dashboard/board.html`, `dashboard/room.html`, the rail's
  clinic icons in `partials/app_nav.html`, and `partials/clinic_icons.html`.
- **Keyboard** (`src/static/js/dashboard-shell.js`): `g` then the key shown on each tab (`g b`, `g r`,
  `g s`), `g c` opens the switcher, `?` lists them. Keys come from the rendered links, so a screen the
  caller cannot open has no key. Nothing fires while focus is in a field. Pages add single-key actions
  with `data-kbd` (Issue 50's Call next will).
- **CSS** (`src/static/css/dashboard.css`): the bar, the switcher, the shortcuts dialog and the queue
  cards, tuned for 1366×768, in both themes.
- **Migration `0025_nav_gate_assigned_scope`** and the model's matching constraint.
- **Docs:** the RBAC matrix and decision snapshot regenerated (the new resource and three surfaces), the
  clinic dashboard product doc says how views follow grants, and the milestone progress for #48.

## Design notes

**The clinic lives in the URL; the cookie is only a preference.** `/dashboard/sites/{site_id}/board`
matches the API (`/api/v1/sites/{site_id}/…`) and the settings pages that already existed, keeps two
tabs at two clinics independent, and makes every screen a bookmarkable link. The cookie only answers
"where does `/dashboard` go", and a clinic id in it that the person no longer works at is ignored (a
test sets clinic B's id on clinic A's receptionist and still lands on A).

**Switching is a link, not a form.** The switcher is a native `<details>` of plain links, so it opens
with Enter, needs no script and no CSRF token, and changes nothing about the session: there is nothing
to sign in to again. The link carries the current screen (`?section=board`), and the target clinic
decides whether that screen is the caller's there.

**Why a new `visits` resource rather than a DENY or a tier trick.** A gate can only say "at least this
tier". Anything under `queues` cascades to a receptionist, and a DENY row would make the room depend on
a negative grant nobody sees in the matrix. `visits.notes` is where clinical notes belong anyway: it is
health information (non-negotiable 4), so no grant on the clinic's operations should inherit into it.
The manifest scopes it by queue, so `own` means the nurse's assigned rooms. Issue 53 adds the API and a
cross-tenant case; until then `test_cross_tenant.py` lists it as pending with that issue named.

**Tests read the frame, not the HTML.** `docs/IDE/RULES/testing-strategy.mdc` forbids asserting markup,
so the tests assert status codes and redirects, and what a page offers through `response.context["clinic"]`:
the exact data the rail and tabs render from.

**Downgrade is lossy in the safe direction.** `0025` down moves any `assigned` gate to `business`, so a
surface opens for fewer people, never more. Because the nav-gate sync is insert-only (a live admin
re-gate must survive a redeploy), those rows stay `business` after upgrading again until they are reset;
the demonstration below shows it.

**Out of scope:** the live board (Issue 49), its buttons (50), walk-ins (51), reordering (52), the
room's actions and notes (53) and the settings screens (54).

## Changes

- **`src/core/nav_registry.py`:** `SITE_ID_PLACEHOLDER`, `NavDestination.shortcut`, `.site_scoped`,
  `.href_at()`, the three destinations, `site_destinations()`, and segment-wise `destination_for_path()`.
- **`src/core/nav_visibility.py`:** `nav_visibility_for_roles()`, `nav_visibility_at_site()`; the grant
  helpers take a role list.
- **`src/modules/visits/`** (new): the `visits` manifest (`visits`, `visits.notes`, nurse or doctor
  `update` at `own`), registered in `src/core/rbac_manifest_registry.py`.
- **`src/web/dashboard/shell.py`, `routes.py`** (new) and **`src/main.py`** (router registered).
- **`src/web/routes.py`:** `/dashboard` redirects clinic staff; the two settings pages moved out.
- **`src/web/context.py`:** `page_context(nav=…)` and a `clinic` frame on every signed-in page, so the
  rail's clinic links stay when a receptionist opens their profile.
- **`alembic/versions/0025_nav_gate_assigned_scope.py`** and **`src/database/models/nav_gate_override.py`.**
- **Templates and static files** listed in the summary.
- **Tests:** `tests/integration/dashboard/` (new fixture: two clinics, a receptionist, a nurse on one
  room, a manager, a person who is a receptionist at one clinic and the manager of the other, and the
  operator) with ten tests; two registry tests; a template guard; the layout test now allows a frame
  between a page and its layout; the payment page test patches its new module; `visits` pending in the
  cross-tenant guard.
- **Docs:** `docs/architecture/rbac-matrix.md`, `tests/snapshots/rbac_decisions.txt`,
  `docs/PRODUCT/05-clinic-dashboard.md`, the M7 milestone Status row, the README Status block, and the
  generated bars.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` clean (248 files)
- [x] Whole suite as CI runs it, in UTC with PostgreSQL and Redis:

  ```text
  $ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest tests/ -q --no-cov -n auto --dist loadscope
  1889 passed, 1 skipped, 9 xfailed, 45 warnings in 165.37s
  ```

  The one skip is the 07:30 rush, which skips under `-n` and runs alone in its own CI step.
- [x] The new tests:

  ```text
  tests/integration/dashboard/test_dashboard_shell.py::test_a_receptionist_a_nurse_and_a_manager_each_get_their_own_screens
  tests/integration/dashboard/test_dashboard_shell.py::test_the_frame_names_the_clinic_and_who_is_signed_in_with_their_role_there
  tests/integration/dashboard/test_dashboard_shell.py::test_a_screen_a_role_may_not_open_is_refused_not_just_left_out_of_the_menu
  tests/integration/dashboard/test_dashboard_shell.py::test_another_clinics_screens_are_not_found_like_an_id_that_does_not_exist
  tests/integration/dashboard/test_dashboard_shell.py::test_switching_clinics_reopens_the_board_scoped_to_the_new_clinic_without_signing_in
  tests/integration/dashboard/test_dashboard_shell.py::test_a_switch_to_a_screen_not_open_at_the_other_clinic_lands_on_its_first_screen
  tests/integration/dashboard/test_dashboard_shell.py::test_a_remembered_clinic_the_caller_does_not_work_at_is_ignored
  tests/integration/dashboard/test_dashboard_shell.py::test_the_menu_follows_a_grant_change_with_no_code_change
  tests/integration/dashboard/test_dashboard_shell.py::test_the_room_reads_only_the_queues_the_nurse_is_assigned_to
  tests/integration/dashboard/test_dashboard_shell.py::test_a_signed_out_visit_goes_to_sign_in_and_the_page_opens_after_it
  tests/unit/security/test_nav_registry.py::test_every_clinic_destination_names_its_site_and_has_its_own_shortcut
  tests/unit/security/test_nav_registry.py::test_a_clinic_page_path_resolves_to_its_destination_whichever_clinic_it_names
  tests/unit/platform/test_ui_shell.py::test_no_template_decides_anything_by_a_role_name
  tests/unit/platform/test_ui_shell.py::test_only_layouts_extend_base_and_every_page_extends_a_layout
  14 passed in 1.74s
  ```

- [x] **The template guard can fail.** A throwaway template with two role decisions, then removed:

  ```text
  E   AssertionError: templates deciding by role:
  E     _probe/p.html:1: {% if user.role == 'receptionist' %}
  E     _probe/p.html:2: {{ 'a' if 'clinic_manager' in roles }}
  ```

  Its first draft also flagged `{{ user.role }}` on the access console and `rbac_tab == 'roles'`; it
  now flags decisions only, and printing a role or a tab named "roles" passes.
- [x] **Before the migration**, the suite reported 242 errors and 29 failures; the ones inspected failed
  in a fixture's catalog sync with
  `sqlite3.IntegrityError: CHECK constraint failed: ck_nav_gate_overrides_ck_nav_gate_overrides_scope`.
  With the constraint widened, five failures were left, each a consequence this PR then dealt with: the
  layout test, the cross-tenant guard (`visits` pending), the payment page's moved module, and the RBAC
  matrix and snapshot.
- [x] **Migration on PostgreSQL**, on a fresh database through `scripts/db/deploy-sequence.sh`
  (`0024 -> 0025`, then `rbac seed --check: in sync`). The seeded gates, down and up:

  ```text
  -- after upgrade and seed          -- after alembic downgrade -1
  board           | assigned         board           | business
  clinic_settings | assigned         clinic_settings | business
  room            | own              room            | own
  -- at 0024 the narrow constraint refuses 'assigned' again:
  DETAIL:  Failing row contains (board, queues.tickets, read, null, assigned, f, …)
  ```

- [x] **In a browser** (Playwright's Chromium at exactly 1366×768, against that database with the demo
  seed plus a second clinic for the receptionist and a room for the nurse):

  ```text
  signed out, open /dashboard/sites/01a0…d878/board
    -> /?next=%2Fdashboard%2Fsites%2F01a0…d878%2Fboard&openSignin=1   (the sign-in modal opens)
  sign in as reception@clinicq.example
    -> /dashboard/sites/01a0…d878/board                               (back on the page asked for)
  document.documentElement.scrollWidth, innerWidth -> 1366, 1366     (light and dark: no sideways scroll)
  ? -> the shortcuts dialog is open;  g c -> the switcher is open
  Enter on the other clinic -> /dashboard/sites/01a0…0e04/board, "Glen Earle Clinic",
    tabs ["Front desk", "Clinic settings"], role "Clinic manager"     (no sign-in)
  g s -> /dashboard/sites/01a0…0e04/settings/display
  /dashboard -> /dashboard/sites/01a0…0e04/board                      (the switch was remembered)
  nurse@   -> …/room,  tabs ["My room"],  rail [Dashboard, My room, Account, Sign out]
  manager@ -> …/board, tabs ["Front desk", "Clinic settings"], rail [Dashboard, Front desk, Clinic settings, Account, Sign out]
  ```

### Screenshots (1366×768)

The receptionist's front desk, light and dark:

![Front desk at 1366×768, light](https://github.com/Billykat7/clinicQ/blob/cabcbd2d9224a5197b275c1481b5f0f97c3c31b5/docs/GITHUB/PR/M7/assets/pr48/board-1366x768-light.png?raw=true)

![Front desk at 1366×768, dark](https://github.com/Billykat7/clinicQ/blob/cabcbd2d9224a5197b275c1481b5f0f97c3c31b5/docs/GITHUB/PR/M7/assets/pr48/board-1366x768-dark.png?raw=true)

The switcher open (`g c`), and the same person after switching, where they manage the clinic:

![The site switcher](https://github.com/Billykat7/clinicQ/blob/cabcbd2d9224a5197b275c1481b5f0f97c3c31b5/docs/GITHUB/PR/M7/assets/pr48/switcher-1366x768.png?raw=true)

![After switching clinics](https://github.com/Billykat7/clinicQ/blob/cabcbd2d9224a5197b275c1481b5f0f97c3c31b5/docs/GITHUB/PR/M7/assets/pr48/switched-1366x768.png?raw=true)

The nurse (one room) and the clinic manager:

![The nurse's room view](https://github.com/Billykat7/clinicQ/blob/cabcbd2d9224a5197b275c1481b5f0f97c3c31b5/docs/GITHUB/PR/M7/assets/pr48/nurse-1366x768.png?raw=true)

![The clinic manager's front desk](https://github.com/Billykat7/clinicQ/blob/cabcbd2d9224a5197b275c1481b5f0f97c3c31b5/docs/GITHUB/PR/M7/assets/pr48/manager-1366x768.png?raw=true)

The shortcut list (`?`):

![Keyboard shortcuts](https://github.com/Billykat7/clinicQ/blob/cabcbd2d9224a5197b275c1481b5f0f97c3c31b5/docs/GITHUB/PR/M7/assets/pr48/shortcuts-1366x768.png?raw=true)

## Acceptance criteria

- [x] A receptionist, nurse and clinic manager each see a different, correct navigation: front desk;
      my room; front desk and clinic settings (test, and the browser run above)
- [x] Switching sites reloads the board scoped to the new site without a re-authentication: the same
      client, one sign-in, clinic B's queues on the reloaded board, and B's menu (test and browser)
- [x] The layout is fully usable at 1366×768 without horizontal scrolling: `scrollWidth` 1366 at a
      1366 viewport for every role and both themes; screenshots attached at exactly that size
- [x] Navigation is driven by permissions data, not hard-coded role checks in templates: a grant added
      and removed changes the menu and the page's answer with no code change (test), and the template
      guard fails the build on a role decision
- [x] The primary actions are reachable by keyboard: every screen by `g` and its key, the switcher by
      `g c`, the list by `?`; the switcher and tabs are native links and a disclosure, reachable by Tab
- [x] An unauthenticated visit redirects to sign-in and returns to the intended page afterwards (the
      redirect and its `next` by test; the return through the sign-in modal in the browser run)

## Risk and rollback

The kernel's rail and the access console are unchanged for an account with no clinic. For clinic staff,
`/dashboard` now redirects to their clinic, and the two settings pages render inside the new frame at the
same URLs with the same gates. One migration widens a check constraint and rewrites no data. Rollback is
a revert of this PR and `alembic downgrade 0024`, which moves the three `assigned` gates to `business`
(they open for fewer people, never more).

**Known limits:** the front desk and room bodies are server-rendered counts until Issues 49 and 53; the
switcher lists every live clinic the person holds a role at, including one where no screen is theirs,
and that clinic's home answers 403 honestly rather than hiding it.

Closes #48
