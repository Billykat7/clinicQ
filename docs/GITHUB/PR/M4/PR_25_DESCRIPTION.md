# PR: A clinic runs several named queues, and the server decides who may join which (Issue 25 / M4-25)

**Milestone:** [Milestone 4: Clinics, Queues & Configuration](https://github.com/Billykat7/clinicQ/milestone/4) ·
**Issue:** [#25](https://github.com/Billykat7/clinicQ/issues/25) · **Builds on:** #23 (PR #138), #24 (PR #139)

> **Merge order:** after #23 and #24. This branch is stacked on them, so the Conventions check
> fails on their commits until they merge. Every test job passes.

A real clinic visit is triage → doctor → pharmacy, not one line. This is the other half of the
sprint-5 day-3 commitment with Issue 23: every ticket in M6 belongs to a queue, so D's board and the
queue engine both start here.

## Summary

- **`queue` (migration `0009`):** site, name, slug, kind, room label, ticket prefix, display order,
  expected service minutes, maximum daily capacity, `allows_remote_join`, active and soft-delete
  flags. **`(site_id, name)` and `(site_id, slug)` are unique per site**, not platform-wide: every
  clinic has a Triage and they are different queues.
- **Deactivation hides a queue from new joins and keeps its history.** `is_active` is cleared, the
  row stays, and the base query in the service deliberately does **not** filter on it — a join
  surface asks for `include_inactive=false`, a report never does.
- **A walk-in-only queue is enforced on the server.** `ensure_remote_join_allowed(queue, source)`
  refuses web, USSD and WhatsApp and admits a walk-in; `GET /queues/joinable?source=…` is the
  channel menu reading the **server's** answer, with the refusal travelling beside each queue.
- **Onboarding gets a sensible default set:** triage, a consulting room and the pharmacy window, in
  that order, with the pharmacy walk-in only. Idempotent, so re-running onboarding never duplicates
  a line or resurrects one a clinic deleted.
- **`QueueFactory` builds and persists the real model**, and `make seed-dev-data` writes the demo
  clinics' own queues — four at a community health centre, three at a smaller clinic, two at a
  private practice.

## Design notes

**The module is `src/modules/queues/`, not `src/modules/queue/`.** The spec names the singular; the
package that exists is the plural, because Issue 18 put the queues RBAC manifest there and
`src/core/rbac_manifest_registry.py` already imports it. Creating `queue/` beside `queues/` would
leave two near-identical packages one letter apart, which is worse than either name. The spec's
actual point — **do not park queues under sites** — is honoured: the model is
`src/database/models/queue.py`, the service is its own module, and M6's queue engine grows in the
same place. Flagging it here rather than silently deviating.

**A queue's routes hang off `/sites/{site_id}/queues`.** A queue does not exist outside a clinic,
and a `/queues/{id}` URL would invite a handler that forgot the site filter. With the site in the
path, `require_site_access` answers "whose clinic" before the verb, and another clinic's id is a
404 with the same body an id that never existed gets.

**Why deactivation rather than deletion, spelled out in the base query.** Deleting a queue would
orphan every ticket that ever pointed at it, and "how long did the pharmacy take last month" is a
question a clinic has to be able to ask about a line it has since closed. So `_live()` filters
`is_deleted` and **not** `is_active`, and the docstring says why — the bug this prevents is a future
reader "tidying up" by adding the second filter and quietly emptying last month's report.

**`allows_remote_join` cannot be enforced in a template.** A USSD session has no button to hide.
The check is a function in the service that every join path calls (Issue 40 brings the routes), and
the channel menu endpoint reports its answer rather than computing its own. The pharmacy is walk-in
only in the default set and in the seed, for a reason worth stating: you cannot collect medicine
from a phone, so a remote ticket for it puts somebody in a line they cannot reach the front of.

**`create_default_queues` takes a bare `site_id`, and that is a named exception in the site-scope
guard.** Onboarding (Issue 29) creates a clinic and its queues *before anyone holds a role at it*,
so there is no `SiteAccess` to scope by. The guard's `_UNSCOPED_BY_DESIGN` list now carries the
entry with that reason — one function, not the file, so the next query in the same module is still
a finding. The function refuses to do anything if the clinic already has a queue, which is what
keeps the exception narrow.

**Reordering is one request over the whole list.** `PUT /queues` takes the ids in the order they
should appear. A per-queue "set position to 3" is how two queues end up sharing a position; ids that
do not belong to the clinic are ignored rather than refused, so a stale entry in a browser's list
cannot stop a manager reordering the rest.

**Expected minutes are validated to 1..240.** Zero would make the wait estimator (Issue 42) divide
by nothing, and anything above four hours is somebody typing hours into a minutes field.

**Out of scope:** tickets and the queue engine (Issue 39 onwards), which this model blocks;
assigning staff to queues (Issue 28); and the services a queue handles (Issue 26).

## Changes

- **`alembic/versions/0009_queues.py`** (new), **`src/database/models/queue.py`** (new).
- **`src/modules/queues/`**: `schemas.py`, `service.py`, `router.py` (all new), joining the
  `rbac_manifest.py` Issue 18 left there. Eight routes, all behind the site guard.
- **`src/commons/enums.py`:** `QueueKind`, `BoundedContext.QUEUES`, `AuditEntityType.QUEUE`.
- **`src/api/v1/router.py`:** two lines.
- **`tests/factories.py`:** `QueueFactory` builds and persists `Queue`; `site_id` has no default,
  so a test cannot build a queue that belongs nowhere. `TicketFactory` now takes the real queue.
- **`scripts/db/seed_dev_data.py`:** `seed_queues`, from the dataset's own lines per clinic.
- **`tests/`:** `integration/sites/test_queues_api.py` (16 cases). Guard lists updated with their
  reasons: the public `/sites/queues/info`, the `queue` cross-tenant case (which replaces the
  `queues` `PENDING` entry), and `create_default_queues` in the site-scope exception list.

## Testing

- [x] `ruff check` / `ruff format --check` clean; `mypy src/` clean (201 files).
- [x] `make test`: **1339 passed**, 27 skipped, 9 xfailed.
- [x] `make test-postgres`: **25 passed**, including migration `0009` down and up, `alembic check`
      finding no drift, and the naming convention holding for the new table's two unique
      constraints.
- [x] **How to verify, end to end on a real server.** A throwaway database on PostgreSQL 18 +
      PostGIS 3.6, migrated and seeded, then driven through the real app. Transcript, verbatim:

```text
  python -m scripts.db.seed_dev_data
    Clinics: 11 created · Opening hours: 11 created · Queues: 11 created
    Public holidays: 27 created · Staff accounts: 4 created

  queues per clinic: hillbrow-chc 4, laudium-chc 4, stanza-bopape-chc 4, imbalenhle-chc 4,
                     glen-earle-clinic 3, mandela-sisulu-clinic 3, mofolo-south-clinic 3,
                     red-hill-clinic 3, medicross-meldene 2, medicross-randburg 2,
                     medicross-pinetown 2          ← a CHC runs four lines, a practice two

  GET  /queues                   -> 4: Triage [triage, T, remote=True]
                                      General consultation [consultation, A, remote=True]
                                      Chronic medication collection [pharmacy, C, remote=False]
                                      Immunisation [other, I, remote=True]
  POST /queues (manager)         -> 201  Doctor Room 3
  POST /queues (same name)       -> 409
  POST /queues (receptionist)    -> 403
  POST expected_minutes = 600    -> 422  Input should be less than or equal to 240
  POST expected_minutes = 0      -> 422  Input should be greater than or equal to 1
  PUT  /queues (reorder)         -> 200  Doctor Room 3, Immunisation, Chronic medication
                                         collection, General consultation, Triage

  walk-in only, decided by the server:
    source=web       pharmacy joinable=False | This queue takes walk-in patients only.
    source=ussd      pharmacy joinable=False |   Please join at the clinic's front desk.
    source=whatsapp  pharmacy joinable=False |
    source=walk_in   pharmacy joinable=True  |

  deactivation keeps history:
    DELETE /queues/{id}                 -> 200  is_active: False
    GET /queues?include_inactive=false  -> 4 queues (was 5)
    GET /queues/{id} (the manager)      -> 200  the row is still there
    in the database                     -> is_deleted: False

  another clinic:  GET /sites/<B>/queues -> 404 ·  POST /sites/<B>/queues -> 404

  audit trail:
    create | manager@clinicq.example | site_id set | added the queue 'Doctor Room 3' (consultation)
    update | manager@clinicq.example | site_id set | reordered 5 queue(s)
    update | manager@clinicq.example | site_id set | deactivated 'Doctor Room 3'; its tickets are kept
```

- [ ] Screenshot: no UI in this PR. The manager's queue screen is Issue 54 and the board is Issue 56.

## Acceptance criteria

- [x] **A site can carry any number of named queues, ordered for display.** Three added through the
      API and listed in the clinic's order; the demo clinics carry two to four each.
- [x] **Deactivating a queue hides it from joins while keeping its history queryable.** Both halves
      tested: gone from `include_inactive=false`, still resolvable by id, `is_deleted` false in the
      database. Yesterday's tickets are read through the **agreed ticket fixture** rather than the
      `tickets` table, which arrives with Issue 39 — when it lands, that assertion becomes a query
      against the real table and does not otherwise change. Stated plainly because the issue asks
      for the real query "once #39 exists, or a fixture until then".
- [x] **A newly onboarded clinic starts with a sensible default queue set.** Triage, a consulting
      room and the pharmacy, in order, with the pharmacy walk-in only; and re-running it creates
      nothing.
- [x] **A queue that disallows remote joins accepts walk-ins only, enforced server-side.**
      `ensure_remote_join_allowed` refuses each of the three remote sources and admits the walk-in;
      the channel menu reports the server's answer with its reason.
- [x] **Queue names are unique per site.** A second Triage at the same clinic is a 409; one at
      another clinic is a 201.
- [x] **Queue factories exist for the M6 and M7 test suites.** `QueueFactory.create(db,
      site_id=…)` persists the real model, and `TicketFactory.build_batch(n, queue=…)` already
      takes it.

## Risk and rollback

**Migration `0009`** adds one table and changes nothing existing, so the previous release runs
unchanged on the new schema and the migration is reversible (proven by the round-trip test). One
behaviour change: `make seed-dev-data` now also writes queues, skipping any clinic that already has
one so a manager's edits are never overwritten.

**Follow-ups noticed:** `max_daily_capacity` is stored and validated but nothing enforces it yet —
the check belongs with the join service (Issue 40), and there is no test that can assert it until
tickets exist; and `ticket_prefix` is unique by convention rather than by constraint, so two queues
at one clinic can share a letter, which would make two ticket numbers look alike on the board
(worth a per-site unique constraint when Issue 39 makes it observable).

Closes #25
