# PR: One site guard, and another clinic's id answers 404 (Issue 19 / M3-19)

**Milestone:** [Milestone 3: Identity, Auth, RBAC & Consent](https://github.com/Billykat7/clinicQ/milestone/3) ·
**Issue:** [#19](https://github.com/Billykat7/clinicQ/issues/19) · **Builds on:** #15–#18 (PRs #129–#132)

> **Merge order:** after #129, #130, #131 and #132; the Conventions check fails on their commits
> until then. Land with #132: **D's dashboard shell (Issue 48) needs both.**

This is non-negotiable 3 (`docs/guideline.md`): every site-scoped query goes through one helper, and
cross-site access returns **404, never 403**, so ids cannot be probed. The kernel already separated
*whose rows* (`src/core/scope.py`) from RBAC's *what*, with the grant's tier and scoped role
assignments; what it did not have was ClinicQ's shapes, a route-level guard, or anything that fails
when a query skips it. All three are here, with the first real site-scoped resource to prove them on.

## Summary

- **`src/core/site_scope.py`**, the helper, with four rules in one place:
  - which clinics a caller may reach is read from their **role assignments** (`user_roles`,
    `scope_type='site'`), never a column, a token claim or a request parameter;
  - `require_site_access(resource, verb)` answers **404 for any other site before considering a
    permission**, with the same body an unknown site id gets; a caller who *is* at the site but
    lacks the verb gets 403, because they already know the site exists;
  - the verb is then resolved with **the roles held at that site**, so a manager at Clinic B is a
    receptionist at Clinic A and nothing more;
  - every read of a site-scoped row is built here: `scoped_select`, `get_in_site_or_404`,
    `staff_at_site`, `roles_held_at_site`.
- **The platform-admin hatch is explicit, read-only and audited:** a grant that reaches `business`
  opens another clinic only with `X-ClinicQ-Cross-Site-Reason`, and writes one audit row per
  request naming the site, the path and the reason. Without the header: 404. A cross-site write:
  refused.
- **The resolver learns ClinicQ's shapes:** `ScopeShape.SITE` and `ScopeShape.QUEUE` on the `sites`
  and `queues` manifests, resolved into `ScopeNarrowing.site_ids` and `.queue_ids` (a nurse's
  `own`-tier call-next grant reaches the queues assigned to them: `AssignmentScopeType.QUEUE`).
- **A staff module** (`GET /sites/{site_id}/staff`, `…/staff/{user_id}`): the first site-scoped
  resource, and what the suites probe. Issue 22 adds invitations to it.
- **Two guards.** `test_site_scoped_queries.py` fails the build when a query on a site-scoped model
  is built outside the helper — the model set is **discovered** from the mappers, so a model that
  gains `site_id` is covered with no edit. `test_cross_tenant.py` probes the rule over HTTP and
  **fails when a site-scoped resource has no case in it** and no pending entry naming its issue.

## Design notes

**404 before 403, and the same bytes as "no such id".** The order matters: membership is decided
first, so a permission refusal can never be used to learn that a clinic exists. The body is
`{"detail": "Not found.", "code": "http.not_found"}` for a real other-clinic id and for an id that
never existed, which the suite asserts by comparing the two responses (minus the request id), and
again across ten near-miss ids.

**The hatch is a header, not a role name.** "Explicit, audited and never implicit" rules out
`platform_admin` simply seeing everything. The condition is the *grant's* tier (`business`), plus a
safe method, plus a written reason; the audit row is written and committed before the read returns,
so a read that answers 200 has always left a trace. A cross-site write is refused even with a
reason: the operator who must write is assigned to the clinic, which is itself a recorded act.

**Why a staff module now.** The issue's criterion names ticket, queue, staff and report endpoints;
of those only staff can exist in M3 (queues are Issue 25, tickets Issue 39, reports M12). Staff are
site-scoped through assignments rather than a column, which makes them the awkward case, so proving
the guard on them is worth more than proving it on the easy one. The other four are in the suite's
`PENDING` map with the issue that will bring them, and a test fails the moment one of those
resources gates a route without a case.

**Where the nurse's queue lands.** `permitted_queue_ids` and the `QUEUE` narrowing exist here, so
Issue 28's room assignment writes `user_roles(scope_type='queue')` rows and Issue 42's call-next
reads them through the same helper. "A nurse cannot call next on a queue they are not assigned to"
is the grant from #18 plus this narrowing plus that route.

**Out of scope:** which queues a nurse may call within their site (Issue 28), the audit trail's own
site column and read API (Issue 20).

## Changes

- **New:** `src/core/site_scope.py`; `src/modules/staff/` (`service`, `router`, `schemas`);
  `tests/unit/security/test_site_scoped_queries.py`; `tests/integration/security/test_cross_tenant.py`.
- **`src/core/scope.py`:** `assigned_scope_ids`; `ScopeNarrowing.site_ids` / `.queue_ids`;
  the module docstring now describes ClinicQ's shapes rather than the property project's.
- **`src/commons/enums.py`:** `AssignmentScopeType.QUEUE`; `ScopeShape.SITE` / `QUEUE`;
  `AuditEntityType.SITE`; `BoundedContext.STAFF`.
- **Manifests:** `sites` declares `scope_shape=SITE`, `queues` declares `QUEUE`.
- **`scripts/db/seed_dev_data.py`:** every demo staff member but the platform admin is assigned to
  the first demo clinic (`hillbrow-chc`), so the site routes work locally; the operator is assigned
  to none, on purpose.
- **`docs/guideline.md`:** non-negotiable 3 now names the helper, the four rules and both guards.

## Testing

- [x] `ruff check`, `ruff format --check`, `mypy src/` (179 files) clean.
- [x] `make test`: **1130 passed**, 17 skipped, 9 xfailed. PostgreSQL and Redis: **17 passed**.
- [x] **The query guard fails on real code.** Replacing `staff_at_site(access)` with `select(User)`
      in the staff service:
      `src/modules/staff/service.py:49 builds select(User) outside the site guard; use
      src.core.site_scope (scoped_select / get_in_site_or_404 / staff_at_site)`.
      Restored, it passes; the file also carries fixtures for the ungated router shape and the same
      query written through the helper. It caught one real mistake while this PR was being written:
      the staff service's own role lookup, which is why `roles_held_at_site` now lives in the guard.
- [x] **How to verify, on a real server** (PostgreSQL, `make seed-dev-data`, the demo staff at
      `hillbrow-chc`, the clinic manager additionally a receptionist at `mandela-sisulu-clinic`):

```text
A receptionist at Hillbrow CHC:
  GET /sites/hillbrow-chc/staff                     -> 200
      3 colleagues: manager (clinic_manager), nurse (nurse_doctor), reception (receptionist)
  GET /sites/mandela-sisulu-clinic/staff            -> 404   (another clinic)
  GET /sites/does-not-exist/staff                   -> 404   (no such clinic)
      bodies identical: True  {'detail': 'Not found.', 'code': 'http.not_found'}
  GET /sites/mandela-sisulu-clinic/staff/<real id>  -> 404

The platform admin, assigned to no clinic:
  GET /sites/hillbrow-chc/staff, no reason header   -> 404
  GET /sites/hillbrow-chc/staff, with a reason      -> 200
  audit_event:
    platform-admin@clinicq.example | read | site | hillbrow-chc |
    cross-site read of /api/v1/sites/hillbrow-chc/staff: support ticket 4821
```

  Note the manager row in the first list: they hold `clinic_manager` at Hillbrow and
  `receptionist` at Mandela Sisulu, and the list shows only the role they hold **here**.

- [ ] Not shown: a screenshot. No template, stylesheet or script changed.

## Acceptance criteria

- [x] A Clinic A staff token receives 404 for every Clinic B resource id: shown above and in
      `test_cross_tenant.py` for every route the staff resource has.
- [x] Ticket, queue, staff and report endpoints are all covered by the cross-tenant suite:
      **staff is covered now**; ticket, queue and report endpoints do not exist yet (Issues 39, 25,
      M12) and are in the suite's `PENDING` map, which fails the build the moment one of them gates
      a route without a case. That is the honest state of this criterion in M3.
- [x] No router constructs a query on a site-scoped model without the helper, enforced by a guard
      test (demonstrated failing above).
- [x] Platform-admin cross-site reads write an audit row every time (shown; asserted for a second
      read too).
- [x] Enumerating sequential ids leaks no information about another site: identical bodies for a
      real other-clinic id, a near-miss id and an unknown one.
- [x] The guard is documented as a project non-negotiable in `docs/guideline.md` (rule 3, now with
      the helper's rules and both guards).

## Risk and rollback

No migration and no new setting. The only new routes are the two staff reads. **Existing staff
accounts with no site-scoped assignment reach no site route** (404 everywhere) until they are
assigned: that is the guard working, and `make seed-dev-data` now assigns the demo accounts. The
`X-ClinicQ-Cross-Site-Reason` header does nothing for anyone whose grant does not reach `business`.
Rollback is a revert.

**Follow-ups noticed:** site ids are clinic slugs until Issue 23 gives sites real ids, so the
assignments the dev seed writes (and any written by hand before then) need rewriting when that
lands — worth a line in Issue 23. `require_site_access` reads `{site_id}` from the path, so a future
route that takes the site in the body or a query parameter needs its own small variant.

Closes #19

🤖 Generated with [Claude Code](https://claude.com/claude-code)
