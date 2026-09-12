# PR: A clinic exists, with a real position on a map (Issue 23 / M4-23)

**Milestone:** [Milestone 4: Clinics, Queues & Configuration](https://github.com/Billykat7/clinicQ/milestone/4) ·
**Issue:** [#23](https://github.com/Billykat7/clinicQ/issues/23) · **Builds on:** #3 (baseline + PostGIS), #19 (site guard) ·
**Opens M4.**

> **Merge order:** first of M4. Issues 24–30 are stacked on this branch and their Conventions
> checks stay red until it merges.

This is the row everything else in ClinicQ hangs off, so it lands on its own and first: the
sprint-5 day-3 commitment is this PR plus Issue 25, because C, D and B build against them. It
brings the `site` table with a PostGIS `geography(Point, 4326)` column and its GiST index, the
profile CRUD behind the site guard, a **server-side** geocoding proxy, and the `sites` fixtures
every other M4 issue uses.

## Summary

- **`site` (migration `0007`):** slug, name, sector, status, `geography(Point, 4326)` location,
  address, suburb, city, province, postal code, phone, notes, plus the standard active/soft-delete
  and timestamp columns. Two indexes, both created before there is a single row: **GiST on
  `location`**, and a composite on `(sector, status)` for the directory filter.
- **The GiST index is proven from the query plan, not from its existence.**
  `test_the_radius_query_plan_actually_uses_the_gist_index` reads `EXPLAIN` and asserts the index
  name appears and `Seq Scan` does not (see *Design notes* for why `enable_seqscan` is off).
- **Geocoding is the server's job.** The CSP allows `connect-src 'self'`, so a browser cannot reach
  a geocoder at all; `POST /sites/geocode` (onboarding) and `POST /sites/{site_id}/geocode` (a
  manager fixing their own address) proxy to the configured provider. It is **off by default** —
  an unconfigured deployment answers `503` with the reason and the operator types the coordinate in.
- **A location is validated against South Africa's bounding box.** `0, 0`, a swapped pair and a
  dropped minus sign are each refused with a message that names the numbers read.
- **Sector, status and province are enums** (`SiteSector`, new `SiteStatus`, new `SaProvince`),
  never free strings. `status` is not settable by a client: a clinic becomes visible through the
  verification workflow (Issue 29), never by asking to.
- **`SiteFactory` builds and persists the real model**, replacing Issue 8's `SiteStub`, and
  `make seed-dev-data` now writes the eleven demo clinics — so a seeded receptionist's role
  assignment finally points at a `site` row that exists.

## Design notes

**Why a `TypeDecorator` instead of GeoAlchemy2's `Geography` directly.** Two reasons, both
practical. The test suite runs on SQLite (`tests/conftest.py`), which has no PostGIS and no
SpatiaLite, so a bare `Geography` column makes `Base.metadata.create_all` fail before a single test
runs; `PointGeography.load_dialect_impl` renders the real geography type on PostgreSQL and plain
text elsewhere, and a `Coordinates` value round-trips identically on both. And GeoAlchemy2 hangs DDL
listeners on spatial columns to manage indexes of its own — wrapping the type keeps the GiST index
*this project's*, named by this project's convention and visible in the migration diff.

GeoAlchemy2 does unwrap a `TypeDecorator` (`_check_spatial_type` calls `load_dialect_impl`) and then
reads `spatial_index` straight off the decorator, where `TypeDecorator.__getattr__` proxies it to
`Text` and raises `AttributeError`. That was a real failure against PostgreSQL, not a theory; the
fix is two declared class attributes on `PointGeography` saying, in one line each, that this type
manages no index.

**`enable_seqscan = off` in the EXPLAIN test, and why that is still an honest assertion.** With
eleven demo clinics a sequential scan genuinely *is* the cheaper plan and the planner is right to
pick it. What has to be proven is that the index is **usable for this predicate** — which is what a
plan produced with the sequential scan priced out shows. The setting is `SET LOCAL`, so it ends with
the transaction. A companion test compiles the service's own `within_radius_clause` and asserts it
is the predicate `EXPLAIN` was given, so the plan cannot drift away from the query the application
runs.

**The coordinate is a value object, and `POINT(lon lat)` is written in exactly one place.**
`Coordinates` validates degrees on construction, so an impossible pair cannot exist even in memory,
and latitude comes first everywhere a person reads it. `point_ewkt` in `src/database/types.py` is
the only function in the codebase that puts longitude first — which is where the swapped-pair bug
would otherwise live.

**Why the directory listing is `business`-tier and a manager reads their clinic by id.** A staff
member's role is held **at a site** (`user_roles` with `scope_type='site'`), and the kernel resolves
a scoped assignment only on a check that supplies a matching scope — so a clinic manager cannot
satisfy *any* gate on a route that names no site. Rather than widen their assignment (which would
make them a manager everywhere) or invent a second narrowing path, `GET /sites` is the operator's
directory and a manager uses `GET /sites/{site_id}`. Their "which clinics do I work at" list is the
site switcher's, built on assignments in Issue 28. The listing still applies
`site_ids_in_scope` underneath, so a future grant change cannot silently open the platform.

**`DELETE /sites/{site_id}` is deliberately *not* behind the site guard.** A platform admin is
assigned to no clinic on purpose (Issue 19), so `require_site_access` would 404 them on their own
operation. The `business`-tier gate authorises it, the delete is soft, and it is audited.

**The platform routes bind the clinic into the request context before auditing.**
`record_audit_event` reads `site_id` from the request context, which the site guard normally binds
— and creating or removing a clinic deliberately has no site guard. The first version of this PR
wrote the creation row with `site_id = NULL`, which the end-to-end run above made visible: the row
that created a clinic would have been missing from that clinic's own audit trail (Issue 20). One
`bind_request_context` call fixes it, and the API test asserts it rather than the transcript.

**`site` has no `site_id` column — it *is* the site** — so the site-scope guard's model discovery
does not pick it up. Its routes go through `require_site_access` all the same, and that is asserted
over HTTP: the new `sites.profile` case in the cross-tenant suite replaces the two `PENDING`
entries that named this issue.

**Out of scope:** opening hours (Issue 24), queues (Issue 25), the services catalogue (Issue 26),
display and privacy settings (Issue 27), the public registration form and verification (Issue 29),
and the nearby-search API (Issue 31), which will call `sites_within_radius` as it stands here.

## Changes

- **`alembic/versions/0007_sites.py`** (new), **`src/database/models/site.py`** (new).
- **`src/database/types.py`** (new): `PointGeography` and `point_ewkt`.
  **`src/commons/geo.py`** (new): `Coordinates`, the operating-area box and its refusal message.
- **`src/modules/sites/`**: `schemas.py`, `service.py`, `router.py`, `geocoding.py` (all new);
  `__init__.py` rewritten to say what to read in which order. The RBAC manifest is unchanged —
  Issue 18 declared it, and this PR adds routes under it, not permissions.
- **`src/commons/enums.py`:** `SiteStatus` (+ `SITE_DEFAULT_STATUS`,
  `SITE_PUBLICLY_VISIBLE_STATUSES`), `SaProvince`, `GeocodingProvider`, `BoundedContext.SITES`.
- **`src/core/site_scope.py`:** `site_ids_in_scope`, the list-endpoint counterpart of
  `require_site_access`. **`src/core/config.py`:** five `GEOCODING_*` settings, and `.env.example`
  regenerated (`make env-example`). **`src/api/v1/router.py`:** two lines.
- **`requirements.txt`:** `GeoAlchemy2==0.20.0` and `types-shapely==2.1.0.20260728`, each with the
  reason next to it.
- **`scripts/db/demo_dataset.py`:** the local `Province` enum is now `src.commons.enums.SaProvince`,
  re-exported under the same name so the demo data, the model and every future report group by one
  vocabulary. **`scripts/db/seed_dev_data.py`:** `seed_sites()`, and the demo staff are assigned to
  the seeded clinic's **id** rather than its slug.
- **`tests/`:** `unit/sites/` (the box, the value object, the geocoding rules — 23 cases),
  `integration/sites/conftest.py` (the two-clinic fixture the rest of M4 builds on),
  `integration/sites/test_site_profile_api.py` (15 cases over HTTP),
  `integration/sites/test_site_postgis.py` (6 cases against a real server).
  Guard lists updated with their reasons: the public `/sites/info`, the two geocode lookups in
  `NOT_AUDITED`, and the `sites.profile` cross-tenant case (whose fixture now creates real `site`
  rows). **`CONTRIBUTING.md`:** `SiteFactory` in the factory examples.

## Testing

- [x] `ruff check` / `ruff format --check` clean; `mypy src/` clean (192 files).
- [x] `make test`: **1230 passed**, 27 skipped, 9 xfailed.
- [x] `make test-postgres` against PostgreSQL 18 + PostGIS 3.6: **25 passed**, including the
      migration round-trip (`0007` down and up), `alembic check` finding no drift, and the
      constraint/index naming convention holding for the new table.
- [x] **The EXPLAIN assertion was itself tested.** With
      `op.create_index("ix_clinicq_site_location_gist", ...)` deleted from `0007`, exactly two
      tests go red — the plan test and the catalog test — and the four behavioural PostGIS tests
      still pass. So the plan test measures the index rather than the query.
- [x] **How to verify, end to end on a real server.** A throwaway database on PostgreSQL 18 +
      PostGIS 3.6, brought up by `alembic upgrade head`, then `seed_rbac`, `seed_dev_data`, and the
      calls below driven through the real app. Transcript, verbatim:

```text
  python -m scripts.db.seed_dev_data
    Clinics: 11 created, 0 updated, 0 unchanged
    Staff accounts: 4 created, 0 updated, 0 unchanged

  operator creates a clinic, body asking for status "verified"  POST /api/v1/sites
    -> 201  {'slug': 'zola-clinic', 'status': 'draft',
             'location': {'latitude': -26.2684, 'longitude': 27.8472}}
             ↑ draft, whatever the body asked for

  the same slug again                    -> 409  The slug 'zola-clinic' is already in use.
  location 0, 0                          -> 422
      Value error, 0.00000, 0.00000 is outside South Africa (latitude -35.5..-21.5,
      longitude 15.5..33.5). Check that latitude and longitude are not swapped, and
      that the latitude is negative.
  latitude and longitude swapped         -> 422
  clinic manager creates a clinic        -> 403
  manager edits their own clinic         -> 200
  receptionist edits it                  -> 403
  manager reads another clinic           -> 404 {'detail': 'Not found.', 'code': 'http.not_found'}
  manager reads an id that never existed -> 404 {'detail': 'Not found.', 'code': 'http.not_found'}
     identical bodies: True                    ← apart from the per-request id
  manager lists the whole directory      -> 403
  operator lists the whole directory     -> 200  12 clinics
  geocode with GEOCODING_PROVIDER unset  -> 503  No geocoding provider is configured
      (GEOCODING_PROVIDER). Enter the clinic's coordinate manually.

$ psql:
  indexes on clinicq.site
     ix_clinicq_site_location_gist | gist (location)
     ix_clinicq_site_sector_status | btree (sector, status)
     pk_site | btree (id)
     uq_site_slug | btree (slug)
  location column: geography(Point,4326)

  SELECT slug, round(ST_Distance(location, ST_GeogFromText('SRID=4326;POINT(28.04 -26.205)')))
    FROM clinicq.site
   WHERE ST_DWithin(location, ST_GeogFromText('SRID=4326;POINT(28.04 -26.205)'), 2000);
     hillbrow-chc | 1379 m

  SET LOCAL enable_seqscan = off; EXPLAIN <the same query>;
     Index Scan using ix_clinicq_site_location_gist on site  (cost=0.26..20.78 rows=1 width=90)
       Index Cond: (location && _st_expand('0101...3AC0'::geography, '2000'::double precision))
       Filter: st_dwithin(location, '0101...3AC0'::geography, '2000'::double precision, true)

  audit trail:
    create | site | platform-admin@clinicq.example | 01a095c1-d25d-… | created clinic zola-clinic (draft)
    update | site | manager@clinicq.example        | 01a095c1-c659-… | updated the profile of hillbrow-chc
                                                     ↑ site_id, so Issue 20's per-clinic trail finds both
```

- [ ] Screenshot: no UI in this PR. The manager settings screen is Issue 54; the public
      registration form is Issue 29.

## Acceptance criteria

- [x] **A site persists with a real coordinate and is returned by a `ST_DWithin` query.** Shown
      above and asserted in `test_site_postgis.py`, with the distance in metres on the spheroid
      (widening the radius from 2 km to 30 km adds Orlando West, 20 km out).
- [x] **The GiST index is present in the migration and used by the query plan.** Created by `0007`
      before any row exists; the plan is read with `EXPLAIN` and asserted to name the index, and a
      companion test proves the predicate under `EXPLAIN` is the service's own.
- [x] **Sector is an enum, never a free string.** `SiteSector` on the wire and in the column, with
      `SiteStatus` and `SaProvince` beside it; the conventions guard covers all three.
- [x] **Creating a site without a valid location is rejected with a clear error.** Three shapes of
      mistake, each with the numbers in the message: `0, 0`, a swapped pair, and a positive
      latitude.
- [x] **Only a clinic manager for that site, or a platform admin, can edit it.** The site guard
      decides *whose* clinic (another clinic's id is a 404, not a 403) and the `sites.profile:update`
      grant decides *what* — a receptionist holds `read` and is refused the edit.
- [x] **`sites` fixtures exist in `tests/factories.py` for every other module to use.**
      `SiteFactory.build()` / `.create(db)` produce the real model, cycling the eleven demo clinics;
      the cross-tenant suite and the new M4 fixture already use them.

## Risk and rollback

**Migration `0007`** adds one table and changes nothing existing, so the previous release runs
unchanged on the new schema and the migration is reversible (proven by the round-trip test). Two
behaviour changes worth naming, both intended: `make seed-dev-data` now writes eleven clinics, and
the demo staff's role assignments point at a site **id** rather than a slug — a database seeded
before this PR keeps its old assignment and should be re-seeded (the script is idempotent). A new
runtime dependency, GeoAlchemy2, is pinned and used by one module.

**Follow-ups noticed:** the geocoding proxy has no rate limit of its own, which matters once a
provider with a quota is configured (worth folding into Issue 29, which is the screen that will use
it); `Site.notes` is free text with no redaction rule, and should be named in the M13 data map
(Issue 95) before anyone types a person into it.

Closes #23
