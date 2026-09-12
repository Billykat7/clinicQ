# PR: The audit trail gets its clinic, its request, and a read API per clinic (Issue 20 / M3-20)

**Milestone:** [Milestone 3: Identity, Auth, RBAC & Consent](https://github.com/Billykat7/clinicQ/milestone/3) ·
**Issue:** [#20](https://github.com/Billykat7/clinicQ/issues/20) · **Builds on:** #15–#19 (PRs #129–#133)

> **Merge order:** after #129–#133; the Conventions check fails on their commits until then.

Most of the trail was built: the `audit_event` table with an append-only trigger, `src/core/audit.py`
(snapshot, diff, redaction) and a search API that audits its own reads. Checking it turned up the two
gaps the issue names — no `site_id`, no `request_id` — and one it does not:

- **`TRUNCATE` was not append-only.** The baseline's trigger is `BEFORE UPDATE OR DELETE … FOR EACH
  ROW`, and a row-level trigger never fires for `TRUNCATE`, so one statement could have emptied an
  append-only table. Migration `0004` adds the statement-level guard.

## Summary

- **Migration `0004`:** `site_id`, `request_id` and `actor_role` on `audit_event` (all nullable, so
  the previous release keeps inserting on the new schema), two indexes, and the `TRUNCATE` guard.
- **Filled in automatically:** `record_audit_event` takes the request's id and the site the guard
  bound (Issue 19) from the request context, so a row joins to that request's log lines and to its
  clinic without every call site remembering to pass them.
- **A read per audience.** `GET /sites/{site_id}/audit/events` is one clinic's trail through the site
  guard (404 for any other clinic; a clinic manager reaches it through their grant on `sites`, a
  receptionist does not). `GET /audit/events` reads every clinic behind a `business`-tier grant on a
  new `audit` resource, held by `platform_admin` and the kernel `admin`. Both filter by entity,
  actor, action and date range, and **both audit their own read**.
- **Personal data minimised:** `AUDIT_REDACTED_FIELDS` now covers phone numbers, names, WhatsApp
  ids, dates of birth, free-text notes and device details, so a diff shows *that* a patient's
  contact details changed and never what they are.
- **A guard for unaudited mutations:** a state-changing route in a ClinicQ module that records
  nothing fails the build unless it is listed with a reason.

## Design notes

**Two resources, not one, for reading.** `sites.audit` is a clinic's own trail — a clinic manager
already holds it through their `sites` grant, and the site guard keeps it to their clinic. `audit`
is the platform-wide trail, which is a different privilege: it reads every clinic at once. Keeping
them apart is what lets the per-clinic read exist at all without handing a manager the whole
platform. The platform-wide route used to be gated on `logs` (the kernel's log-viewer resource);
that grouped "may read the server logs" with "may read every clinic's audit trail", which are not
the same permission.

**Why `site_id` is nullable, and why nothing is backfilled.** A platform-level action (an RBAC
change, a patient acting on their own record) belongs to no clinic; only a whole-platform reader
sees those rows. Existing rows keep NULL: the value cannot be invented for a request that is long
over, and a backfill is exactly the `UPDATE` the trigger exists to refuse.

**The audit row's site comes from the guard, not the caller.** `require_site_access` binds the site
onto the request context (Issue 19), and `record_audit_event` reads it there, so a row cannot be
attributed to a clinic the caller never passed the guard for.

**What "exactly one audit row" can be checked by.** A test cannot assert the *count* for routes
that do not exist yet, so the guard asserts the *shape*: every mutating route in a ClinicQ module
records an audit event, or calls a service function that does, or is listed with a reason (three
are: two patient sign-in steps and the sign-out, none of which change a record). When the priority
reorder (Issue 46), the display-mode change (Issue 27) and the no-show (Issue 43) land, they are
covered the day their route appears.

**Found while testing:** the site-scoped staff fixture also wrote an *unscoped* role assignment, so
a manager at one clinic resolved as a manager everywhere — the exact hole Issue 19 exists to close,
hiding in the test factory. The factory and the dev seed now write one assignment, held at the
clinic; a test asserts it.

**Out of scope:** hash-chaining the log (Issue 99), retention and purge (Issue 95).

## Changes

- **`alembic/versions/0004_audit_site_request.py`** (new), **`src/database/models/audit_event.py`.**
- **`src/core/audit.py`:** `site_id` / `actor_role` arguments and the request-context fill.
- **`src/commons/enums.py`:** the personal fields in `AUDIT_REDACTED_FIELDS`.
- **`src/modules/audit/`:** `service.search_audit_events` takes the site access and date range;
  `audit_trail_for` too; `router.py` gains the per-clinic route and a shared filter/audit helper;
  new `rbac_manifest.py` (the `audit` resource and its two grants); `sites.audit` in the sites
  manifest.
- **`src/core/site_scope.py`:** `select_in_scope(model, access)` — the one way a query reads across
  clinics, so "platform-wide" is a visible argument rather than a missing filter.
- **`tests/`:** `integration/security/test_audit_log.py` (new, 10 cases incl. 3 PostgreSQL),
  `unit/security/test_mutations_are_audited.py` (new), the redaction case in
  `unit/security/test_audit_core.py`, the audit case in the cross-tenant suite, and the factory and
  seed assertions for one assignment per staff member.
- **Regenerated:** `tests/snapshots/rbac_decisions.txt` (+56 lines) and
  `docs/architecture/rbac-matrix.md` (+2 rows). Every line is the two new resources against the
  seven roles: `audit` allows `platform_admin` and `admin` read (and denies the other five);
  `sites.audit` allows `clinic_manager` (read/create/update, through `sites`) and `platform_admin`
  (all four); everything else denies. No existing decision moved.

## Testing

- [x] `ruff check`, `ruff format --check`, `mypy src/` (180 files) clean.
- [x] `make test`: **1143 passed**, 20 skipped, 9 xfailed. PostgreSQL and Redis: **20 passed**
      (the three new trigger tests included).
- [x] **How to verify, on a real server** (PostgreSQL migrated to `0004`, `make seed-dev-data`; the
      clinic manager is additionally a receptionist at a second clinic):

```text
$ \d clinicq.audit_event   → … site_id | request_id | actor_role  (all nullable)
  triggers: trg_audit_event_append_only, trg_audit_event_no_truncate

  manager, own clinic        GET /sites/hillbrow-chc/audit/events           -> 200
  manager, the clinic where they are only a receptionist                    -> 403
  receptionist, own clinic                                                  -> 403
  operator, platform-wide    GET /audit/events                              -> 200
  operator, one clinic, no reason header                                    -> 404
  the manager read had x-request-id: 01a09292-6877-7346-9c3e-2e061d70e351

  actor                          | action | entity_type | site_id      | request_id
  platform-admin@clinicq.example | read   | audit_log   |              | 01a09292-693c
  manager@clinicq.example        | read   | audit_log   | hillbrow-chc | 01a09292-6877
                                                                         ↑ the same request

$ psql:
  UPDATE clinicq.audit_event SET action='x'  -> ERROR: audit_event is append-only: UPDATE is not permitted
  DELETE FROM clinicq.audit_event            -> ERROR: audit_event is append-only: DELETE is not permitted
  TRUNCATE clinicq.audit_event               -> ERROR: audit_event is append-only: TRUNCATE is not permitted
  2 rows still there
```

- [ ] Not shown: a screenshot. No template, stylesheet or script changed (the audit viewer UI is
      Issue 99).

## Acceptance criteria

- [x] Every state-changing action writes exactly one audit row: enforced as a shape by
      `test_mutations_are_audited.py` (a mutating ClinicQ route that records nothing fails the
      build, with three listed exceptions that change no record), and by the routes that exist
      today writing one row each.
- [x] Updating or deleting an audit row fails at the database level — and truncating it, which this
      PR adds. Shown in `psql` above and tested against PostgreSQL.
- [x] The read API filters by site and is itself site-scoped: the per-clinic route is behind the
      site guard, the platform-wide route takes `site_id`, and both write a READ row.
- [x] Audit rows carry the request id so they join to application logs: shown above against the
      response's `X-Request-ID`.
- [x] Personal data in the before/after summary is minimised to what the audit needs: field names
      and non-personal values stay, personal values are `<redacted>`; tested on a patient diff.
- [ ] **A test proves a priority reorder, a display-mode change and a no-show all appear in the
      log.** Not possible in M3: none of those routes exists (Issues 46, 27, 43; queues and tickets
      are M4 and M6). What is in place instead is the guard above, which fails the build when any
      of them lands without an audit row, and the per-clinic read that will show them. This is the
      one criterion this PR does not meet, and it is named in the issue's own dependencies.

## Risk and rollback

**Migration `0004`** adds three nullable columns, two indexes and one trigger to a table that is
append-only, so there is no data to rewrite and the previous release runs on the new schema.
Reversible. Behaviour changes: the platform-wide `GET /audit/events` now needs `audit:read` rather
than `logs:read` — the seed grants it to `platform_admin` and `admin`, so anyone who had it keeps
it, but a **custom** role granted `logs` alone loses that route and needs the new grant from
`/admin/rbac`. Diffs of personal fields are redacted from this deploy on; rows written before keep
whatever they recorded.

**Follow-ups noticed:** `actor_role` is written only where a caller passes it (the routes here do
not yet resolve the acting role); filling it centrally belongs with the dashboard's site switcher
(Issue 48), which is where "which role am I acting as" becomes a real question. Retention of audit
rows is Issue 95.

Closes #20
