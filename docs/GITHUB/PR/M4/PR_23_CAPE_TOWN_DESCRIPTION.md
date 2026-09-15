# PR: Seed real Cape Town clinics, and the Cape Town places a search starts from (Issue 23 / M4-23 follow-up)

**Milestone:** [Milestone 4: Clinics, Queues & Configuration](https://github.com/Billykat7/clinicQ/milestone/4) ·
**Issue:** [#23](https://github.com/Billykat7/clinicQ/issues/23) (closed by its pull request; this is a
follow-up)

The demo world had eleven clinics, all in Gauteng and KwaZulu-Natal. Anyone testing from Cape Town had
nothing near them: pressing **Use my location** found no clinic within 50 km, and typing a Cape Town suburb
found no place, because the places a search starts from (Issue 34, migration `0014`) also covered only those
two provinces.

This PR adds 15 real Cape Town clinics to the demo directory, and 513 Cape Town places to search from.

## Scope

- **In:**
  - fifteen Cape Town clinics in `scripts/db/demo_dataset.py`;
  - migration `0038`, seeding the City of Cape Town's places;
  - two build rules in the area tool;
  - the tests and docs that counted eleven clinics or two provinces.
- **Out:**
  - rebuilding `0014`'s Gauteng and KwaZulu-Natal places (frozen with their migration);
  - opening hours and queues that are the clinics' own (they stay demo values, as for the first eleven);
  - other Western Cape towns.

## Summary

- **Fifteen Cape Town clinics**, each with its OpenStreetMap element:
  - thirteen public: Delft, Bishop Lavis and Lotus River community health centres; Site B, Nolungile and
    Kuyasa in Khayelitsha; Nyanga Community Day Clinic; and Elsies River, Chapel Street (Woodstock), Spencer
    Road (Salt River), Maitland, Lavender Hill and Strandfontein clinics;
  - two private: Kenilworth Medicross and Medicross Fish Hoek.
  - `make seed-dev-data` now seeds 26 clinics and 80 queues. It is still idempotent: a second run reports
    `0 created, 0 updated, 26 unchanged`.
- **513 Cape Town places** in migration `0038`:
  - from `alembic/data/0038_areas_cape_town.csv`, every place node inside the City of Cape Town;
  - with everyday spellings OSM does not carry: "Gugulethu" finds Guguletu, "Mitchell's Plain" finds
    Mitchells Plain, and "CPT" and "Kaapstad" find Cape Town;
  - the downgrade removes exactly these places.
- **Two build rules in `scripts/db/build_area_dataset.py`**, applied to this extract:
  - **places outside the operating country are left out:** the City of Cape Town's boundary includes the
    Prince Edward Islands;
  - **one place is kept per name in a municipality:** every Cape Town place has the same municipality, so two
    places with one name would be two identical suggestions.

## Design notes

- **The same source and record as the first eleven.** Every clinic's name, position and operator was read from
  OpenStreetMap through the Overpass API on 2026-09-15 (data as of 2026-05-31), not typed from memory. Each
  carries its element (`node/`, `way/` or `relation/`). An operator tagged with a typo ("Western Cape
  Department oif health") or not tagged at all is written correctly, with a comment saying so.
- **A migration, not the dev seed, for the places.** `0014` put the searchable places in a migration because
  every environment needs them, not only a demo database. The Cape Town places follow the same path: a frozen
  CSV built by the tool, which records both Overpass queries so the file can be rebuilt and checked.
- **Why duplicates are dropped rather than labelled.** A suggestion's label is "name, municipality"
  (Issue 34). Of the 13 names that occurred twice, 9 pairs are the same place mapped twice (identical or
  within about a kilometre). The other 4 (Riverside, Protea Park, The Vines, Westridge) are different places
  whose labels would be identical. The larger kind is kept (a suburb over a neighbourhood), then the older
  node. For Westridge that keeps Somerset West's and drops Mitchells Plain's: both are suburbs, and the
  Somerset West node is older. Telling same-named places apart inside one metro needs a finer boundary than
  OSM has for Cape Town, which is left for later.
- **The rules apply to new builds only.** `0014`'s CSV was built before them and is frozen with its migration,
  so the Gauteng and KwaZulu-Natal places are unchanged (2,081, as before).

## Changes

- **`scripts/db/demo_dataset.py`:** fifteen `SiteStub`s under a Cape Town comment; the module docstring counts
  them.
- **`alembic/versions/0038_areas_cape_town.py`, `alembic/data/0038_areas_cape_town.csv`** (new): 513 places and
  their names; the downgrade deletes by `osm_ref`.
- **`scripts/db/build_area_dataset.py`:**
  - `--output`, and the Cape Town queries;
  - `COMMON_NAMES` for Cape Town, checked only when its province is in the extract;
  - the country and one-name-per-municipality rules.
- **`scripts/db/seed_dev_data.py`:** the summary names the provinces from the data instead of "Gauteng and
  KwaZulu-Natal".
- **Tests:**
  - `tests/unit/seed/test_demo_dataset.py`: Western Cape bounds, relations as OSM elements, and a Cape Town
    test (public and private, Woodstock, Khayelitsha and Delft);
  - `tests/integration/discovery/test_area_search.py`: both datasets counted by province; "Gugulethu" and
    "Kaapstad" as alternatives; a Woodstock search whose nearest clinic is Chapel Street; Melville now in
    more than two provinces;
  - docstrings that said "eleven clinics".
- **Docs:** `docs/QUICKSTART.md` and `docs/OPS/PATIENT_APP_TESTING.md` describe twenty-six clinics, including
  Cape Town.

## Testing

**The whole suite, as CI runs it** (`pytest tests/ -n auto --dist loadscope`, PostgreSQL and Redis, `TZ=UTC`):

```text
2495 passed, 1 skipped, 9 xfailed in 456.63s (0:07:36)
```

**The area search** (`tests/integration/discovery/test_area_search.py`): `28 passed in 56.07s`. That run first
failed five times, and each failure was a real finding:

- a place at 46.9° S (the Prince Edward Islands);
- two "Riverside, City of Cape Town" suggestions with the same label;
- "Elsies River" folding to the same search key as "Elsiesriver", so it is not an alternative at all;
- a Melville in a third province;
- an attribute name in the new test.

The build rules and the tests above are the fixes.

**The dataset builds the same file twice** from the saved extract (`cmp` of two outputs: identical):

```text
wrote 513 areas to alembic/data/0038_areas_cape_town.csv
```

**The migration, both ways, on a local database** (`clinicq_pwa_guide`):

```text
before: Gauteng|1160 KwaZulu-Natal|921 Western Cape|513
Running downgrade 0038 -> 0037
down:   Gauteng|1160 KwaZulu-Natal|921   names_orphans=0
Running upgrade 0037 -> 0038
up:     Gauteng|1160 KwaZulu-Natal|921 Western Cape|513
```

**The seed, twice:**

```text
Clinics: 15 created, 0 updated, 11 unchanged
Demo dataset (scripts/db/demo_dataset.py): 26 clinics in Gauteng, KwaZulu-Natal and Western Cape, 80 queues, 8433 tickets by 11:30 today (…)
Clinics: 0 created, 0 updated, 26 unchanged
```

**In the browser** (375 px, `/discover`): typing "Gugulethu" suggested "Guguletu (Gugulethu), City of Cape Town".
Choosing it listed "8 clinics within 10 km", nearest first: Nyanga Community Day Clinic about 1.9 km away,
Bishop Lavis Community Health Centre 3.9 km, Elsies River Clinic 5.9 km, then Delft Community Health Centre
6.8 km. Each was "Closed, opens tomorrow at 07:00" at 19:40.

`make milestone-progress`: `14 milestone(s): up to date`. This follow-up closes nothing, so it assumes no issue
closed. `ruff check .` and `ruff format --check .` are clean.

**Not checked:** the clinics' real opening hours and services. They are demo values, as for the first eleven.

## Risk and rollback

- **Migration `0038` adds rows to `area` and `area_name` only.** No table changes; the release before this one
  runs unaffected. The downgrade deletes exactly the 513 places, and their names and any patient's recent
  searches from them cascade.
- **Production gets the places too**, as with `0014`. They are public place names and positions from
  OpenStreetMap, under the ODbL, which the discovery pages already credit.
- **The demo clinics are development data.** `seed_dev_data` still refuses anything but a local development
  database.
- **Tests that create sites with the factory** now cycle through twenty-six clinics instead of eleven, so a
  factory-made site can be in Cape Town. The full suite passes with that.

Refs #23
