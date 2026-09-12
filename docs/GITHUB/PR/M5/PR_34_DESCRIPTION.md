# PR: Find a clinic by typing a suburb, when there is no GPS (Issue 34 / M5-34)

**Milestone:** [Milestone 5: Discovery & Geolocation](https://github.com/Billykat7/clinicQ/milestone/5) ·
**Issue:** [#34](https://github.com/Billykat7/clinicQ/issues/34) · **Builds on:** #31 (PR #147)

> **Merge order:** after #31 (PR #147). This branch is stacked on it, so the Conventions check fails
> on #31's commits until that merges. Every test job passes.

GPS is unavailable more often than a smartphone-first design assumes: a declined permission, a
phone indoors, an old handset, and **every USSD session**, which has no location at all. This PR is
how those patients find a clinic. They type a suburb, township or town ("Soweeto" works), pick
the place, and the same `find_nearby_sites()` from #31 searches from that place's centroid, with
every distance labelled as approximate. The web page, USSD (#73) and WhatsApp (#75) all call the
same two functions for this, and a source guard fails the build if a second place-name search
appears anywhere else.

## Summary

- **2,081 real places, seeded by migration `0014`**: every OpenStreetMap place node in Gauteng
  (1,160) and KwaZulu-Natal (921), each with its municipality, province and centroid, and 68
  alternative names. The source, the licence (ODbL 1.0), the extraction date and both Overpass
  queries are in the migration docstring.
- **`search_areas(db, text)`**, the one place-name search: the text is folded (accents, case,
  punctuation), then matched exactly, then by prefix, then by `pg_trgm` similarity under a GIN index.
  "Soweeto", "Mamelody", "HILBROW" and "kwa-thema" all find their places.
- **Alternative names are rows of their own**: Tembisa finds Thembisa, Tokoza finds Thokoza, Joburg
  finds Johannesburg, Tshwane finds Pretoria, and eThekwini finds Durban. The answer says which
  name matched.
- **`find_nearby_sites(db, AreaOrigin(area_id))`**: the #31 search, from an area's centroid. Every
  result carries `DistanceBasis.AREA_CENTROID` and a label such as *about 2.1 km from the middle of
  Soweto*, written once in `discovery/wording.py` so no channel can show a bare figure.
- **API:** `GET /api/v1/clinics/areas?q=` (public typeahead), `/clinics/nearby?area_id=` (exactly
  one origin), and a signed-in patient's `GET` / `PUT /clinics/areas/recent`, which keep the
  latest five so picking one again is a single interaction.

## Design notes

**F supplies the data, A builds the search.** The dataset is built by
`scripts/db/build_area_dataset.py` (F's tool), which reads two saved Overpass API answers and writes
`alembic/data/0014_areas.csv`. It touches neither a database nor the network, so a reviewer can
rebuild the CSV from the files and diff it. It records both queries, computes each place's
municipality with Shapely from the administrative boundaries (the most local of `admin_level` 8,
then 6), and merges a short, reviewed `COMMON_NAMES` list of everyday spellings. The build fails if
an entry names a place that is not in the extract. The migration loads the CSV and freezes it; a
later dataset is a later migration.

**Which province is the pilot's?** No document names one. The demo directory
(`scripts/db/demo_dataset.py`) has seven clinics in Gauteng and four in KwaZulu-Natal, so the dataset
covers both. Covering only one would leave the Durban and Pietermaritzburg demo clinics unreachable
without GPS.

**Folding lives in `src/commons/search.py`**, not in the discovery module, because migration `0014`
uses it to fill `area_name.search_key`. If the folding ever changes, a new migration recomputes the
keys; `tests/unit/discovery/test_search_wording.py` pins today's behaviour.

**Ranking** is exact, then prefix, then trigram similarity; among equals a place's own name comes
before an alternative, a city before a suburb, then alphabetical order. The best-matching name is
chosen per area (`DISTINCT ON`), so a place matched by two of its names appears once. The
similarity threshold is set per transaction (`set_config(..., true)`), so the module's constant
decides it rather than server configuration.

**"Approximate everywhere" is one function.** `wording.distance_label()` writes "1.4 km" from a
position and "about 4.0 km from the middle of Soweto" from an area. The service puts it on every
result, so the web API, a USSD adapter calling the service directly, and the pages in #32 and #35
all get the same words. Metres are rounded to 50 so no label claims a precision nobody has.

**One service call for every channel, enforced.** `tests/unit/discovery/test_one_area_search.py`
walks `src/` and fails if any module other than `discovery/areas.py` names the `AreaName` model or
runs raw SQL against `area_name`, and proves it can fail on both shapes. The integration test
`test_the_same_service_call_backs_web_ussd_and_whatsapp` drives a text-only adapter (the shape #73
will have) through `search_areas` and `find_nearby_sites` and asserts it gets exactly the places and
clinics the web API returns, in the same order.

**Recently used areas are suburbs, never positions.** `patient_recent_area` stores
`(patient, area, used_at)`: at most five per patient, one row per area (searching again moves it to
the top), deleted with the patient. The routes sit behind the `patient` role's grant on
`patients.self`, like the patient's own consent routes. Each item's `id` is the whole next search.
On the web that is one tap and on USSD one digit, which is what the criterion asks.

**`pg_trgm` goes in `public`**, like PostGIS in `0001`, because it is shared with anything else in the
database; a downgrade drops the three tables and leaves the extension. `alembic check` finds no drift
and the base-to-head round trip passes.

**Out of scope:** the USSD and WhatsApp menus (Issues 73, 75) and the page that offers the area
search when the location prompt is declined (Issue 32, next in this stack).

## Changes

- **`alembic/versions/0014_areas_seed.py`** (new): `pg_trgm`; tables `area`, `area_name`,
  `patient_recent_area`; the GIN trigram index; the seed. **`alembic/data/0014_areas.csv`** (new,
  2,081 rows).
- **`scripts/db/build_area_dataset.py`** (new): rebuilds the CSV from a saved OSM extract.
- **`src/database/models/area.py`** (new): `Area`, `AreaName`, `PatientRecentArea`, exported from
  `models/__init__.py`.
- **`src/commons/search.py`** (new): `fold_for_search`. **`src/commons/enums.py`:** `AreaKind`,
  `DistanceBasis`.
- **`src/modules/discovery/areas.py`** (new): `search_areas`, `get_area`, `remember_area`,
  `recent_areas`. **`wording.py`** (new): `distance_label`.
- **`src/modules/discovery/service.py`:** `AreaOrigin` / `SearchOrigin`; each result's
  `distance_basis` and `distance_label`; the page's `origin_area`. **`schemas.py`:** `AreaOut`,
  `AreaListOut` and the new fields. **`router.py`:** `/areas`, `/areas/recent`,
  `/areas/recent/{area_id}`, and `area_id` on `/nearby`.
- **`tests/integration/discovery/test_area_search.py`** (new, 25 cases); **`conftest.py`:** the RBAC
  catalogue and a patient-session client. **`tests/unit/discovery/test_one_area_search.py`** and
  **`test_search_wording.py`** (new, 18 cases). **`tests/unit/security/test_api_route_gates.py`:** the
  public typeahead, with its reason.

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (218 files).
- [x] Full suite with PostgreSQL and Redis required, in UTC as CI runs it: **1533 passed, 9
      xfailed** (1489 on #31; 43 new here, and the migration round-trip and `alembic check` tests
      now cover `0014`).
- [x] `tests/integration/discovery/test_area_search.py` and `tests/unit/discovery`: **54 passed**.
- [x] **How to verify, end to end.** A throwaway database on the compose PostgreSQL 18 + PostGIS 3.6:
      `alembic upgrade head`, `seed_dev_data`, then the real app. Transcript, with the request log
      lines removed:

```text
$ alembic upgrade head on a fresh database, then seed_dev_data
  alembic_version: 0014
  areas: [('Gauteng', 1160), ('KwaZulu-Natal', 921)]
  names: (2149, 68)            ← all names, of which alternatives
  pg_trgm: 1.6

$ GET /api/v1/clinics/areas?q=Soweeto&limit=3
    Soweto, City of Johannesburg Metropolitan Municipality       city
$ GET /api/v1/clinics/areas?q=sowe&limit=3
    Soweto, City of Johannesburg Metropolitan Municipality       city
$ GET /api/v1/clinics/areas?q=Tembisa&limit=3
    Thembisa, City of Ekurhuleni Metropolitan Municipality       town  (matched 'Tembisa')
    Temba, City of Tshwane Metropolitan Municipality             suburb
$ GET /api/v1/clinics/areas?q=Joburg&limit=3
    Johannesburg, City of Johannesburg Metropolitan Municipality city  (matched 'Joburg')
$ GET /api/v1/clinics/areas?q=kwa-thema&limit=3
    KwaThema, City of Ekurhuleni Metropolitan Municipality       suburb
    KwaDuma, Nongoma Local Municipality                          village
$ GET /api/v1/clinics/areas?q=Riverside&limit=3
    Riverside, eThekwini Metropolitan Municipality               suburb
    Riverside, Umzimkhulu Local Municipality                     village
    Riverside Estates, City of Tshwane Metropolitan Municipality suburb

$ GET /api/v1/clinics/nearby?area_id=<Soweto>&radius_m=20000
  origin_area: Soweto, City of Johannesburg Metropolitan Municipality | distance_basis: area_centroid
    mandela-sisulu-clinic    approximate=True  about 2.1 km from the middle of Soweto
    mofolo-south-clinic      approximate=True  about 2.8 km from the middle of Soweto
    medicross-meldene        approximate=True  about 12.2 km from the middle of Soweto
    hillbrow-chc             approximate=True  about 15.9 km from the middle of Soweto
    medicross-randburg       approximate=True  about 15.9 km from the middle of Soweto

$ GET /api/v1/clinics/nearby?lat=-26.235&lon=27.906&radius_m=20000   (GPS, for contrast)
    mandela-sisulu-clinic    approximate=False  50 m
    mofolo-south-clinic      approximate=False  2.6 km
    medicross-meldene        approximate=False  11.4 km
```

- [ ] Screenshot: no page in this PR. The page that offers this search is Issue 32.

## Acceptance criteria

- [x] **Declining GPS still returns results via a typed suburb name.**
      `test_declining_gps_still_returns_results_via_a_typed_suburb`: "Soweeto", pick the first
      suggestion, search. The two Soweto clinics come first, with no position sent at all.
- [x] **Typeahead tolerates a misspelling and common alternative names.**
      `test_a_misspelt_suburb_is_still_found` (the spec's "Soweeto"), six more misspellings, case and
      punctuation forms, and six alternative names, each asserting the right place is suggested
      **first** and, for an alternative, that `matched_name` says which name matched.
- [x] **The same service call backs the web, USSD and WhatsApp area search.**
      `test_the_same_service_call_backs_web_ussd_and_whatsapp` (a text-only adapter gets what the web
      API gets, in order) and the source guard `test_only_the_area_service_queries_place_names`,
      which fails on a second implementation and is shown failing on one.
- [x] **Area data is seeded by migration with its source documented.**
      `test_area_data_is_seeded_by_migration_with_its_source_documented`: the migrated database holds
      all 2,081 rows (1,160 + 921), every centroid inside South Africa, and the docstring names
      OpenStreetMap, the ODbL, the licence URL, the row count, the Overpass API and the build script.
- [x] **Distances from an area centroid are clearly labelled as approximate.**
      `test_distances_from_an_area_centroid_are_labelled_approximate_everywhere`: over the API every
      area result has `distance_is_approximate: true` and a label beginning "about" and naming the
      area; a GPS search has neither; the service's own dataclasses carry the same label.
- [x] **Selecting a recently used area takes one interaction.**
      `test_selecting_a_recently_used_area_takes_one_interaction`: after `PUT
      /areas/recent/{id}`, the recent list's first `id`, unchanged, is the whole `/nearby` search.
      Two more tests cover the cap of five, newest first, no repeats, and that one patient never
      sees another's.

## Risk and rollback

**Migration `0014`** creates three tables and an extension and changes nothing existing, so the
previous release runs unchanged on the new schema. It is reversible; a downgrade leaves `pg_trgm`
installed, as `0001` leaves PostGIS. The seed adds about 2 seconds to a fresh `upgrade head`. The new
public routes read a public dataset. Rollback is a revert, but #32 onwards are stacked on this.

**Follow-ups noticed:** `patient_recent_area` is personal information (suburb-level, never a
position), so it belongs in Issue 95's data map with a retention rule. The dataset covers two
provinces; the real pilot province, once named, may need a later migration. OSM coverage of rural
villages in KwaZulu-Natal is thinner than of metro suburbs.

Closes #34
