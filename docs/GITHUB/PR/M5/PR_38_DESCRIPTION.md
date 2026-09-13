# PR: Anonymous discovery analytics, a rate-limited search, and the discovery contract (Issue 38 / M5-38)

**Milestone:** [Milestone 5: Discovery & Geolocation](https://github.com/Billykat7/clinicQ/milestone/5) ·
**Issue:** [#38](https://github.com/Billykat7/clinicQ/issues/38) · **Builds on:** #30 (the drift test,
merged), and #31–#37 in the stack (PRs #147–#153)

> **Merge order:** last, after PRs #147 → #148 → #149 → #150 → #151 → #152 → #153. The issue says to
> start only once #31–#37 are merged. They are not merged yet, so this branch is stacked on #33's, and
> the Conventions check fails on the earlier issues' commits until they merge. Every test job passes.
> **This is M5's last issue**, so it carries the milestone's release note,
> [`RELEASE_v0_5_0.md`](../../RELEASES/RELEASE_v0_5_0.md). The `v0.5.0` tag is cut once this merges.
>
> **Who did what, to agree in review:** the spec names F as the single owner. The sprint plan gives
> the events to C and the contract to F, and the issue asks for that split to be agreed first.
> Nobody was available to agree it with, so both halves are here, one per commit: the events and the
> route wiring, then the manager's switch and report, then the contract. Either lane can take over its
> half in review without untangling the other.

A clinic manager's first question is "how many people saw us and did not come?". Recording a view
and a join as separate events answers it. What these events must never do is identify a patient, so
that rule is enforced in **what the table can hold**, and a test proves it by reading the stored rows.
The same PR stops the public directory being copied wholesale, and writes the discovery API down as a
contract that the M10 channel adapters can build against without reading the code.

## Summary

- **Four events** in `discovery_event` (migration `0017`): `search_performed`, `clinic_viewed`,
  `join_started`, `join_completed`. Each has a channel (`web`, `api`, `ussd`, `whatsapp`), the time,
  the Johannesburg service day, and a **session reference that rotates daily**.
- **No identifier and no position.** There is no user, patient, phone, IP or coordinate column. A
  search keeps *how* it was made (position or area), its radius, sector and result count, and never
  *where from*.
- **A clinic can opt out** (`site.analytics_enabled`), and the whole feature has a switch
  (`DISCOVERY_ANALYTICS_ENABLED`).
- **A view-to-join report per clinic** for its manager, and a platform-wide `conversion_by_site` for
  the M12 reports (Issue 89 shows them).
- **Rate limits on the public search**, per address and per session, returning `429` with
  `Retry-After`.
- **`contracts/discovery.yaml`** for every route under `/api/v1/clinics`, checked by #30's drift test
  through one added `Contract(...)` line.

## Design notes

**How the session reference rotates.** A browser gets a random `clinicq_discovery_session` cookie:
httpOnly, `SameSite=Lax`, `Secure` outside development, expiring at midnight in Johannesburg. An
event does not store the cookie. It stores `HMAC(key, "<service day>:<token>")`, truncated to 32 hex
characters, under a key derived from the application secret with its own context string. One visit's
events on one day share a reference; the next day's do not, even with the same cookie. Without the
server key a token cannot be matched to its reference, and the reference cannot be turned back into
the token. A caller with no cookie (most API clients) is counted and linked to nothing.

**Recording never breaks a patient's request.** Each event is written in its own savepoint. A
failure is logged and dropped, and the search, page or join goes ahead (unit-tested with a failing
session). With analytics off, the recorder does not touch the session at all.

**What counts as a search.** The first page of results. *Show more* is the same search and is not
counted again. The API's `/nearby` and `/{slug}` record under the `api` channel; the join events are
recorded by the join flow in Issue 40, which calls `record_join_started` and `record_join_completed`.

**Two budgets, and why the address one is loose.** `enforce_discovery_search_limit` uses the
kernel's sliding-window limiter, like the other public endpoints. It counts `ip:<address>` against
`DISCOVERY_SEARCH_RATE_LIMIT_PER_IP` (300 a minute) and, when the cookie is sent,
`session:<token>` against `..._PER_SESSION` (60 a minute). South African mobile networks put many
phones behind one carrier-grade NAT address, so a tight per-IP limit would refuse a whole township's
patients because of one scraper. The per-session budget is the one a single browser meets. A script
that drops the cookie still meets the address budget.

The typeahead and the three `/discover` search routes share the budget, because they are the same
scraping risk. A person typing a suburb costs a handful of requests (the typeahead waits 300 ms),
well inside 60.

**Opting out stops counting; it does not delete.** Turning the switch off stops new events about the
clinic immediately. Events already recorded stay, so reports about past days do not change under the
people reading them. `site_id` has no foreign key for the same reason: last month's report should
outlive a clinic's listing.

**The report's shape.** `GET /api/v1/sites/{site_id}/reports/discovery-conversion` covers the last 30
service days by default and at most 366. A backwards range is a `422`, and the rate is `null`, not
`0`, when nobody viewed the clinic. It is the first route on `sites.reports`, and the switch is the
first on `sites.settings`, so both came out of the cross-tenant suite's PENDING list and into cases.

**The contract, from the routers as they are.** It documents 6 operations with their `404`, `422` and
`429` answers, the `Retry-After` header and the budgets. It says in words what *approximate* (an area
centroid), *not measured* (`null` is not `0`) and *clinic-reported* mean. It has 41 examples, all
validated against their own schemas by the existing test. The routers now declare their `422` and
`429`, so FastAPI's own document names them and the status check can see them. The HTML pages under
`/discover` are not an API and are out of it. The manager's two routes name a `{site_id}`, so they go
in `sites.yaml` (now `0.5.0`) with every other route behind the site guard.

**Out of scope:** the reports' UI (Issue 89), and the join flow that calls the join recorders (Issue 40).

## Changes

- **`alembic/versions/0017_discovery_event.py`**, **`src/database/models/discovery_event.py`** (new);
  **`src/database/models/site.py`:** `analytics_enabled`.
- **`src/modules/discovery/analytics.py`** (new): `new_session_token`, `session_ref`, `record_search`,
  `record_clinic_viewed`, `record_join_started`, `record_join_completed`, `conversion_for_site`,
  `conversion_by_site`, `SiteConversion`.
- **`src/commons/enums.py`:** `DiscoveryEventKind`, `DiscoveryChannel`. **`src/core/config.py`:**
  `DISCOVERY_ANALYTICS_ENABLED` and three `DISCOVERY_SEARCH_RATE_LIMIT_*` settings; **`.env.example`**
  regenerated.
- **`src/core/rate_limit.py`:** `discovery_search_limiter`. **`src/core/rate_limit_deps.py`:**
  `DISCOVERY_SESSION_COOKIE`, `enforce_discovery_search_limit`, `DiscoverySearchLimit`.
- **`src/modules/discovery/router.py`:** the limit on `/nearby` and `/areas`; search and view events;
  declared `422`/`429`. **`src/web/discover.py`:** the limit on `/discover`, `/discover/results` and
  `/discover/areas`; the session cookie; search and view events.
- **`src/modules/sites/router.py`**, **`schemas.py`:** `GET`/`PUT /{site_id}/settings/analytics`,
  `GET /{site_id}/reports/discovery-conversion`.
- **`contracts/discovery.yaml`** (new); **`contracts/sites.yaml`**, **`contracts/README.md`**.
- **Tests (new, 25):** `tests/integration/discovery/test_discovery_analytics.py` (10, PostGIS),
  `tests/integration/sites/test_discovery_analytics_api.py` (7),
  `tests/unit/discovery/test_analytics_session_ref.py` (7), and in
  `tests/integration/contracts/test_openapi_contracts.py` the discovery `Contract` entry plus
  `test_a_discovery_route_added_without_documenting_it_is_caught`.
  **Guards:** cross-tenant cases for `discoveryevent`, `sites.reports` and `sites.settings`; the site
  guard's reasoned exception for `conversion_by_site`.
- **Release note:** `docs/GITHUB/RELEASES/RELEASE_v0_5_0.md` (new). It covers what #147–#154 shipped,
  migrations `0014`–`0017` and whether each is reversible, the upgrade notes, and the known issues,
  including the partial exit criteria and what still stands from v0.4.0.
- **Milestone close:** `docs/GITHUB/MILESTONES/M5_discovery_geolocation.md` (Status, exit criteria
  with the partial ones marked), `README.md` Status, `docs/TEAM/WORKLOAD_SPLIT.md` sprint rows,
  `docs/PLAN/IMPLEMENTATION_PLAN.md` gantt.

## Testing

- [x] `ruff check .` and `ruff format --check` clean (392 files); `mypy src/` clean (227 files).
- [x] Full suite with PostgreSQL and Redis required, in UTC: **1659 passed, 9 xfailed**.
- [x] The drift test on both contracts: 18 passed.
- [x] **Transcript** from a fresh database migrated to `0017` and seeded with `seed_dev_data`, driving
      the real app (`python v38.py`, the same harness as the earlier M5 PRs):

```text
== One visit: a signed-in patient (phone +27825550199) searches, opens a clinic, joins
$ GET /discover?lat=-26.205&lon=28.04 -> 200
  set-cookie: clinicq_discovery_session =<token>; HttpOnly; Max-Age=74953; Path=/; SameSite=lax
$ GET /discover/clinics/hillbrow-chc -> 200
$ analytics.record_join_completed(...)   # what the join flow (Issue 40) will call

$ SELECT * FROM clinicq.discovery_event ORDER BY occurred_at
  columns: id, kind, channel, occurred_at, service_day, session_ref, site_id, sector, origin_basis, radius_m, result_count
   {'kind': 'search_performed', 'channel': 'web', 'occurred_at': '2026-09-13 01:10:46.302857+00:00', 'service_day': '2026-09-13', 'session_ref': '15dab2e3685351163179a59096c27806', 'site_id': None, 'sector': 'all', 'origin_basis': 'position', 'radius_m': '10000', 'result_count': '2'}
   {'kind': 'clinic_viewed', 'channel': 'web', 'occurred_at': '2026-09-13 01:10:46.597440+00:00', 'service_day': '2026-09-13', 'session_ref': '15dab2e3685351163179a59096c27806', 'site_id': '01a09851-114e-7784-9f3d-dbeed2d1cb06', 'sector': None, 'origin_basis': None, 'radius_m': None, 'result_count': None}
   {'kind': 'join_completed', 'channel': 'web', 'occurred_at': '2026-09-13 01:10:46.633036+00:00', 'service_day': '2026-09-13', 'session_ref': '15dab2e3685351163179a59096c27806', 'site_id': '01a09851-114e-7784-9f3d-dbeed2d1cb06', 'sector': None, 'origin_basis': None, 'radius_m': None, 'result_count': None}
  contains phone? False
  contains phone digits? False
  contains patient id? False
  contains cookie token? False
  contains latitude? False
  contains longitude? False

== The clinic manager's report and switch (manager@clinicq.example, Hillbrow CHC)
  sign in: 200
$ GET /api/v1/sites/<hillbrow>/reports/discovery-conversion -> views=1 joins_completed=1 rate=1.0 range=2026-08-15..2026-09-13
$ PUT /api/v1/sites/<hillbrow>/settings/analytics {analytics_enabled: false} -> 200 False
  after another view of the opted-out clinic: views=1 analytics_enabled=False
  audit: discovery analytics off for hillbrow-chc

== Hammering the public search from one address (defaults: 300/min per IP, 60/min per session)
  320 requests in 6.2 s: {200: 299, 429: 21}
  first 429 at request 300, Retry-After: 60, detail: 'Too many searches in a short time. Please wait a minute and try again.'
  (the first 429 is at 300, not 301: the patient's /discover page above spent one of this address's 300)
$ reset the limiter, then one browser session searching from one address
  one session, 62 searches: 60 x 200, then 2 x 429 (first at 61)
```

      `occurred_at` prints in UTC because the harness connection runs in UTC; the stored value
      carries its offset, and `service_day` is the Johannesburg date.

- [x] **The drift test failing.** With `/clinics/{slug}` deleted from `discovery.yaml`:

```text
$ pytest tests/integration/contracts/test_openapi_contracts.py::test_every_route_the_application_serves_is_documented
E       AssertionError: discovery.yaml does not document these routes, which the application serves:
E           GET /api/v1/clinics/{slug}
E       assert not {('GET', '/api/v1/clinics/{slug}')}
1 failed, 1 passed
```

      The same check also runs on every build as a test:
      `test_a_discovery_route_added_without_documenting_it_is_caught` adds `GET /api/v1/clinics/export`
      to the real application and asserts the comparison names it.

## Acceptance criteria

- [x] **View and join events are captured separately and joinable per site.** They are distinct kinds
      carrying `site_id`, indexed on `(site_id, kind, service_day)`.
      `test_the_conversion_numbers_count_views_and_joins_per_clinic` and the transcript's report.
- [x] **Conversion rate per site is queryable and appears in the M12 report set.** **Partly:**
      `conversion_for_site` (behind the manager's route) and `conversion_by_site` (platform-wide) are
      the queries. The M12 report set does not exist yet; Issue 89 shows them.
- [x] **`discovery.yaml` documents every discovery route, enforced by the drift test.** One `Contract`
      entry. The failure is shown above and tested on the real app.
- [x] **The search endpoint is rate limited per IP and per session.**
      `test_hammering_the_search_from_one_address_is_refused`,
      `test_one_session_has_its_own_smaller_budget` and `test_the_web_search_pages_share_the_budget`,
      and the transcript's 429s.
- [x] **No event row stores a phone number or a precise location.**
      `test_a_search_a_view_and_a_join_store_nothing_that_identifies_the_patient` reads the rows with
      plain SQL, checks that the column set is exactly the eleven listed (so a new column fails the
      test), and checks that the patient's phone, id, cookie token and coordinates are absent. The
      transcript shows the same.
- [x] **Analytics can be disabled per site if a clinic objects.**
      `test_a_clinic_that_opts_out_is_never_counted` and
      `test_opting_out_is_audited_and_stops_new_views_being_counted`. The whole feature also switches
      off (`test_analytics_switched_off_records_nothing_and_changes_no_answer`).

## Risk and rollback

**Migration `0017`** adds one table and one column with a server default, and it is reversible. The
previous release runs against it unchanged. With `DISCOVERY_ANALYTICS_ENABLED=false`, the only change
a patient can notice is the rate limit.

**What to watch after deploy:**

- **The limiter is per process** unless `RATE_LIMIT_BACKEND=redis` is set, as it is for the kernel's
  other limiters. Without it, several workers multiply the effective budget by the worker count.
- **Any change to `JWT_SECRET` changes every future session reference.** That is harmless (references
  only need to be stable within a day) but worth knowing.

Rollback is `alembic downgrade 0016` (which drops the recorded events with the table) and a revert.

**Follow-ups:** the join flow (Issue 40) calls the two join recorders; Issue 89 renders the report; a
retention window for `discovery_event` should be set with the M12 data-retention work (F).

Closes #38
