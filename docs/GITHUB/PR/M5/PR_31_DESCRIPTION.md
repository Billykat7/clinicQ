# PR: Find the verified clinics near a coordinate, as data every channel can reuse (Issue 31 / M5-31)

**Milestone:** [Milestone 5: Discovery & Geolocation](https://github.com/Billykat7/clinicQ/milestone/5) ·
**Issue:** [#31](https://github.com/Billykat7/clinicQ/issues/31) · **Builds on:** #23 (sites and the
GiST index), #24 (opening hours), #25 (queues), all merged

> **Merge order:** first of the M5 stack, branched from `main`. Every later M5 branch (#34, #32, #35,
> #36, #37, #33, #38) is stacked on this one.

The search service is the shared core of discovery. The web list, the map, the USSD menu and the
WhatsApp bot all ask "which clinics are near me?", and this PR gives them one function that answers
it: `find_nearby_sites()` in the new `src/modules/discovery/`, which returns plain data and never
HTML, behind a thin public route at `GET /api/v1/clinics/nearby`. A second search in a channel
adapter is the failure this is built to avoid, so the radius cap, the verified-only rule and the
open-now logic all live in the service, where an adapter calling it directly gets them too.

## Summary

- **`find_nearby_sites(db, origin, radius_m, sector, open_now, limit, offset)`**: verified clinics
  inside an `ST_DWithin` radius on the `geography` column, nearest first, each with the
  straight-line distance, a rough walking and driving time, whether it is open now and when it next
  opens (Issue 24's rules), and its live queues. It returns frozen dataclasses.
- **`GET /api/v1/clinics/nearby`**: public, paginated (`limit` up to 50, `offset`), with `sector`
  (`public` / `private` / `all`) and `open_now`. Served under `/api/v1/`, recorded as **open
  decision 8** in `docs/GITHUB/ISSUES/README.md`.
- **The radius is capped in the service at 50 km.** Asking for 5,000 km searches 50 km, and the
  answer says `"capped": true`.
- **Only verified clinics, by construction.** The visibility rule is written once
  (`publicly_visible_site_clauses()` beside the site guard) and a new guard helper,
  `published_select()`, is the only way a patient-facing read reaches a clinic's hours or queues.
  A draft, pending or suspended clinic's rows are unreachable even if its id is passed in.
- **Queue lengths are read directly, and honestly.** There is no ticket table until Issue 39, so
  every length is `null` ("not measured"), never `0`. One function, `read_waiting_counts()`, is
  what Issue 39 fills in and Issue 36 wraps.
- **Proven against PostGIS:** `EXPLAIN` of the service's own statement uses
  `ix_clinicq_site_location_gist` with the planner left alone, and the search takes a **median of
  about 13 ms with 500 seeded clinics** (budget 200 ms).

## Design notes

**Queue length says "not measured" until tickets exist.** The instruction for this issue was to read
queue lengths directly until Issue 36 lands. There is nothing to read yet: tickets are Issue 39 in
M6, and the demo dataset's day of tickets is still a stub for that reason. Creating a ticket table
here would take Issue 39's model and its sequence design away from its owner (A, in M6), so this
PR does not. Instead `src/modules/queues/live.py` holds the single direct read,
`read_waiting_counts(db, queues)`, and today it returns `None` for every queue. `None` travels to the
API as `null`, documented as "not the same as 0", and `total_waiting` refuses a partial sum. A clinic
showing "0 waiting" at 07:30 on a Monday would send a patient on a trip on a figure nobody counted.
Issue 39 replaces that one function body with a grouped `COUNT` of `waiting` tickets, and nothing
above it changes. The same rule covers the wait estimate: `wait_range` is `null` until Issue 42's
estimator exists, and `WaitRange` refuses a "range" that is really one number.

**The reader is a parameter.** `find_nearby_sites(..., reader=read_waiting_counts)` is the seam Issue
36's snapshot cache plugs into, and the one a test uses to prove a real count travels intact to the
result (`test_a_measured_queue_length_travels_intact_to_the_result`).

**Public reads go through the guard too.** Non-negotiable 3 puts every query on a site-scoped row
through `src/core/site_scope.py`, and a patient holds no `SiteAccess`. Rather than exempt discovery
from the rule, `published_select(model, site_ids)` joins the row to its clinic and applies the
visibility clauses. It reads only what a clinic publishes about itself (hours, closures, queue names).
`tests/unit/security/test_site_scoped_queries.py` learns the helper's name, and that is the only
change to a guard test.

**A fixed number of queries per page.** One statement returns the page, the distances and the total
(a window function); `published_schedules()` loads every result's weekly hours, holiday rules,
closures and the holiday calendar in four queries; `published_live_queues()` reads the queues in one
more and calls the reader once. So twenty results cost seven queries whether the directory holds
eleven clinics or eleven thousand.

**`open_now` is filtered in Python over a bounded candidate set.** Issue 24's precedence (closure,
then holiday rule, then weekly schedule, with spans that cross midnight) does not reduce to SQL, so
the filter examines up to 500 nearest candidates, then paginates. That is the whole seeded
directory in the performance test and more than a 50 km radius holds outside the three metros; it is
timed too (about 60 ms over 500 candidates).

**Capping, not refusing.** A radius above 50 km is narrowed, not rejected with a 422, so a patient's
app that asks for 100 km still gets the clinics within 50 km. Below 100 m the radius is raised to
100 m, because a GPS fix's own error is larger than the circle. A coordinate outside South Africa
*is* refused (422), naming the numbers and suggesting a swapped pair, because an empty list there
would hide the bug.

**Travel time is deliberately rough.** A routing engine would be a network call per result to a host
the CSP does not allow. `src/modules/discovery/travel.py` states its three numbers: a road detour
factor of 1.3, walking at 4.5 km/h and driving (car or minibus taxi) at 25 km/h, rounded up and never
below one minute. The map (Issue 33) hands off to the phone's own directions app for the real route.

**No RBAC manifest for the module.** Discovery gates nothing: both routes are public and are listed
in `tests/unit/security/test_api_route_gates.py` with their reasons. Rate limiting against scraping
is Issue 38's.

**Out of scope:** the list and map pages (Issues 32, 33), searching from a suburb (Issue 34), the
snapshot cache (Issue 36) and the discovery OpenAPI contract (Issue 38).

## Changes

- **`src/modules/discovery/`** (new): `service.py` (`find_nearby_sites`, `nearby_statement`,
  `clamp_radius` and the result dataclasses), `travel.py` (`estimate_travel`), `schemas.py` (the
  API's rendering), `router.py` (`GET /clinics/info`, `GET /clinics/nearby`), `__init__.py`.
- **`src/modules/queues/live.py`** (new): `read_waiting_counts`, `published_live_queues`,
  `total_waiting`, `LiveQueue`, `WaitRange`.
- **`src/core/site_scope.py`:** `publicly_visible_site_clauses()` and `published_select()`.
- **`src/modules/sites/hours.py`:** `published_schedules()` for many visible clinics at once;
  `schedule_for()` now shares its holiday and closure helpers.
- **`src/modules/sites/discovery.py`:** `publicly_visible()` reuses the clauses above.
- **`src/commons/enums.py`:** `SectorFilter` (`public` / `private` / `all`) and
  `BoundedContext.DISCOVERY`. **`src/api/v1/router.py`:** the router registered.
- **`tests/integration/discovery/`** (new): `conftest.py` (the demo clinics in a migrated PostGIS
  database) and `test_nearby_search.py` (20 cases). **`tests/unit/discovery/test_travel_and_radius.py`**
  (new, 11 cases).
- **`tests/unit/security/test_api_route_gates.py`:** the two public routes, with reasons.
  **`test_site_scoped_queries.py`:** `published_select` recognised as a guard helper.
- **`docs/GITHUB/ISSUES/README.md`:** open decision 8 decided.

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (214 files).
- [x] Full suite with PostgreSQL and Redis required, in UTC as CI runs it
      (`TZ=UTC pytest tests/ -n auto --dist loadscope`): **1489 passed, 9 xfailed** (was 1458 on
      `main`; the 31 new tests account for the difference).
- [x] **The index and the budget, from the tests' own output** (`-s`):

```text
tests/integration/discovery/test_nearby_search.py
  500 clinics, 48 within 10 km: median 12.5 ms, slowest 19.4 ms
  open_now over 500 candidates: 56.5 ms
43 passed   (discovery, the two unit files and both guard tests)
```

      `EXPLAIN` of `nearby_statement()` (5 km, all sectors) over the same 500 analysed clinics, with
      no planner setting changed:

```text
Sort  (cost=247.51..247.51 rows=1 width=1338)
  Sort Key: (st_distance(location, '0101000020E6...'::geography, true)), id
  ->  WindowAgg  (cost=4.40..247.50 rows=1 width=1338)
        ->  Bitmap Heap Scan on site  (cost=4.40..234.86 rows=1 width=1322)
              Filter: ((is_deleted IS FALSE) AND (is_active IS TRUE) AND ((status)::text = 'verified'::text)
                       AND st_dwithin(location, '0101000020E6...'::geography, '5000'::double precision, true))
              ->  Bitmap Index Scan on ix_clinicq_site_location_gist  (cost=0.00..4.40 rows=17 width=0)
                    Index Cond: (location && _st_expand('0101000020E6...'::geography, '5000'::double precision))
```

- [x] **How to verify, end to end.** A throwaway database on the compose PostgreSQL 18 + PostGIS 3.6,
      migrated with `alembic upgrade head`, seeded with `scripts.db.seed_dev_data.seed` (the eleven
      demo clinics, verified, with hours and queues), then driven through the real app. Transcript,
      with the request log lines removed:

```text
$ GET /api/v1/clinics/nearby?lat=-26.2&lon=27.9&radius_m=5000
200 total 1 radius {'requested_m': 5000, 'applied_m': 5000, 'capped': False}
    3950 m  mandela-sisulu-clinic    public   walk 69 min, drive 13 min, waiting=None, queues=3

$ GET /api/v1/clinics/nearby?lat=-26.2&lon=27.9&radius_m=20000
    3950 m  mandela-sisulu-clinic
    5495 m  mofolo-south-clinic
   10385 m  medicross-meldene
   13350 m  medicross-randburg
   14550 m  hillbrow-chc

$ (mandela-sisulu-clinic set to pending_verification) same search
    5495 m  mofolo-south-clinic
   10385 m  medicross-meldene
   13350 m  medicross-randburg
   14550 m  hillbrow-chc

$ GET /api/v1/clinics/nearby?lat=-26.2&lon=27.9&radius_m=5000000   (5,000 km)
 radius: {"requested_m": 5000000, "applied_m": 50000, "capped": true}  total: 5  cities: ['Johannesburg', 'Pretoria', 'Randburg']

$ GET /api/v1/clinics/nearby?lat=28.04&lon=-26.2   (swapped pair)
422 28.04000, -26.20000 is outside South Africa (latitude -35.5..-21.5, longitude 15.5..33.5).
    Check that latitude and longitude are not swapped, and that the latitude is negative.
```

- [ ] Screenshot: no page in this PR. The list that renders this data is Issue 32.

## Acceptance criteria

- [x] **A search from a coordinate returns clinics within the radius, nearest first.**
      `test_a_search_returns_the_clinics_inside_the_radius_nearest_first` and the transcript: the
      exact five clinics within 20 km, in distance order, and none beyond it.
- [x] **The query uses the GiST index, confirmed by an `EXPLAIN` assertion in a test.**
      `test_the_query_uses_the_gist_index` compiles `nearby_statement()`, the statement the service
      runs, and asserts the plan names `ix_clinicq_site_location_gist` with an index scan and no
      sequential scan on `site`. Unlike Issue 23's eleven-clinic test, it does not turn
      `enable_seqscan` off.
- [x] **Search completes in under 200 ms with 500 seeded clinics.**
      `test_search_completes_in_under_200_ms_with_500_seeded_clinics` times the whole service call
      (clinics, schedules, queues, travel and open status) seven times after a warm-up and asserts
      the median: 12.5 ms here. The `open_now` path is timed separately.
- [x] **Only `verified` sites are returned.** `test_only_verified_sites_are_returned` puts a clinic
      100 m from the patient and tries draft, pending, suspended, switched off and deleted in turn;
      none is returned and `total` does not count it. The transcript shows the same over HTTP.
- [x] **The service returns data structures, never rendered HTML, so channels can reuse it.**
      `test_the_service_returns_data_structures_never_rendered_html`: frozen dataclasses all the way
      down, JSON-serialisable with no markup, and a text-only menu of the kind USSD will render built
      from nothing but attribute access.
- [x] **Radius is capped server-side so a caller cannot request the entire country.**
      `test_the_radius_is_capped_server_side` over the API and against the service directly: 5,000 km
      becomes 50 km, `capped` is true and no Durban clinic appears.

## Risk and rollback

No migration and no change to an existing route. Two public routes are added, and they return only
what verified clinics publish. The refactor of `schedule_for()` keeps its behaviour; Issue 24's
suite passes unchanged. Rollback is a revert of this PR, but the later M5 branches are stacked on it.

**Known limits, stated rather than hidden:** queue lengths are `null` until Issue 39; wait ranges
are `null` until Issue 42; the search is not rate limited until Issue 38, so the 50 km cap is today's
only defence against copying the directory.

Closes #31
