# Release v0.4.0: Clinics, Queues & Configuration

**Date:** 2026-09-12 · **Milestone:** M4 · **Issues closed:** 23–30

A pre-release, and the one the rest of the project was waiting for. v0.3.0 left a system that knew
who everyone was and which clinic they belonged to, without a clinic to belong to: a site was an id
on a role assignment and nothing more. This adds the thing itself — a clinic with a position on a
map, opening hours it can be held to, the several named lines a real visit actually goes through,
what it offers and how long each thing takes, what its waiting-room screen may say about a person,
who works in which room, and the way a clinic gets listed at all.

There is still **no ticket**. Nobody joins a queue in this release; the queue engine is M6. What
shipped here is the shape every one of those tickets will hang off, proven against a real PostgreSQL
18 + PostGIS server and by driving the two new pages in a browser.

Three decisions shape it. **A clinic's position is a PostGIS `geography(Point, 4326)` column with
its GiST index from day one**, because M5 must not have to migrate a populated table to search by
distance. **The waiting-room board's privacy default is held by a guard that reads source**, not by
a test of today's creation paths, because the failure that rule actually has is the *next* path
somebody writes. And **every decision a clinic makes is one function with one writer** — the
lifecycle, the join gate, the display settings — because four consumers derive behaviour from each
of them and one direct write outside is how they start disagreeing silently.

## What shipped

- **A clinic exists, with a real position on a map** (Issue 23, PR #138; migration `0007`). `site`
  carries name, slug, sector, status, a `geography(Point, 4326)` location, address and phone.
  **The GiST index is created before there is a single row**, and it is proven from the query plan
  rather than from its existence: a `postgres`-marked test runs `EXPLAIN` over the `ST_DWithin`
  predicate and asserts the plan names `ix_clinicq_site_location_gist` and contains no `Seq Scan`,
  with a companion test compiling the service's own clause to prove the plan describes the query the
  application runs. `PointGeography` wraps GeoAlchemy2 in a `TypeDecorator` so the same models
  create on the SQLite test database, and so the index stays this project's, named by this project's
  convention and visible in the migration diff. **Geocoding is proxied by the server**: the
  Content-Security-Policy allows `connect-src 'self'`, so a browser cannot reach a geocoder at all;
  it is off unless configured, and an unconfigured deployment answers 503 and asks the operator for
  the coordinate. A location is validated against South Africa's bounding box, so `0, 0`, a swapped
  pair and a dropped minus sign are each refused with the numbers in the message.
- **The system knows whether a clinic is open** (Issue 24, PR #139; migration `0008`). Four tables,
  in the order they resolve: an ad-hoc **closure** beats a **public-holiday rule** beats the
  **weekly schedule**. Hours are stored per *span*, so a lunch break, a split shift and a span that
  crosses midnight are all one shape. `is_open_now()` and `next_open_at()` are pure functions of a
  schedule and a moment — no session, no clock — and `next_open_at(s, m) == m` is exactly
  `is_open_now(s, m)`, so a screen cannot show "closed" beside "opens at 09:00" when it is 09:30.
  The public holidays are **computed, not typed**: Easter by the Gregorian algorithm, the twelve
  proclaimed days, and section 2(1) of the Public Holidays Act 36 of 1994 — a holiday on a Sunday
  earns the Monday after it. A closure **raises an event and sends nothing**, on a new in-process
  bus, because a clinic must be able to close while the SMS gateway is down.
- **A clinic runs several named queues** (Issue 25, PR #140; migration `0009`). A real visit is
  triage → doctor → pharmacy, so `queue` carries kind, room label, ticket prefix, display order,
  expected service minutes, daily capacity and `allows_remote_join`, unique **per site** because
  every clinic has a Triage and they are different queues. **Deactivation hides a queue from new
  joins and keeps its history**: the base query filters `is_deleted` and deliberately not
  `is_active`, with the reason written beside it. A walk-in-only queue is refused **on the server**;
  a USSD session has no button to hide, so the channel menu reports the server's answer and its
  reason beside each queue.
- **Every clinic starts showing ticket numbers only** (Issue 27, PR #141; migration `0010`). This is
  where **non-negotiable 4** begins. Five columns on `site`, each with its safe value as a *server*
  default so a restore or a hand-written `INSERT` gets it too. The rule is held by a guard that
  walks the source of `src/`, `scripts/` and the factories and fails on any code constructing a
  `Site` that names `display_mode` or `display_show_comment` — wherever it is, including a path
  added tomorrow — with two fixtures proving the walk can fail. Changing the mode takes a
  clinic-manager grant, an **explicit confirmation in the request** (so a client that renders no
  warning cannot make the change) and an audit row naming the fields that moved; a reason beside a
  **full** name takes a second, separate confirmation. The warning text lives next to the rule it
  describes and is served to the screen, so the sentence a manager reads and the rule the server
  enforces are the same string.
- **A clinic lists what it offers** (Issue 26, PR #142; migration `0011`). `clinic_service` —
  named so, not `Service`, because every module here has a `service.py` and the two would sit one
  letter apart meaning different things. Expected minutes are the wait estimator's prior until real
  samples exist (Issue 42), so they are validated to 1..240: zero would make an estimate divide by
  nothing and a value in hours would report a wait in days. A new clinic gets six primary-care
  services with the minutes the demo dataset uses, and deactivating one removes it from new joins
  and never from history.
- **Staff are linked to clinics and rooms** (Issue 28, PR #143; migration `0012`). Site membership
  **reuses the kernel's scoped role assignment** — there is deliberately no `staff_site_assignments`
  table, because a second answer to "who works here" is a second answer that can disagree with the
  one authorization uses. Room membership is new, and one function writes both it and the kernel's
  queue-scoped `user_roles` row that the scope resolver reads, with a test that they never diverge.
  **Removing somebody takes effect on their next request**, proven with an access token minted
  *before* the change and used after it — nothing about which clinics or queues somebody reaches is
  a claim in a token. A clinic always keeps at least one clinic manager.
- **A clinic can sign itself up** (Issue 29, PR #144; migration `0013`). A public registration form
  on the front door, and `POST /sites/register`, which takes no session and **grants nothing**: the
  clinic it creates is `pending_verification`, invisible to every patient-facing surface, with no
  account, role or session attached, and it arrives with its default queues and catalogue so an
  approving admin sees a working clinic. The verification console follows the list-view pattern — a
  tab per URL, a filter bar, sortable columns, an anchored slideover. The listing lifecycle is a
  small state machine with **one writer**, and a rejection or a suspension must say what is wrong,
  because the submitter is shown exactly what the admin writes. **An unverified clinic never appears
  in a discovery search**, asserted against the search service rather than a page, and suspending
  one stops joins on the next read.
- **The API is written down, and the application is checked against it** (Issue 30, PR #145).
  `contracts/sites.yaml`: 45 operations across 31 paths, 54 schemas, hand-written from the routers,
  with error responses beside the happy paths and examples throughout. The drift harness checks it
  in **both directions** — an undocumented route fails the suite and names it, and so does a
  documented path with no handler — validates every example against the schema it claims to be an
  example of, and is **written to be reused**: the discovery, queue, notifications, channels and
  reporting contracts each add one `Contract(...)` line and inherit all of it.

## Migrations

Seven revisions, `0007` to `0013`, applied in order by `scripts/db/deploy-sequence.sh` before the
new release serves traffic. Each adds tables or nullable columns and drops nothing, so the
**previous release keeps running on the new schema** during a deploy and after a rollback, and each
is reversible — proven by a `postgres`-marked round-trip test that downgrades to base and upgrades
to head, with `alembic check` finding no drift afterwards.

- **`0007_sites`** — new `site`: slug (unique), name, sector, status, `geography(Point, 4326)`
  location, address, suburb, city, province, postal code, phone, notes. Two indexes: **GiST on
  `location`**, which is the whole reason this column lands in M4 rather than M5, and a composite on
  `(sector, status)` for the directory's own filter. The `geography` type is `public`'s, installed by
  the baseline's `CREATE EXTENSION postgis`.
- **`0008_site_hours`** — four new tables, in the order of precedence they resolve in:
  `site_opening_hours` (one row per *span*), `public_holiday` (the country's calendar, **not**
  site-scoped: 16 June is 16 June for every clinic), `site_holiday_rule` (one clinic's answer for
  one holiday) and `site_closure` (a reason, a window, who announced it, whether it was lifted).
  `site_closure.announced_by` is `RESTRICT`: a deactivated account is still the author of what it
  announced.
- **`0009_queues`** — new `queue`. `(site_id, slug)` and `(site_id, name)` are unique **per site**,
  and the composite index `(site_id, is_active, display_order)` is what the board and the four
  channel menus read on every refresh.
- **`0010_site_display_settings`** — five columns on `site`: `display_mode`, `display_show_comment`,
  `board_language`, `announce_audio`, `reason_retention_days`. Each carries its safe value as a
  **`server_default`**, which is the half a Python-side default would miss — a row inserted by a
  restore or a hand-written `INSERT` gets `number_only` too. Existing rows are backfilled to exactly
  where a new clinic would be.
- **`0011_clinic_services`** — new `clinic_service` (unique per site, like a queue) and
  `queue_clinic_service`, the link between a queue and the services it handles. The link is a plain
  association table rather than a mapped model: it carries no data of its own and both ends are
  already site-scoped by their parents, so a class would add a row to the tenancy guard's surface
  for nothing.
- **`0012_staff_queue_assignments`** — new `staff_queue_assignment`. It carries `site_id` alongside
  `queue_id` even though the queue knows its clinic, and that is about the tenancy guard rather than
  normalisation: it puts every read through `scoped_select` like every other site-scoped read (the
  guard *discovers* the model by that column). **A downgrade does not remove** the kernel's
  queue-scoped `user_roles` rows this table shadows — they belong to the kernel's schema and predate
  this revision; re-upgrading and re-saving an assignment brings the two back into step.
- **`0013_site_onboarding`** — seven nullable columns on `site` (contact name, email and phone;
  submitted, reviewed and reviewed-by; review note) and the console's `(status, submitted_at)` index.
  **There is no `site_registration` table**, deliberately: a submission *is* the clinic, and a
  separate row would mean copying everything across on approval and then keeping two records of the
  same place, one of which goes stale.

## Upgrade notes

- **One new runtime dependency: `GeoAlchemy2==0.20.0`**, used by one module. `pip install -r
  requirements.txt` before `alembic upgrade head`; the Docker image installs the same file.
  `jsonschema==4.26.0` and `types-shapely==2.1.0.20260728` are test-time only.
- **Five new settings**, all with defaults, so nothing is required (`.env.example` now lists 123):
  `GEOCODING_PROVIDER` (`none`), `GEOCODING_BASE_URL`, `GEOCODING_USER_AGENT` (empty),
  `GEOCODING_COUNTRY_CODE` (`za`) and `GEOCODING_TIMEOUT_SECONDS` (5). **Geocoding stays off until a
  provider and a descriptive `User-Agent` are both set**; until then `POST /sites/geocode` answers
  503 and an operator types the coordinate in, which is a supported path rather than a failure mode.
  Run `make check-config` after copying; `make env-example` regenerates the file.
- **`GET /api/v1/sites` now needs a `business`-tier grant on `sites`**, which only `platform_admin`
  holds. A clinic manager's role is held *at a site* and does not satisfy a gate on a route that
  names none, so a manager reads their own clinic by its id. Their "which clinics do I work at" list
  is the site switcher's, and that arrives with Issue 48.
- **`make seed-dev-data` now writes clinics, hours, queues, services and holidays**, and prints a
  line per step. Each step is idempotent **by clinic**: a clinic that already has hours, queues or a
  catalogue is left exactly as it is, so a re-run cannot overwrite a manager's real decision.
- **The demo staff's role assignments changed shape.** They were scoped by a clinic *slug*, which
  nothing could resolve; they are now scoped by the `site` row's id. A development database seeded
  before this release keeps the old assignment and reaches no site route — **re-run
  `make seed-dev-data`**; it is idempotent and will repair the rest.
- **`scripts/db/demo_dataset.py`'s local `Province` enum is now `src.commons.enums.SaProvince`**,
  re-exported under the same name. All nine provinces, one spelling, so the demo data, the model and
  every future district report group by the same values.
- **No RBAC change.** The `sites` and `queues` resource trees and their grants were declared by Issue
  18 and seeded in v0.3.0; this release adds routes *under* them, not permissions. `make seed-rbac`
  still runs in the deploy sequence and will report nothing new for these.
- **Two new public surfaces**, both of which grant nothing: `GET /register-clinic` (the front-door
  form) and `POST /api/v1/sites/register`. Each carries its reason in the two allow-lists that would
  otherwise fail the build.

## Known issues

- **Nobody can join a queue yet.** There is no `tickets` table, no join route and no call-next: that
  is M6. The server-side join gate (`join_gate`) and the walk-in-only check both exist and are
  tested, because closures, suspension and remote-join rules had to be decided here — but nothing
  calls them from a patient-facing route until Issue 40.
- **The retention ceiling is an interim 90 days.** Issue 27 had to choose one rather than leave
  `reason_text` unbounded, and the reasoning is recorded next to the constant so the M13 data map
  (Issue 95) can argue with it rather than guess at it: POPIA s 14, a purpose that is one visit, and
  a quarter as the longest operational use anyone has named. Changing it is one line.
- **A brand-new clinic's first manager still cannot be appointed.** A platform admin is deliberately
  assigned to no clinic (Issue 19), so the site guard 404s them on `POST
  /sites/{site_id}/staff/{user_id}/roles` — their own operation. This was raised as a follow-up in
  PR #136 and is still open; today the path is a platform admin editing `user_roles`, which is
  exactly what the onboarding workflow was meant to remove. It needs an operator-tier route of its
  own, and it should land before the pilot.
- **Neither new public surface is rate-limited.** A determined stranger can fill the verification
  queue with rubbish, and the geocoding proxy has no quota of its own — which matters as soon as a
  provider with a paid quota is configured. The shared limiter already exists; both want an entry.
- **Three columns are stored and mean nothing yet**: `queue.max_daily_capacity` (enforced by the
  join service, Issue 40), `clinic_service.requires_appointment` (Issue 80), and
  `site.announce_audio` with `site.board_language` (the board and its announcements, Issues 56 and
  60). Each is here because adding the column now costs nothing and adding it to a populated table
  later costs a migration — the same reasoning as the PostGIS column.
- **A service's `expected_minutes` and its queue's `expected_service_minutes` can disagree** about
  the same line. Both are the estimator's prior and Issue 42 will have to pick between them; it is
  better decided there, with the real samples in view, than guessed at here.
- **The contract is not published anywhere a consumer can fetch it.** It is in the repository and
  checked by CI, but C and B read it from a checkout. A static route or a release artefact belongs
  with the frontend's own setup.
- **C and B have not confirmed they can build against the contract alone.** That is Issue 30's last
  acceptance criterion and the one thing this release cannot assert for itself: it is two other
  people's sign-off, requested in PR #145.
- **The board's server-side privacy projection is M8's, not this release's.** Issue 27 settles what a
  clinic may *choose*; "under `number_only` a name is not in the payload at all" is enforced when the
  board is built (Issue 58). Until then the settings are a decision with nothing reading it, which is
  the safe order to do it in but is worth knowing.
- **Everything from v0.3.0's list still stands**, unchanged by this release, except one: the
  "there is no `site` table yet" entry is now closed. The one-time-code store is still process-local
  and needs Redis before scaling out; the consent wording is still F's unreviewed draft; two of the
  three audit proofs Issue 20 asked for still wait on Issues 46 and 43 (**the third, a display-mode
  change, is now covered** by Issue 27's tests); `actor_role` is still not filled centrally; the
  credential in the repository's history has still not been rotated; and nothing is provisioned.

## Verification

Run on `Issue/30/sites-openapi-contract-tests` at `21ff9b4`, with PostgreSQL 18 + PostGIS 3.6 and
Redis 8 in the development stack:

```text
TZ=UTC pytest -q -n auto              1431 passed, 27 skipped, 9 xfailed in 33.47s
pytest -q -m "postgres or redis"      27 passed, 1440 deselected in 50.30s
```

`TZ=UTC` is not decoration. The suite is run in the pipeline's zone as well as this machine's,
because SQLite hands business datetimes back naive and a comparison that reads them in the
**server's** zone is correct on a developer's SAST laptop and two hours out in a container. That is
a bug this milestone actually shipped to CI — a clinic that had just closed still reported itself
open — and `src.commons.time.stored_sast` is the fix, with a regression test that sets the process
zone and fails against the old code on any machine.

CI ran the same suites in three shards on every one of PRs #138–#145, with `REQUIRE_POSTGRES_TESTS`
and `REQUIRE_REDIS_TESTS` set so a missing service container fails rather than skips, and
`deploy-sequence.sh` applied `0001`–`0013` in order on each. Because the eight branches are
**stacked**, the `Conventions` job (every commit starts with this issue's prefix) is red on each PR
until its predecessors merge; it clears as they land, and every other job is green.

Beyond the suites, each issue's pull request carries a transcript of its own routes driven against a
throwaway PostgreSQL database created, migrated and seeded for the purpose, and the three new pages
(`/register-clinic`, `/admin/verification` and the display settings) were driven in a real browser
and photographed light and dark at 2x. **Everything the browser found, no test caught**: the CSRF
cookie-name bug, the display-options gate bug, a slideover reaching for the wrong namespace, a
registration form that painted two untouched fields red on load, and two elements shipped with no
style at all. A passing suite says the routes answer correctly; it says nothing about whether the
page is usable, and this milestone is the evidence for that.
