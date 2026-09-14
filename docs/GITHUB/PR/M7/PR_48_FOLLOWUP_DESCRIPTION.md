# PR: Offer the notification bell only where its badge poll is allowed (Issue 48 / M7-48 follow-up)

**Milestone:** [Milestone 7: Clinic Dashboard](https://github.com/Billykat7/clinicQ/milestone/7) ·
**Issue:** [#48](https://github.com/Billykat7/clinicQ/issues/48) (closed by #170; this is its
follow-up) · **Fixes:** the known limit "The notification bell answers 403 for clinic roles" in
`RELEASE_v0_7_0.md`, and the platform admin's `403` listed in `RELEASE_v0_5_0.md`

Every page in the dashboard shell showed the notification bell. Its script polls
`GET /api/v1/notifications/center/unread-count`, which requires `communications.notifications:read`.
Reception, nurses, clinic managers and the operator held no such grant, so every page they opened
logged a `403`, and the bell was dead.

**Adding the grant to the clinic roles cannot fix it.** That was the first plan, and it was tried. A
clinic role is held **at a site** (`user_roles` with `scope_type='site'`), and
`active_roles_for_user` counts a site-held role only when the check names that site. The centre routes
name no site, so a clinic manager has no roles there whatever the manifest grants. With the grant
seeded, the manager still got `403`. Worse, `can()` on their clinic page, which resolves roles at the
clinic, then answered `True`: gating the bell on it would have shown a bell whose poll is refused.

## Summary

- **The bell is offered only to a caller its routes allow.** `page_context` sets
  `show_notification_bell` from `can_open_notification_centre(nav)`. The layout renders the bell
  partial and `notification-center.js` only when it is set.
- **The operator's bell works.** `platform_admin` holds `communications.notifications:read` at `own`.
  Its role is unscoped, so the grant counts on the site-less centre routes. The centre is first-person
  by construction: every route reads and marks only the caller's own rows.
- **Clinic roles get no bell, on purpose.** Nothing in ClinicQ writes to a staff member's centre yet;
  only kernel email categories record items there. The manifest's docstring says what must change
  first when that stops being true.
- **One resource key.** `CENTRE_RESOURCE_KEY` in `src/modules/notifications/__init__.py` is used by
  both the route dependency and the page flag, so the two gates cannot drift apart.

## Design notes

- **Why not resolve the centre routes with roles held at any site?** That would give clinic staff a
  working bell, but it changes the RBAC enforcement path for a surface with no content for them. It
  deserves its own issue and a security review, once something writes to a staff member's centre.
- **Why not an unscoped `user` role for every staff member?** The kernel documents `user` as the role
  that holds the bell, but no shipped grant gives it one (see the matrix). Giving every staff member
  an unscoped `user` assignment would need a data migration and every staff-creation path changed, and
  it widens what `user` unlocks for them.
- **A flag, not a template `can(...)` call.** `docs/IDE/RULES/testing-strategy.mdc` rules out asserting
  on HTML. A context flag is what the template renders from, so the tests can assert on it, just as
  `test_dashboard_shell.py` asserts on the `clinic` frame.

## Changes

- **`src/web/context.py`:** `can_open_notification_centre(nav)`, and `show_notification_bell` in
  `page_context`.
- **`src/templates/layouts/dashboard.html`:** the bell partial and its script render behind
  `show_notification_bell`.
- **`src/modules/communications/rbac_manifest.py`:** a `platform_admin` grant on
  `communications.notifications` at `read · own`, and a docstring explaining why the clinic roles hold
  none.
- **`src/modules/notifications/__init__.py`:** `CENTRE_RESOURCE_KEY`.
- **`src/modules/notifications/router.py`:** the centre dependency uses the constant and
  `PermissionVerb.READ`. The stale comment claiming the base `user` role holds the grant
  (migration `0044`) now points at the manifest.
- **`docs/architecture/rbac-matrix.md`, `tests/snapshots/rbac_decisions.txt`:** regenerated
  (`make rbac-matrix`, `make rbac-snapshot`). One cell and one decision move. The hand-written `own`
  tier row now names the operator's centre.
- **`tests/integration/dashboard/test_notification_bell.py`** (new).
- **`docs/GITHUB/PR/M7/assets/pr48/followup-bell-header.png`** (new).

## Testing

### The new tests

```text
$ TZ=UTC pytest tests/integration/dashboard/test_notification_bell.py -v
test_the_bell_is_offered_exactly_when_its_poll_is_allowed[desk.a] PASSED
test_the_bell_is_offered_exactly_when_its_poll_is_allowed[nurse.a] PASSED
test_the_bell_is_offered_exactly_when_its_poll_is_allowed[manager.a] PASSED
test_the_bell_is_offered_exactly_when_its_poll_is_allowed[desk.b] PASSED
test_the_bell_is_offered_exactly_when_its_poll_is_allowed[both] PASSED
test_the_bell_is_offered_exactly_when_its_poll_is_allowed[trainee] PASSED
test_the_bell_is_offered_exactly_when_its_poll_is_allowed[operator] PASSED
test_a_clinic_manager_is_not_offered_the_bell PASSED
test_the_operator_is_offered_its_own_count PASSED
test_the_operator_grant_does_not_open_the_delivery_viewer PASSED
10 passed
```

The parity test signs in as every person in the dashboard fixture. On `/dashboard` and on
`/account/profile` it asserts that `show_notification_bell` equals "the unread-count poll answers
200".

### The tests fail when the fix is undone

Each change was applied by hand, run, and reverted:

```text
== 1. bell always offered (the layout before this PR)
FAILED test_the_bell_is_offered_exactly_when_its_poll_is_allowed[desk.a]
FAILED test_the_bell_is_offered_exactly_when_its_poll_is_allowed[nurse.a]
FAILED test_the_bell_is_offered_exactly_when_its_poll_is_allowed[manager.a]
FAILED test_the_bell_is_offered_exactly_when_its_poll_is_allowed[desk.b]
FAILED test_the_bell_is_offered_exactly_when_its_poll_is_allowed[both]
FAILED test_the_bell_is_offered_exactly_when_its_poll_is_allowed[trainee]
FAILED test_a_clinic_manager_is_not_offered_the_bell
7 failed, 3 passed
== 2. operator grant removed
FAILED test_the_operator_is_offered_its_own_count
1 failed, 9 passed
== 3. clinic_manager granted the centre (the first plan)
FAILED test_the_bell_is_offered_exactly_when_its_poll_is_allowed[manager.a]
FAILED test_the_bell_is_offered_exactly_when_its_poll_is_allowed[both]
FAILED test_a_clinic_manager_is_not_offered_the_bell
3 failed, 7 passed
```

Mutation 3 is the one that matters most. If someone later grants a clinic role the centre, the build
fails until the routes can honour it.

### The regenerated RBAC artefacts

```diff
-| `communications.notifications` | — | — | — | — | — | delete · business | — |
+| `communications.notifications` | — | — | — | — | read · own | delete · business | — |

-resource | role=platform_admin | key=communications.notifications | verb=read | deny | tier=own
+resource | role=platform_admin | key=communications.notifications | verb=read | allow | tier=own
```

```text
$ python scripts/generate_rbac_snapshot.py --check
tests/snapshots/rbac_decisions.txt is up to date.
```

### In a browser, before and after

Playwright signed in through the modal on the local M7 verify database (`clinicq_m7_verify`, Docker
Postgres). It reloaded the landing page and recorded every `/notifications/center` response. For
"before", `main` at `38f295f` served on port 8018. For "after", this branch served on port 8019, once
`python -m scripts.db.seed_rbac` had written the new grant (`grants +1`).

| Signed in as | Before: bell | Before: centre requests | After: bell | After: centre requests |
|---|---|---|---|---|
| `reception` (lands on `/board`) | shown | `unread-count → 403` ×2 | not rendered | none |
| `nurse` (lands on `/room`) | shown | `unread-count → 403` ×2 | not rendered | none |
| `manager` (lands on `/board`) | shown | `unread-count → 403` ×2 | not rendered | none |
| `platform-admin` (`/dashboard`) | shown | `unread-count → 403` ×2 | shown | `unread-count → 200` ×2 |

Header after the fix: the clinic manager (top) has no bell, and the operator (bottom) has one.

![Header after the fix: manager without the bell, operator with it](https://github.com/Billykat7/clinicQ/blob/a50ae3612ada84ab6164d00f093a76b6ad8fc0ca/docs/GITHUB/PR/M7/assets/pr48/followup-bell-header.png?raw=true)

### The rest of the suite

Written on `main` at `38f295f` (M7's close) and rebased onto `main` at `78f82c4` (M8's close) before
pushing. On the rebased branch, in UTC with PostgreSQL, Redis and Chromium:

```text
$ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest -q --no-cov -n auto --dist loadscope
2080 passed, 1 skipped, 9 xfailed in 430.89s

$ ruff check . && ruff format --check .
All checks passed!
558 files already formatted

$ mypy src/
Success: no issues found in 274 source files

$ make milestone-progress-check
14 milestone(s): up to date
```

The before-and-after browser table above was recorded before the rebase, against M7's `main`.

## Acceptance criteria

- [x] A signed-in clinic manager's dashboard makes no request that answers `403` for the bell. Shown
      by `test_a_clinic_manager_is_not_offered_the_bell` and by the browser table.
- [x] The bell is offered through a grant check, never a role name. `show_notification_bell` comes
      from `nav.can(CENTRE_RESOURCE_KEY, READ)`, and the trainee's custom role follows the same rule.
- [x] The grant sits in the manifest that declares the resource, at the narrowest tier that works, and
      the matrix and snapshot are regenerated.
- [x] The operator's `own` grant does not open the whole-platform delivery viewer:
      `GET /api/v1/notifications` still answers `403`.

## Not tested, and known limits

- **Clinic staff have no notification centre.** That is the trade-off chosen here. Until something
  writes to a staff member's centre, and the centre routes resolve roles held at any site, they are not
  offered the bell.
- **A role granted the centre from `/admin/rbac` is only as good as where it is held.** An operator who
  grants a site-held role the centre from the console lights the bell on that role's clinic pages, and
  the poll still answers `403`. The build only catches this for the shipped grants.
- **The v0.7.0 release note still lists the `403` as a known limit.** Whether to amend it depends on
  whether `v0.7.0` is tagged before or after this merges.

## Risk and rollback

Low. It changes one template condition, one context key and one grant row, adding a permission to
`platform_admin` and removing none. `make seed-rbac` is insert-only, so rolling the code back
leaves the operator's grant in place. That is harmless, because the routes it opens are first-person.
Revert the two `Issue 48:` commits to roll back.

Refs #48 (already closed by #170; this PR closes nothing new)
