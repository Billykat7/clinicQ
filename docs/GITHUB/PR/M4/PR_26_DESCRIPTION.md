# PR: A clinic lists what it offers, and how long each thing takes (Issue 26 / M4-26)

**Milestone:** [Milestone 4: Clinics, Queues & Configuration](https://github.com/Billykat7/clinicQ/milestone/4) ·
**Issue:** [#26](https://github.com/Billykat7/clinicQ/issues/26) · **Builds on:** #25 (PR #140), and #23/#24/#27 beneath it

> **Merge order:** after #23, #24, #25 and #27. This branch is stacked on them, so the Conventions
> check fails on their commits until they merge. Every test job passes.

Wait estimates in M6 need a starting number before a clinic has any history. A small catalogue with
an expected duration per service gives the estimator a sane seed on a clinic's first morning, and
gives discovery something to say about a clinic beyond a queue length.

## Summary

- **`clinic_service` (migration `0011`):** site, name, slug, category, description, expected
  minutes, display order, `requires_appointment`, active and soft-delete flags. Name and slug are
  unique **per site**, like a queue's.
- **The model is `ClinicService` in `clinic_service.py`, not `Service`.** Every module here has a
  `service.py` holding its persistence layer, so `from … import service` and `from … import
  Service` would sit one letter apart meaning entirely different things.
- **`queue_clinic_service`** links a queue to the services it handles — a plain association table,
  not a mapped model (see *Design notes*).
- **Expected minutes are validated to 1..240.** They are the wait estimator's prior (Issue 42), so
  zero would make an estimate divide by nothing and a value in hours would report a wait in days.
  `expected_minutes_prior()` is the function Issue 42 calls, with the queue's own pace as a
  fallback.
- **Deactivating a service removes it from new joins and never from history**, held in the base
  query the same way the queues module holds it, with the reason written next to it.
- **A new clinic gets a catalogue it can edit:** six primary-care services (consultation, chronic
  medication collection, immunisation, antenatal, HIV and TB, screening) with the minutes the demo
  dataset uses. Seeded for all eleven demo clinics.

## Design notes

**Why the link table is not a model.** `queue_clinic_service` carries nothing but its two foreign
keys, and both ends are already site-scoped by their parents. Making it a mapped class would add a
row to the tenancy guard's discovered surface — a model with a `site_id` it does not need, or a
model without one that the guard then has to be taught about — for no gain. As a Core `Table` it is
invisible to both guards and correct by construction.

**Another clinic's queue id is dropped, not refused.** A service may name the queues that handle it;
if the payload contains a queue belonging to somebody else, `_queues_for` simply does not find it.
Refusing would turn a stale entry in a browser's list into an error a manager cannot act on, and —
more to the point — *accepting* it would be a cross-tenant link. Narrowing silently is the only
option that is both usable and safe, and there is a test that the id really is dropped rather than
quietly stored.

**`seed_default_catalogue` takes a bare `site_id`, and that is a named exception in the site-scope
guard** — the same shape, and the same reason, as `create_default_queues` in Issue 25: onboarding
writes a clinic's catalogue before anyone holds a role at it. The entry names the function, not the
file, and the function refuses to do anything if the clinic already has a service.

**The seed data lives next to the code that writes it.** The issue says F prepares the catalogue in
sprint 3; it is six services and their durations, and putting it in `DEFAULT_CATALOGUE` in
`catalogue.py` rather than a fixture file means it can be reviewed in a diff, typed against the
`ServiceCategory` enum, and changed in one place. The categories are what a district report (M12)
will group by across clinics that each name the same service differently.

**`requires_appointment` is stored and means nothing yet.** Appointment slots are Issue 80. It is
here because it is part of what a clinic says about a service, and adding the column now costs
nothing while adding it to a populated table later costs a migration — the same reasoning as the
PostGIS column in Issue 23. Named in the follow-ups so it is not mistaken for working.

**Out of scope:** the wait estimator itself (Issue 42), which reads `expected_minutes`; and the
clinic detail page (Issue 35), which displays the list.

## Changes

- **`alembic/versions/0011_clinic_services.py`** (new),
  **`src/database/models/clinic_service.py`** (new): the model, the link table, and the
  1..240-minute bounds as constants the schema reads.
- **`src/modules/sites/catalogue.py`** (new): the queries, the rules, `DEFAULT_CATALOGUE`,
  `seed_default_catalogue` and `expected_minutes_prior`.
- **`src/modules/sites/router.py`:** four routes under `/sites/{site_id}/services`, all behind the
  site guard; `_audit_entity`, a small helper for auditing something *inside* a clinic rather than
  the clinic itself. **`schemas.py`:** three models.
- **`src/commons/enums.py`:** `ServiceCategory` (eight members, each with what it covers) and
  `AuditEntityType.CLINIC_SERVICE`.
- **`scripts/db/seed_dev_data.py`:** `seed_services`, idempotent by clinic.
- **`tests/`:** `integration/sites/test_services_catalogue_api.py` (12 cases); the `clinicservice`
  cross-tenant case and the `seed_default_catalogue` site-scope exception, each with its reason.

## Testing

- [x] `ruff check` / `ruff format --check` clean; `mypy src/` clean (204 files).
- [x] `make test`: **1379 passed**, 27 skipped, 9 xfailed.
- [x] `make test-postgres`: **25 passed**, including migration `0011` down and up and `alembic
      check` finding no drift.
- [x] **How to verify, end to end on a real server.** A throwaway database on PostgreSQL 18 +
      PostGIS 3.6, migrated and seeded, then driven through the real app. Transcript, verbatim:

```text
  python -m scripts.db.seed_dev_data
    Clinics: 11 · Opening hours: 11 · Queues: 11 · Services: 11 · Holidays: 27 · Staff: 4

  the seeded catalogue (66 rows across 11 clinics):
    General consultation            consultation    15 min
    Chronic medication collection   chronic          5 min
    Immunisation                    child_health     6 min
    Antenatal care                  maternal        25 min
    HIV and TB services             hiv_tb          20 min
    Health screening                screening       10 min

  PUT  link to the 'General consultation' queue -> 200  queue_ids set, minutes 18
  POST expected_minutes = 600      -> 422  Input should be less than or equal to 240
  POST expected_minutes = 0        -> 422  Input should be greater than or equal to 1
  POST a name the clinic uses      -> 409  This clinic already offers 'General consultation'.
  POST (receptionist)              -> 403
  GET  (receptionist)              -> 200
  POST linking another clinic's queue -> 201  queue_ids: []   ← dropped, never linked

  deactivation keeps history:
    DELETE /services/{id}          -> 200  is_active: False
    new joins see 6, the manager sees 7
    in the database                -> is_deleted: False, and still linked to its queue (1 row)

  another clinic:  GET /sites/<B>/services -> 404

  audit trail:
    update | manager@clinicq.example | updated 'General consultation'
    create | manager@clinicq.example | added 'Dressings' (15 min)
    update | manager@clinicq.example | deactivated 'General consultation'; past tickets still name it
```

- [ ] Screenshot: no UI in this PR. The catalogue screen is Issue 54 and the clinic detail page is
      Issue 35.

## Acceptance criteria

- [x] **A new clinic gets a seeded services catalogue it can edit.** Six entries; the test seeds a
      clinic, lists them, and edits one through the API.
- [x] **The wait estimator falls back to the service's expected minutes when fewer than N samples
      exist.** *Partly (Issue 26).* This PR provides the number and the function that reads it
      (`expected_minutes_prior`, with the queue's own pace as the fallback when a ticket names no
      service), tested both ways. The "fewer than N samples" rule is the estimator's own and lands
      with Issue 42 — there are no samples to count until tickets exist.
- [x] **Services appear on the clinic detail page and in the channel menus.** *Partly (Issue 26).*
      The API that both read is here, with `include_inactive=false` for exactly that purpose; the
      detail page is Issue 35 and the channel menus are M10. Said plainly because the rendering
      half is in other milestones.
- [x] **Expected minutes are validated to a sensible range.** 1..240, refused at 0 and 600 and
      accepted at the ceiling, so the refusal is about the bound and not an off-by-one.
- [x] **Deactivating a service removes it from new joins but not from history.** Both halves:
      absent from `include_inactive=false`, still in the manager's list, `is_deleted` false, and
      its queue link intact.
- [x] **The catalogue is site-scoped like every other clinic resource.** Every query through
      `scoped_select`, another clinic's id a 404, and a case in the cross-tenant suite.

## Risk and rollback

**Migration `0011`** adds two tables and changes nothing existing, so the previous release runs
unchanged on the new schema and the migration is reversible (proven by the round-trip test). One
behaviour change: `make seed-dev-data` now also writes a catalogue per clinic, skipping any clinic
that already has one.

**Follow-ups noticed:** `requires_appointment` is stored but nothing enforces it until Issue 80;
and a service's `expected_minutes` and its queue's `expected_service_minutes` are two numbers that
can disagree about the same line, which Issue 42 will have to pick between — worth deciding there
rather than guessing here.

Closes #26
