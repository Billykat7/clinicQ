# PR: One clinic, before a patient decides to go: hours, live queues, services, contact (Issue 35 / M5-35)

**Milestone:** [Milestone 5: Discovery & Geolocation](https://github.com/Billykat7/clinicQ/milestone/5) ·
**Issue:** [#35](https://github.com/Billykat7/clinicQ/issues/35) · **Builds on:** #24 (opening hours, merged),
#31 (PR #147), #34 (PR #148), #32 (PR #149)

> **Merge order:** after #31, #34 and #32 (PRs #147–#149). This branch is stacked on them, so the
> Conventions check fails on their commits until they merge. Every test job passes.

The detail page is where a patient decides to commit to a clinic, so it answers four questions at
once: is it open (and can I join), how long are the queues, what does it treat, and how do I get
there. It lives in the same page family as the list (`src/web/discover.py`,
`src/templates/discover/`), and every name in the list now links to it. The facts come from one
service call, `clinic_profile()`, which returns plain data. The JSON API
(`GET /api/v1/clinics/{slug}`) and later the channel menus read the same function.

## Summary

- **`/discover/clinics/{slug}`**: open or closed with when it opens, today's hours, the join action,
  each queue's length and wait, directions, tap-to-call, the week's hours, services, and a payment
  section for private clinics. One column that wraps rather than scrolls at 320 px.
- **The join action is always there.** It is enabled only when joining works. Otherwise it is
  disabled with the reason beside it: closed and when it opens, a closure in the manager's words,
  walk-in-only queues, no queues set up, or joining from a phone not switched on yet.
- **Hours come from #24**: `open_state()` over the clinic's schedule, which is `is_open_now()` and
  `next_open_at()` resolved together, so "Open now" and "Opens tomorrow at 07:00" cannot disagree.
- **Waits are a range or nothing.** Until the estimator (#42) exists each queue says *Wait estimate
  not available yet* and shows its length only. There is no invented figure, and `WaitRange`
  refuses a single number.
- **Live figures refresh through htmx** from `/discover/clinics/{slug}/live` every 30 seconds, well
  inside the milestone's 30-second staleness rule.
- **`GET /api/v1/clinics/{slug}`**: the same profile as JSON. An unverified clinic is the same 404 as
  a missing one on the page, the fragment and the API.

## Design notes

**Why the join button is disabled today, and how that ends.** Joining is Issue 40's (M6), and there is
no join route yet. An enabled button that led nowhere would break the rule this page exists to keep.
So `PATIENT_JOIN_ENABLED` (in `src/core/config.py`, default off, in `.env.example`) decides the last
step. While it is off, a clinic that is open and has a remote-joinable queue shows the button disabled
with *Joining from your phone is not switched on yet. You can join at the clinic's front desk while it
is open.* When Issue 40 ships its route at `/discover/clinics/{slug}/join`, turning the flag on
enables the button with no code change. Both states are tested.

**The reason always comes from the rules, never the template.** `clinic_profile()` asks the one join
gate every channel asks (`src/modules/sites/availability.py:join_gate`) and then whether any active
queue allows remote joins, so a USSD menu will refuse for the same reason in the same words. The web
view only adds when the clinic opens next, using the same `opens_phrase()` as the list's
open/closed label.

**"Full" is not decided yet.** The issue mentions a join button disabled when a clinic is full.
Fullness needs the day's issued tickets counted against `max_daily_capacity`, and there are no
tickets until Issue 39, so no reason says "full". Inventing one would be worse. That check belongs
with Issue 40's capacity guard, which will add its reason to the same `JoinAvailability`.

**Medical aid.** The payment block's data is #37's. This page shows the section for private
clinics only, saying nothing is listed yet and to ask the clinic, and defines
`CLINIC_REPORTED_NOTICE` (*Reported by the clinic. Please confirm with the clinic before you
travel.*) as the one sentence #37's data will be shown with. A public clinic has no payment section
at all.

**Live refresh without noise.** The refreshed element carries its own `hx-get` with
`hx-trigger="every 30s"` and swaps itself (`outerHTML`), so a swap keeps polling. A clinic that
stops being visible answers the fragment with 404, which htmx does not swap, so the last figures
stay rather than the section vanishing. *Updated at* is deliberately not a live region: announcing
it to a screen reader every 30 seconds would drown out the page.

**Directions and phone.** Directions use Google's documented cross-platform URL
(`https://www.google.com/maps/dir/?api=1&destination=lat,lon`), which opens the phone's Maps app
when installed; ClinicQ computes no route, and #33 reuses `directions_href()`. The number is a
`tel:` link grouped for reading aloud (*+27 10 555 0100*). A 24-hour span (00:00 to 00:00, Issue 24's
rule) reads *Open all day*, not *00:00–00:00*, which the browser run caught.

**Out of scope:** joining itself (Issue 40), the payment data (Issue 37) and the wait estimator
(Issue 42).

## Changes

- **`src/modules/discovery/profile.py`** (new): `clinic_profile`, `ClinicProfile`, `JoinAvailability`,
  `DayHours`, `ServiceOffered`, `WALK_IN_ONLY`, `NO_QUEUES`.
- **`src/modules/discovery/router.py`:** `GET /clinics/{slug}`. **`schemas.py`:** `ClinicProfileOut`
  and its parts.
- **`src/web/discover.py`:** `clinic_detail` and `clinic_live` routes; `DetailView`, `LiveView`,
  `JoinButton`, `QueueRow`, `HoursRow`; `join_button`, `spans_label`, `phone_label`,
  `directions_href`, `opens_phrase`; `CLINIC_REPORTED_NOTICE`, `LIVE_REFRESH_SECONDS`.
- **`src/templates/discover/detail.html`**, **`_queue_summary.html`**, **`not_found.html`** (new);
  **`_clinic_card.html`:** the name links to the detail page. **`src/static/css/discover.css`:**
  the detail page's styles, tokens only.
- **`src/core/config.py`:** `patient_join_enabled`. **`.env.example`:** regenerated.
- **`tests/integration/discovery/test_clinic_detail.py`** (new, 13 cases); **`conftest.py`:** seeds
  each clinic's services catalogue; **`tests/unit/discovery/test_discover_labels.py`:** all-day spans
  and phone grouping. **`tests/unit/security/test_api_route_gates.py`:** the public profile route,
  with its reason.
- **`docs/GITHUB/PR/M5/assets/pr35/`:** the screenshots below.

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (220 files); the template
      punctuation check finds nothing in `src/templates/discover/`.
- [x] Full suite with PostgreSQL and Redis required, in UTC: **1579 passed, 9 xfailed**.
- [x] **Browser run** against the migrated, seeded `clinicq_m5_verify` database, driven by Playwright
      on the system Chrome at 01:21 on a Sunday. To show an open clinic at that hour, Hillbrow CHC
      was given Sunday hours of 00:00–00:00 and the demo number `+27 10 555 0100` (a Johannesburg
      test range) in that database only. Transcript:

```text
== 320 px wide
  light hillbrow-chc       scrollWidth 320 / clientWidth 320 | right-most element edge 320px | open: Open now
        | join reason: Joining from your phone is not switched on yet. You can join at the clinic's front desk while it is open.
axe light hillbrow-chc at 320px: 0 violations, 36 rules passed
  light medicross-meldene  scrollWidth 320 / clientWidth 320 | right-most element edge 320px | open: Closed, opens tomorrow at 08:00
        | join reason: Medicross Meldene Medical and Dental Centre is closed at the moment. Opens tomorrow at 08:00.
axe light medicross-meldene at 320px: 0 violations, 36 rules passed
  dark  hillbrow-chc       scrollWidth 320 / clientWidth 320 | right-most element edge 320px | open: Open now
axe dark hillbrow-chc at 320px: 0 violations, 36 rules passed
  dark  medicross-meldene  scrollWidth 320 / clientWidth 320 | right-most element edge 320px | open: Closed, opens tomorrow at 08:00
axe dark medicross-meldene at 320px: 0 violations, 36 rules passed

== Live figures refresh without a reload
  t=0s    open: Open now | Updated at 01:21:24
  psql: closure announced          (INSERT INTO clinicq.site_closure … 'A burst water pipe', now for 2 hours)
  t=30s   open: Closed: A burst water pipe. Opens today at 03:21. | Updated at 01:21:54
  join reason: Hillbrow Community Health Centre is closed: A burst water pipe. Opens today at 03:21.
  same document, no reload: True
  GET /discover/clinics/hillbrow-chc/live requests: 2, seconds apart: [30]
  psql: closure lifted
```

      The first run of the refresh showed the gate's closure reason joined to *Opens today…* with
      no full stop between them. `join_button` now ends the reason with one, and the test pins the
      whole sentence.

- [x] **Screenshots**, 320 px wide at 2x, whole page.

      An open public clinic and a closed private one, light:

      | Hillbrow CHC, open | Medicross Meldene, closed |
      |---|---|
      | ![Hillbrow CHC at 320 px, light](https://github.com/Billykat7/clinicQ/blob/53972cccd8908d7fa8ab79a2e74b7c9d6d51a32e/docs/GITHUB/PR/M5/assets/pr35/hillbrow-chc-320-light.png?raw=true) | ![Medicross Meldene at 320 px, light](https://github.com/Billykat7/clinicQ/blob/53972cccd8908d7fa8ab79a2e74b7c9d6d51a32e/docs/GITHUB/PR/M5/assets/pr35/medicross-meldene-320-light.png?raw=true) |

      The same two, dark:

      | Hillbrow CHC, open | Medicross Meldene, closed |
      |---|---|
      | ![Hillbrow CHC at 320 px, dark](https://github.com/Billykat7/clinicQ/blob/53972cccd8908d7fa8ab79a2e74b7c9d6d51a32e/docs/GITHUB/PR/M5/assets/pr35/hillbrow-chc-320-dark.png?raw=true) | ![Medicross Meldene at 320 px, dark](https://github.com/Billykat7/clinicQ/blob/53972cccd8908d7fa8ab79a2e74b7c9d6d51a32e/docs/GITHUB/PR/M5/assets/pr35/medicross-meldene-320-dark.png?raw=true) |

      Thirty seconds after the closure was announced, with no reload (390 px):

      ![Hillbrow CHC after the live refresh, closed with the reason](https://github.com/Billykat7/clinicQ/blob/53972cccd8908d7fa8ab79a2e74b7c9d6d51a32e/docs/GITHUB/PR/M5/assets/pr35/live-closed-after-refresh.png?raw=true)

## Acceptance criteria

- [x] **Wait times are shown as a range, never a single number.** Until #42 there is no estimate, so
      every queue says *Wait estimate not available yet*
      (`test_wait_times_are_a_range_or_not_shown_never_a_single_number`). When one exists it is a
      `WaitRange`, which refuses a range whose ends are equal, and `wait_label` renders it as
      *Wait about 5–15 min* (unit tests).
- [x] **The join action is disabled with an explanation when joining is not possible.** Closed with
      when it opens, a closure's reason, walk-in-only queues, no queues, and joining not switched
      on: each is a test asserting the disabled button and its exact sentence, and both browser
      states show the reason beside the button.
- [x] **The page is fully readable and usable on a 320 px-wide screen.** `scrollWidth` equals the
      320 px viewport for an open and a closed clinic in both themes, with no element past the right
      edge; the screenshots above are at that width.
- [x] **Live figures refresh via htmx without a full reload.** The browser run announces a closure
      in the database while the page is open; 30 seconds later the status, the join reason and
      *Updated at* have changed in the same document, and the network shows one `/live` request
      every 30 seconds.
- [x] **Medical-aid information is labelled as clinic-reported with a confirm-with-the-clinic note.**
      *Met for what exists; #37 brings the data.* No clinic has payment data yet, so none is shown
      unlabelled: a private clinic's section says nothing is listed and to ask the clinic, a public
      clinic has no section, and `CLINIC_REPORTED_NOTICE` is the one sentence #37's block is
      rendered with (`test_payment_information_is_only_for_private_clinics_and_carries_the_notice`).
- [x] **The page passes an automated accessibility check.** axe-core 4.10.3 (WCAG 2.0/2.1/2.2 A and
      AA plus best practice): 0 violations for both clinics in light and dark at 320 px.

## Risk and rollback

No migration. One new setting that defaults to off, two public pages, one public API route and one
fragment, all reading only what verified clinics publish. The list's cards gain a link. Rollback is a
revert; #36, #37, #33 and #38 are stacked on this.

**Follow-ups noticed:** the "full" refusal needs Issue 40's capacity count; the demo dataset has no
clinic phone numbers, so a real directory needs them entered at onboarding; the seeded address line
repeats the clinic's name, which reads oddly on this page (`scripts/db/seed_dev_data.py`).

Closes #35
