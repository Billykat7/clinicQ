# PR: Staff are linked to the clinics and the rooms they work in (Issue 28 / M4-28)

**Milestone:** [Milestone 4: Clinics, Queues & Configuration](https://github.com/Billykat7/clinicQ/milestone/4) ·
**Issue:** [#28](https://github.com/Billykat7/clinicQ/issues/28) · **Builds on:** #22 (invitations), #25 (PR #140), and #23/#24/#26/#27 beneath it

> **Merge order:** after #23, #24, #25, #27 and #26. This branch is stacked on them, so the
> Conventions check fails on their commits until they merge. Every test job passes.

A nurse signed into Room 2 should see Room 2. This links staff to the clinics and to the specific
queues they work, which is what makes M7's nurse view meaningful and Issue 18's RBAC check
enforceable at the row level.

## Summary

- **Site membership reuses the kernel's scoped role assignment** (`user_roles` with
  `scope_type='site'`) — the same record Issue 19's guard resolves by. **There is no
  `staff_site_assignments` table**, deliberately (see *Design notes*).
- **`staff_queue_assignment` (migration `0012`)** is the new part: staff, clinic, queue, active,
  who assigned them. Room membership.
- **One writer keeps two records in step.** `src/modules/staff/assignments.py` writes both the
  assignment row *and* the kernel's queue-scoped `user_roles` row in one transaction, because
  `permitted_queue_ids` — and therefore a nurse's `own`-tier call-next grant — resolves from the
  latter. A test asserts they always match.
- **Removal takes effect on the next request**, proven with an access token minted *before* the
  change, still signed and unexpired, used *after* it: 200 before, 404 after, with no sign-out.
- **A clinic always keeps at least one clinic manager.** Removing the last one is a **409** with a
  message that says what to do instead; removing the second-to-last is fine, so the check is
  discriminating rather than "never remove a manager".
- **Every change is audited** with the acting manager and the clinic, and the room assignments are
  in the cross-tenant suite.

## Design notes

**Why there is no `staff_site_assignments` table**, even though the issue's *Files touched* lists
one. A staff member's clinic is already a role held **at** a site (`user_roles` with
`scope_type='site'`, Issue 15) — that is the record the site guard reads on every request, the one
the invitation flow writes, and the one the seed writes. A second table answering "who works here"
would be a second answer, and the failure mode of two answers is that authorization uses one and the
screen shows the other. Room membership is the part that genuinely does not exist, and that is the
table this PR adds.

**Why the room assignment is two records, and why that is not duplication.**
`staff_queue_assignment` is the *clinic's* record: who put whom on which room, when, and it keeps
the row when somebody comes off so "who was on Room 2 last Tuesday" stays answerable. The kernel's
`user_roles(scope_type='queue')` row is what the **scope resolver** reads — `permitted_queue_ids`,
which turns a nurse's `own` tier into a set of queue ids. Writing only the first leaves the resolver
blind; writing only the second loses the trail and the "who assigned them". So both, in one
transaction, in one function, with `test_the_assignment_row_and_the_kernels_queue_scope_stay_in_step`
asserting they never diverge.

**"Takes effect on the next request" is a property of where the data lives, not of a revocation
step.** Nothing about which clinics or queues somebody reaches is a claim in their token: it is read
from `user_roles` on every request. That is why the test can mint a token, prove it works, remove
the role, and prove the *same token* now 404s — and why there is no cache to invalidate.

**The room set is replaced whole, not edited one room at a time.** "Which rooms does this nurse
work" is one decision; a per-room edit is how somebody ends up still on a room nobody meant to leave
them on. Losing your last role at a clinic also clears your rooms there, because leaving them would
put you back on Room 2 the day you are re-added.

**Another clinic's queue id is refused (404), not dropped.** This is the opposite of Issue 26's
catalogue, and the difference is deliberate: a service's queue links come from a multi-select where
a stale entry is noise, while an assignment names a person and a room by hand, so a manager who gets
it wrong should be told.

**Two named exceptions in the site-scope guard, both about `user_roles`.** That table has no
`site_id` column — the clinic *is* its `scope_id` — so there is no `scoped_select` to route through;
`_assignments_at` applies exactly the filter `roles_held_at_site` applies for reads. And
`_sync_queue_scoped_roles` reads a person's queue-scoped rows **across clinics on purpose**: the
same person may work elsewhere, and only this clinic's rows are ours to remove. Both entries name
the function, with the reason, so the next query in the same file is still a finding.

**"A nurse assigned to Room 2 cannot call next on Room 3" is asserted at the row level**, because
the call-next route is Issue 42. `may_work_queue()` is the check that route will make, and the
grant's *tier* decides whether it is asked at all: a receptionist holds `queues.call` at `assigned`
and reaches every queue at their clinic; a nurse holds it at `own`, which is exactly this question.

**Out of scope:** the dashboard site switcher (Issue 48 renders what this stores), invitations
(Issue 22), and call-next itself (Issue 42).

## Changes

- **`alembic/versions/0012_staff_queue_assignments.py`** (new),
  **`src/database/models/staff_queue_assignment.py`** (new).
- **`src/modules/staff/assignments.py`** (new): `grant_role_at_site`, `revoke_role_at_site`,
  `managers_at`, `set_room_assignments`, `clear_room_assignments`, `_sync_queue_scoped_roles` and
  `may_work_queue`, with `INDISPENSABLE_ROLE` and `ASSIGNABLE_AT_A_SITE` as the two policy
  constants.
- **`src/modules/staff/router.py`:** four routes under `/sites/{site_id}/staff/{user_id}/…` —
  `POST`/`DELETE` `roles`, `GET`/`PUT` `queues` — all on the `sites.staff` grant that already
  gates inviting and deactivating, because deciding who works here and which room they work is one
  job. **`schemas.py`:** four models.
- **`tests/`:** `integration/sites/test_staff_assignments_api.py` (12 cases); the
  `staffqueueassignment` cross-tenant case; the two site-scope exceptions, each with its reason.

## Testing

- [x] `ruff check` / `ruff format --check` clean; `mypy src/` clean (206 files).
- [x] `make test`: **1393 passed**, 27 skipped, 9 xfailed.
- [x] `make test-postgres`: **25 passed**, including migration `0012` down and up and `alembic
      check` finding no drift.
- [x] **How to verify, end to end on a real server.** A throwaway database on PostgreSQL 18 +
      PostGIS 3.6, migrated and seeded, then driven through the real app. Transcript, verbatim:

```text
  rooms:
    PUT  the nurse onto 'Triage'          -> 200  ['Triage']
      staff_queue_assignment -> [('Triage', True)]
      user_roles(queue)      -> ['Triage']     ← what the kernel's scope resolver reads
    PUT  moved to 'General consultation'  -> 200  [('General consultation', True),
                                                   ('Triage', False)]
      the old row is kept, switched off   -> False
    PUT  another clinic's queue           -> 404
    PUT  (receptionist)                   -> 403
    GET  (receptionist)                   -> 200

  removal takes effect on the NEXT REQUEST:
    the same token, before                -> 200
    manager removes the role              -> 200
    the same token, one request on        -> 404   ← no sign-out, no token expiry

  a clinic always keeps a manager:
    a clinic granting platform_admin      -> 403  A clinic cannot grant the 'platform_admin'
                                                  role; that is the operator's to give.
    removing the last manager             -> 409  A clinic must always have at least one clinic
                                                  manager. Add another before removing this one.
    grant a second manager                -> 200  roles now: ['clinic_manager', 'nurse_doctor']
    now removing the first                -> 200  roles now: []

  audit trail:
    manager@clinicq.example | room assignment: Triage
    manager@clinicq.example | room assignment: General consultation
    manager@clinicq.example | removed the receptionist role at this clinic
    manager@clinicq.example | granted the clinic_manager role at this clinic
    manager@clinicq.example | removed the clinic_manager role at this clinic
```

- [ ] Screenshot: no UI in this PR. The assignment screen is Issue 54 and the site switcher is
      Issue 48.

## Acceptance criteria

- [x] **A nurse assigned to Room 2 cannot call next on Room 3.** `may_work_queue` answers `True`
      for Room 2 and `False` for Room 3 after the assignment. Asserted at the row level because the
      call-next route is Issue 42; the tier that decides whether the question is asked is already
      in the queues manifest.
- [x] **A staff member assigned to two sites can switch between them without signing out.** One
      account granted a role at Clinic B reads both clinics' staff lists with the same session.
- [x] **Removing an assignment takes effect on the next request, not the next sign-in.** Proven
      with a token minted before the change: 200, then 404, same token, no sign-out.
- [x] **Assignment changes appear in the audit log with the acting manager.** Five rows in the
      transcript, each with the manager, the clinic and what moved.
- [x] **A site always retains at least one clinic manager, enforced on removal.** 409 with the
      remedy in the message; and the second-to-last may go, so the check is not simply "no".
- [x] **Assignments are covered by the cross-tenant test suite.** The `staffqueueassignment` case,
      with its own paths, joins the standing suite: another clinic's colleague is a 404 for reads
      and for writes.

## Risk and rollback

**Migration `0012`** adds one table and changes nothing existing, so the previous release runs
unchanged on the new schema and the migration is reversible — with one thing worth knowing, written
in the downgrade's docstring: the kernel's queue-scoped `user_roles` rows this table shadows belong
to the kernel's schema and are **not** removed by a downgrade. Re-upgrading and re-saving an
assignment brings the two back into step.

**Follow-ups noticed:** nothing yet *reads* a room assignment at request time except
`may_work_queue`, so a wrong assignment is invisible until Issue 42 — worth a line on the nurse's
own screen (Issue 53) showing which rooms they are on; and `revoke_role_at_site` clears rooms when
somebody's **last** role at a clinic goes, but a nurse demoted to receptionist keeps their rooms,
which is probably right and is currently undecided rather than decided.

Closes #28
