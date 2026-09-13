# Release v0.5.0: Discovery & Geolocation

**Date:** 2026-09-13 · **Milestone:** M5 · **Issues closed:** 31–38

A pre-release. v0.4.0 put clinics in the system; this one lets a patient **find** one. Nearby
verified clinics, public or private, from a phone's location or a typed suburb, on a list or a map,
each with whether it is open, how far it is, and what a patient needs to decide before travelling.

There is still **no ticket**, so nobody joins a queue in this release; that is M6. Every place a queue
length or a wait would appear says so honestly: *not measured* is never shown as `0`, and *no
estimate* is never shown as a number.

Three decisions shape it. **The search is one function that returns data, never HTML**
(`find_nearby_sites`). The web page, the map, and later the USSD menu and WhatsApp bot (M10) all call
it, so a channel is an adapter, not a second search. **The map draws the list's own cards** and never
makes a second query, so it cannot disagree with the list. And **analytics are anonymous by what the
table can hold**, not by policy: `discovery_event` has no column for a person or a position, and a
test reads the stored rows to prove it.

The tag is cut when the last of the eight stacked pull requests (#147–#154) merges. This note is
written in the last one, as the milestone's closing record.

## What shipped

- **Verified clinics near a coordinate** (Issue 31, PR #147). `find_nearby_sites()` runs `ST_DWithin`
  on the `geography` column, nearest first. Each result has the distance, a rough walking and driving
  time, open or closed with when it next opens (v0.4.0's rules), and its queues.
  `GET /api/v1/clinics/nearby` is public and paginated, with `sector` and `open_now`. The radius is
  capped at 50 km **in the service**. Only verified clinics are reachable, by construction:
  `published_select()` beside the site guard is the only way a patient-facing read reaches a clinic's
  hours or queues. `EXPLAIN` proves the GiST index is used. With 500 seeded clinics the search takes a
  median of about 13 ms against a 200 ms budget.
- **A suburb instead of GPS** (Issue 34, PR #148; migration `0014`). There are 2,081 OpenStreetMap
  places in Gauteng and KwaZulu-Natal (ODbL 1.0; source, date and queries are in the migration
  docstring), plus 68 alternative names. `search_areas()` matches exactly, then by prefix, then by
  `pg_trgm` similarity, so "Soweeto", "HILBROW" and "kwa-thema" all find their place, and Joburg finds
  Johannesburg. A search from an area measures from its centroid, and **every distance says it is
  approximate** ("about 2.1 km from the middle of Soweto"), worded once for every channel. A
  signed-in patient's last five areas are remembered.
- **The discovery page** (Issue 32, PR #149). `/discover` offers *Use my location* and a suburb
  search together; a refused location moves the focus to the search. Public / Private / All,
  radius and sort are real radio groups that swap the list with htmx and work without JavaScript.
  Sector badges differ without colour: a word, a shape and a border style. It has skeleton, empty and
  error states, and axe-core finds 0 violations in light and dark. The Slow 3G run found that
  **production nginx never compressed JavaScript**; `platform.conf` is fixed.
- **One clinic's page** (Issue 35, PR #150). `/discover/clinics/{slug}` and
  `GET /api/v1/clinics/{slug}` show hours from v0.4.0's `open_state()`, queues, services, directions
  and tap-to-call. **The join button is never hidden**: it is disabled with its reason (closed until
  when, a closure in the manager's words, walk-in only, or joining from a phone not switched on yet).
  Waits are a range or nothing. The live figures refresh every 30 seconds. The page fits 320 px with no
  horizontal scroll.
- **Queue lengths in one read** (Issue 36, PR #151; migration `0015`). A page of twenty clinics costs
  one Redis `MGET`, then one `site_queue_snapshot` query for anything missing, then one recount.
  **Nothing older than 30 seconds is served**, and a flushed or unreachable Redis is slower, never
  wrong. A 30-second circuit breaker stops an outage adding a timeout to every page.
  `on_queue_changed()` is the write-through hook for M6. A `run_*` sweep under advisory lock 336
  repairs drift every minute, and both paths have Prometheus counters.
- **Payment and medical aid, private clinics only** (Issue 37, PR #152; migration `0016`). A private
  clinic declares cash, card and schemes from a controlled list of 15, plus a named *Other*. It is a
  **self-reported directory tag, not an eligibility check**. Every place it appears says *Reported by
  the clinic. Please confirm with the clinic before you travel.* A public clinic cannot hold a profile
  (`409` on the server), and the filter is **absent from the page** under Public and All. A profile
  not confirmed in six months is marked stale. It ships behind `PAYMENT_FILTER_ENABLED`, **off by
  default**.
- **A map of the same clinics** (Issue 33, PR #153). Leaflet is vendored and loaded only when the map
  is opened. The pins are read from the list's cards: squares for public, diamonds for private,
  clustered when they overlap. A pin previews that clinic's own card with **Directions**. **Tile
  failure or eight seconds without a tile falls back to the list**, with a sentence saying why; this
  was demonstrated by blocking the tile host. The OpenStreetMap attribution is shown. The vendored
  Leaflet had been damaged by an earlier rebrand and is restored byte for byte.
- **Anonymous analytics, a rate-limited search, and the contract** (Issue 38, PR #154; migration
  `0017`). Four events are recorded: search, clinic view, join started, join completed. The only link
  between them is a **session reference that rotates daily** (an HMAC of a cookie and the service day
  under a server key). There is no phone, account, IP or coordinate column. A clinic manager gets an
  opt-out and a view-to-join report. The public search, the typeahead and the `/discover` pages are
  rate limited per address and per session, answering `429` with `Retry-After`.
  `contracts/discovery.yaml` documents every route under `/api/v1/clinics`, under v0.4.0's drift test
  by one added line.

## Migrations

Four revisions, `0014` to `0017`, applied in order by `scripts/db/deploy-sequence.sh`. Each adds
tables, an extension or a column with a server default, and drops nothing, so **the previous release
keeps running on the new schema**. Each is reversible, proven by the `postgres`-marked round trip to
base and back to head.

- **`0014_areas_seed`**: `CREATE EXTENSION pg_trgm`, plus new `area`, `area_name` (GIN trigram index
  on the folded name) and `patient_recent_area`. **It seeds 2,081 areas and their names from
  `alembic/data/0014_areas.csv`**, which adds about 2 seconds to a fresh `upgrade head`. A downgrade
  drops the tables and leaves `pg_trgm` installed, as `0001` leaves PostGIS.
- **`0015_site_queue_snapshot`**: new `site_queue_snapshot` (one row per queue: waiting, average
  wait, updated at) with a `site_id` index. A downgrade returns discovery to reading lengths directly.
- **`0016_site_payment_profile`**: new `site_payment_profile` (one per private clinic) and
  `site_payment_medical_aid` (one row per accepted scheme, indexed by scheme). A downgrade drops the
  declared profiles.
- **`0017_discovery_event`**: new `discovery_event` with a `(site_id, kind, service_day)` index, and
  `site.analytics_enabled` with `server_default true`. `site_id` has **no foreign key** on purpose, so
  a report outlives a listing. **A downgrade drops the recorded events with the table.**

## Upgrade notes

- **No new runtime dependency.** `shapely` is used only by `scripts/db/build_area_dataset.py`, which
  rebuilds the area CSV from saved Overpass extracts. The deployed app never runs it, and the CSV is
  committed. Leaflet 1.9.4 is vendored under `src/static/vendor/leaflet/`.
- **Nine new settings**, all with defaults (`.env.example` now lists 132):
  - `PATIENT_JOIN_ENABLED` (`false`): the detail page's join button stays disabled with its reason
    until Issue 40 exists.
  - `PAYMENT_FILTER_ENABLED` (`false`): with it off, no patient-facing surface shows or filters by
    payment, but clinics can fill in their profile.
  - `DISCOVERY_ANALYTICS_ENABLED` (`true`).
  - `DISCOVERY_SEARCH_RATE_LIMIT_PER_IP` (300), `..._PER_SESSION` (60) and `..._WINDOW_SECONDS` (60).
    The address budget is deliberately loose because mobile carriers put many phones behind one
    address.
  - `QUEUE_SNAPSHOT_TTL_SECONDS` (15), `QUEUE_SNAPSHOT_MAX_AGE_SECONDS` (30) and
    `QUEUE_SNAPSHOT_RECONCILE_SECONDS` (60).

  Run `make check-config` after copying.
- **Set `RATE_LIMIT_BACKEND=redis` with more than one worker.** Otherwise each process keeps its own
  window, and the discovery budgets multiply by the worker count.
- **Redis is used, not required.** Without `REDIS_URL`, the queue snapshot reads the table and then
  recounts. With it, a new scheduler job (`Queue snapshot reconciliation`) runs every minute under an
  advisory lock.
- **`/api/v1/clinics/*` is new and public**, under `/api/v1/` like every kernel route (open decision
  8). So are the `/discover` pages. Each is in the public-route allow-lists with its reason.
- **Two new clinic-manager routes:** `GET`/`PUT /api/v1/sites/{site_id}/settings/analytics` and
  `GET /api/v1/sites/{site_id}/reports/discovery-conversion`. They are the first routes on the
  `sites.settings` and `sites.reports` grants, which v0.3.0 already seeded, so there is **no RBAC
  change** and `make seed-rbac` reports nothing new. There is also a clinic-side payment editor at
  `/dashboard/sites/{site_id}/settings/payment`, served only with the payment flag on.
- **nginx:** `infra/nginx/platform.conf` now compresses `text/javascript` and `image/svg+xml`. Reload
  nginx with the release.
- **Map view loads tile images from `tile.openstreetmap.org`.** The CSP needs no change, because its
  `img-src` already allows any `https:` image. A proxy or firewall in front of patients' browsers must
  allow the tile host, or the map falls back to the list.

## Known issues

- **Queue lengths and waits are not shown yet.** There are no tickets until Issue 39, so every queue
  reads *not measured* and every wait *not available yet*. The 30-second staleness bound is enforced
  by the snapshot, but there is nothing to count. This is the one exit criterion M5 meets only in part.
- **A cold first load on Slow 3G is about 5 seconds**, against the milestone demo's 2. DevTools' 3G
  preset shows first paint at about 1.5 s, and a return visit on Slow 3G about 2.1 s. The remaining
  cost is render-blocking CSS; inline critical CSS in `base.html` belongs to Issue 105.
- **Nobody can join from the detail page.** The button is correct and disabled until Issue 40, and the
  join recorders in `analytics.py` are waiting for that flow to call them. A clinic's "full" refusal
  also needs Issue 40's capacity count.
- **The view-to-join report is a query, not yet a report.** `conversion_by_site` feeds the M12 report
  set, and Issue 89 draws it. Until the join flow exists every conversion rate is `0` or `null`.
- **`discovery_event` and `patient_recent_area` have no retention window yet.** Neither holds a person
  or a position; a recent area is suburb-level. Both belong in Issue 95's data map with a rule.
- **The area dataset covers two provinces.** The pilot province has not been named in any document, so
  Gauteng and KwaZulu-Natal were chosen to match the demo directory. Another province needs a later
  migration, and OSM coverage of rural KwaZulu-Natal villages is thinner than of metro suburbs.
- **The map uses OpenStreetMap's public tile servers.** Their usage policy suits a pilot, not heavy
  production traffic, so a tile provider or self-hosted tiles are needed before launch.
- **The medical-aid scheme list should be reviewed yearly** against the Council for Medical Schemes
  register (F).
- **The demo data is thin for this milestone.** It has no clinic phone numbers and no payment
  profiles, and its address lines repeat the clinic name. A demo with the payment flag on needs a
  profile entered through the editor first.
- **The contract has the same limit as v0.4.0's.** The drift test sees the statuses a route
  *declares*, so the discovery routers now declare their `422` and `429`; a refusal a handler raises
  without declaring it is proved by the module tests, not by the drift test. The contract is still
  read from a checkout, not published.
- **Two tests failed once locally and passed on a rerun.**
  `test_email_change_request_does_not_change_the_address` reads the SMTP host from a developer's
  `.env` and fails when that DNS lookup does. `test_offload_is_dramatically_faster_than_blocking` is a
  timing benchmark that failed under load. Neither has been made independent of the machine yet.
- **A developer's `.env` is stale.** `REDIS_URL` names the compose hostname, which does not resolve
  from the host, and `DB_PASSWORD` does not match the container. Tests and demos work around both.
- **Everything from v0.4.0's list still stands**, except one item this release closes: the public
  discovery search is now rate limited. The clinic registration form and the geocoding proxy still
  are not. A new clinic's first manager still cannot be appointed through a route. The one-time-code
  store is still process-local. The credential in the repository's history has still not been
  rotated, and nothing is provisioned. **The `v0.4.0` tag has not been cut either**; it should be cut
  before this one.

## Verification

Run on `Issue/38/discovery-analytics-contract`, with PostgreSQL 18 + PostGIS 3.6 and Redis 8 in the
development stack, both required rather than skippable:

```text
TZ=UTC pytest -q -n auto tests        1659 passed, 9 xfailed in 133.95s
ruff check . / ruff format --check    clean (392 files)
mypy src/                             clean (227 files)
```

CI ran the unit, integration and flow suites on every one of PRs #147–#154. Because the branches are
**stacked**, `Conventions` stays red on each until the branches below it merge, and every other job
is green.

Each pull request carries its own evidence, and it is worth reading beside the suite:

- **#147:** the `EXPLAIN` plan and the 500-clinic timings.
- **#148:** a transcript of misspelt and alternative names.
- **#149:** Slow 3G and 3G timings through nginx, a keyboard-only run, and axe-core in light and dark.
- **#150:** the page at 320 px, and every disabled-join reason.
- **#151:** the Redis snapshot keys flushed between loads, and the same answers from a cold cache.
- **#152:** the filter's inputs counted under each sector.
- **#153:** the tile host blocked and the list taking over.
- **#154:** the stored event rows containing no phone number, id, cookie or coordinate; a burst refused
  at request 300; and the drift test failing on a deleted route.

The browser found things no test did: uncompressed JavaScript in production, a CSS block left inside
an unclosed `@media`, a corrupted vendored library, select popups a keyboard could not drive, and
in-text links that could not be told apart from the text around them.
