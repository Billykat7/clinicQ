# PR: ClinicQ's five roles, their grants from manifests, and a guard on every API route (Issue 18 / M3-18)

**Milestone:** [Milestone 3: Identity, Auth, RBAC & Consent](https://github.com/Billykat7/clinicQ/milestone/3) ·
**Issue:** [#18](https://github.com/Billykat7/clinicQ/issues/18) · **Builds on:** #15, #16, #17 (PRs #129, #130, #131)

> **Merge order:** after #129, #130 and #131. Until then this branch carries their commits and the
> Conventions check fails on them; re-run it after. **D's dashboard shell (Issue 48) starts from
> this PR**: the resource tree and the role grants below are what it gates on.

The kernel's RBAC is richer than the spec (a resource tree, cumulative verbs, named actions, scope
tiers, manifests, a console, a golden snapshot), so there is no second RBAC here. The work was the
vocabulary: `UserRole` still carried the source project's portal roles (`tenant`, `owner`,
`manager`, `vendor`) and none of ClinicQ's five had a single grant. Two things were not what they
seemed:

- **The decision snapshot guarded nothing.** `tests/snapshots/rbac_decisions.txt` had never been
  committed and the test comparing it with the code had been removed, so `make rbac-snapshot-check`
  failed on every checkout (on `main`: `256 decision(s) have moved`, exit 1) and CI was green
  anyway.
- **It seeded a subset.** The snapshot seeded two roles and five hand-picked grants, not what a
  deployment seeds, so the grants the widgets and communications manifests ship were never pinned:
  the file said a deployed `admin` could not open either console.

## Summary

- **Roles:** `UserRole` is `user`, `admin` and ClinicQ's `patient`, `receptionist`, `nurse_doctor`,
  `clinic_manager`, `platform_admin`. The four portal roles are gone with every grant, test and
  docstring example that named them, and the dead portal manifest is deleted. No aliases.
- **Grants, in the manifests of what they gate:** a new `sites` tree (`profile`, `settings`,
  `display`, `staff`, `reports`) and `queues` tree (`call`, `tickets`, `tickets.priority`), declared
  now so Issue 48 can gate on them; the models land in M4 and M6. Plus `patients.self` and the
  dashboard. The full table is `docs/architecture/rbac-matrix.md` (excerpt below).
- **`require_patient`:** patient routes are gated on the `patient` role's grant, so that role's
  grants are enforced, not decorative.
- **The route guard:** `tests/unit/security/test_api_route_gates.py` walks the real router tree and
  fails on any `/api/v1` route without a gate dependency, unless it is on a closed list with a
  reason (public, self-service, or the kernel's decided-in-the-handler routes).
- **The matrix document is generated** (`make rbac-matrix`) from a database seeded through
  `sync_rbac_catalog`, and `tests/test_rbac_matrix.py` fails when it drifts.
- **Decision 6 settled:** seeding stays in `make seed-rbac`, run automatically by the deploy
  sequence after the migrations (see *Design notes*).
- **The snapshot is a guard again**, seeded the way a deployment is, and regenerated deliberately in
  three reviewable steps (see *Every line that moved*).

## The matrix (excerpt, ClinicQ's resources)

| Resource | `patient` | `receptionist` | `nurse_doctor` | `clinic_manager` | `platform_admin` |
|---|---|---|---|---|---|
| `dashboard` | — | read · business | read · business | read · business | read · business |
| `patients.self` | update · own | — | — | — | — |
| `queues` | — | read · assigned | read · assigned | delete · assigned | read · business |
| `queues.call` | — | update · assigned | **update · own** | delete · assigned | read · business |
| `queues.tickets` | — | update · assigned | update · own | delete · assigned | read · business |
| `sites.display` | — | **read** · assigned | — | update · assigned | delete · business |
| `sites.settings` | — | **—** | — | update · assigned | delete · business |
| `sites.profile` | — | read · assigned | read · assigned | update · assigned | delete · business |
| `sites.staff` | — | read · assigned | read · assigned | delete · assigned | delete · business |
| `sites.reports` | — | — | — | update · assigned | delete · business |

`update · assigned` = the verb (and every lower one) at that tier: `own` is the caller's own rows,
`assigned` the sites they hold a role at, `business` every clinic.

## Design notes

**Decision 6: the seed stays in `make seed-rbac`, and nothing is manual.** The criterion says
"created by migration, not by a manual script"; what it guards against is a deployment whose
permissions depend on someone remembering a step. That already holds: `scripts/db/deploy-sequence.sh`
runs `alembic upgrade head`, then `seed_rbac`, then `seed_rbac --check`, in the new image before it
serves traffic, and CI runs the same sequence on every pull request. A migration would be the worse
home: grants sit next to the resources they gate in each module's `rbac_manifest.py`, while a
migration freezes the values of its day and needs a second hand-written migration for every later
edit. The seed is idempotent and insert-only, so an operator's edit from `/admin/rbac` survives a
deploy. The spec's criterion and scope now say this, and decision 6 is recorded in
`docs/GITHUB/ISSUES/README.md`.

**A receptionist and a nurse differ by tier, not by verb.** Both hold `update` on `queues.call`; the
receptionist's grant is `assigned` (every queue at their clinics), the nurse's `own` (the queues
assigned to them). That is the kernel's scope-tier model doing exactly what it was built for, and it
means Issue 28's room assignment and Issue 19's guard narrow the same grant rather than inventing a
second one. "A nurse cannot call next on a queue they are not assigned to" is therefore met in two
halves: the grant here (pinned by the snapshot and a test that resolves the tier), and the row in
Issue 19, where an unassigned queue answers 404.

**`platform_admin` is not `admin`, and is not scope-exempt.** `admin` stays the kernel's
configuration role (users, RBAC, logs), the one seeded scope-exempt so a deployment always has a
way back in, and it holds nothing on `sites` or `queues`. `platform_admin` is the operator's
cross-clinic role at `business`; reading a clinic it is not assigned to will be explicit and
audited (Issue 19), never implicit.

**Why some routes are listed rather than gated.** The kernel's messaging, alerts and documents
routes pick their resource key per record (an alert's folder, a document's owner type, whether the
caller is in the thread), so they call the resolver in the handler; they are on a closed list with
that reason, and a test forbids any ClinicQ module from joining it. Sign-in, signed links,
signature-verified webhooks, `/info` routes and the caller's own `/auth/me*` are listed as public or
self-service.

**Not small, but contained.** The spec asked for a small PR; the diff is wide because six kernel
test files named the portal roles. The behaviour change is the enum, three manifests, one
dependency factory and one seed helper.

## Every line that moved

The snapshot is regenerated in three commits so each diff has one cause:

1. **`e4dfc38`: the file as the code stood before this PR** (272 decisions: `admin` and `user`;
   `main` would give 256, the 16 more are the `patients` resources #17 declared), so the next two
   diffs are reviewable at all.
2. **`9814cfc`: seeded the way a deployment is** (`sync_rbac_catalog` instead of a hand-picked
   subset). **97 decisions move, all `admin`, all deny → allow:** 84 on `communications.*`, 10 on
   `widgets.*`, and the `alerts`, `announcements` and `messages` rail entries. Those are the grants
   the communications and widgets manifests always shipped. Nothing else moves.
3. **`365c1c3`: the roles.** **960 decisions added, 0 moved, 0 removed** (1232 in all):
   - 880 for the five new roles, 176 each (every resource × 4 verbs, and every surface). Allows:
     `patient` 3 (`patients.self` read/create/update), `receptionist` 14, `nurse_doctor` 13,
     `clinic_manager` 36, `platform_admin` 29; everything else deny. Each allow is a cell of the
     matrix above.
   - 80 for `admin` and `user` against the ten new `sites.*` and `queues.*` resources: all
     `deny tier=own`, by design (the kernel roles hold no clinic grant).

The committed-equals-code test now runs in CI, so the next moved decision fails a pull request.

## Changes

- **`src/commons/enums.py`:** `UserRole` without the portal roles; `GrantScope`'s docstring in
  ClinicQ terms.
- **`src/core/rbac.py`:** `default_system_roles()` seeds seven roles; `SEEDED_ROLE_GRANT_SCOPES` for
  them; `ensure_roles_hold_permission()` for a principal that is not a `user` row.
- **New manifests:** `src/modules/sites/rbac_manifest.py`, `src/modules/queues/rbac_manifest.py`;
  grants added to `src/modules/patients/rbac_manifest.py` and the dashboard manifest
  (`src/api/v1/routes/rbac_manifest.py`); both registered in `rbac_manifest_registry.py`.
- **`src/api/rbac_deps.py`:** `require_patient`; every factory tags what it builds (`__rbac_gate__`).
- **`src/modules/patients/router.py`:** `/me` and `/logout` behind `require_patient`.
- **`src/core/rbac_snapshot.py`:** seeds through `sync_rbac_catalog`.
- **New:** `src/core/rbac_matrix.py`, `scripts/generate_rbac_matrix.py`, `make rbac-matrix`,
  `docs/architecture/rbac-matrix.md`, `tests/unit/security/test_api_route_gates.py`.
- **Deleted:** `src/web/rbac_manifest.py` (the unregistered portal manifest).
- **Docstrings** in `rbac.py`, `rbac_simulator.py`, `rbac_language.py`, `rbac_snapshot.py`,
  `nav_visibility.py` and `schemas/rbac.py`: worked examples in ClinicQ roles.
- **Tests:** `tests/test_rbac_matrix.py` (+6), the snapshot equality test restored, six kernel test
  files moved to ClinicQ roles of the same seeded shape.
- **Docs:** decision 6 in `docs/GITHUB/ISSUES/README.md`; the spec's scope and criterion.

## Testing

- [x] `ruff check`, `ruff format --check`, `mypy src/` (174 files) clean.
- [x] `make test`: **1117 passed**, 17 skipped, 9 xfailed. PostgreSQL and Redis: **17 passed**.
- [x] **The route guard fails on a real route.** With `GET /patients/me`'s gate swapped for bare
      authentication:
      `AssertionError: API routes without a gate dependency … and on no list: GET /api/v1/patients/me`.
      Restored, it passes; the test file also carries a fixture proving the check discriminates.
- [x] **How to verify, on a fresh PostgreSQL database:**

```text
$ make migrate-up && make seed-rbac
rbac seed: roles +7, resources +38 (updated 0), actions +7, permissions +155, nav-gates +24,
           grants +28, named-grants +0
$ make seed-rbac-check        → rbac seed --check: in sync
$ make rbac-snapshot-check    → tests/snapshots/rbac_decisions.txt is up to date.
$ scripts/generate_rbac_matrix.py --check → docs/architecture/rbac-matrix.md is up to date.

 name           | is_system | is_scope_exempt        role         | resource       | max_verb | scope
 admin          | t         | t                      nurse_doctor | queues.call    | update   | own
 clinic_manager | t         | f                      receptionist | queues.call    | update   | assigned
 nurse_doctor   | t         | f                      receptionist | sites.display  | read     | assigned
 patient        | t         | f                      (13 receptionist and nurse grants in all)
 platform_admin | t         | f
 receptionist   | t         | f
 user           | t         | f
```

- [ ] **Not shown: signing in as a receptionist and not seeing the display-mode setting.** There is
      no display-settings page yet (Issue 27). What is proven instead: the route gate refuses
      `sites.display:update` and `sites.settings:update` for a receptionist and admits a clinic
      manager, and the template helper `can()` returns the same answers.

## Acceptance criteria

- [x] Every protected route declares its permission through the dependency, checked by a guard test
      (`test_api_route_gates.py`; the listed exceptions are closed and reasoned).
- [x] A nurse cannot call next on a queue they are not assigned to: **the grant half here** (the
      nurse's `queues.call` grant is `own`, the receptionist's `assigned`; tested), **the row half in
      Issue 19** (the unassigned queue answers 404). Call-next itself is Issue 42.
- [x] A receptionist cannot change display mode or site settings: refused by the gate, tested
      against a seeded database.
- [x] The UI never renders an action the role cannot execute: `can()` gives the gate's answer
      (tested); there is no display-mode page yet to render it on.
- [x] Seeded roles and permissions are created automatically, not by a manual script: by the deploy
      sequence's `make seed-rbac` step after the migrations (decision 6, with the reasoning).
- [x] The RBAC matrix is committed and matches the seeded data, verified by a test.

## Risk and rollback

No migration. On deploy, the seed step adds the five roles and 28 grants and inserts nothing that
changes an existing grant (insert-only). **Anyone holding a portal role in a database** keeps the
string on their `user` row and `user_roles` rows but it now names a role with no grants: they reach
nothing until reassigned. A clean ClinicQ deployment has none; a development database seeded before
this has only the kernel and ClinicQ roles. Rollback is a revert; the seeded roles and grants can
stay (they are inert without code that reads them), or be deleted from `/admin/rbac`.

**Follow-ups noticed:** the property-domain vocabulary the kernel came with (lease, maintenance and
work-order events in `SecurityAuditEvent`, `NotificationTemplate`, the messaging and documents
modules, `ENCRYPTED_COLUMNS` naming a `tenant` table that does not exist) is not about roles and is
left for its own clean-up issue.

Closes #18

🤖 Generated with [Claude Code](https://claude.com/claude-code)
