# PR: The first screen a patient sees: nearby clinics, a sector toggle, and no dead ends (Issue 32 / M5-32)

**Milestone:** [Milestone 5: Discovery & Geolocation](https://github.com/Billykat7/clinicQ/milestone/5) ·
**Issue:** [#32](https://github.com/Billykat7/clinicQ/issues/32) · **Builds on:** #31 (PR #147), #34 (PR #148)

> **Merge order:** after #31 (PR #147) and #34 (PR #148). This branch is stacked on them, so the
> Conventions check fails on their commits until they merge. Every test job passes.

`/discover` is the list a patient opens on a cheap Android phone. It shows nearby clinics, public or
private, each with its distance, queue length and whether it is open. It is server-rendered on the
patient layout, and htmx swaps the list when the sector, radius or sort changes. The page calls the
same `find_nearby_sites()` and `search_areas()` the JSON API, USSD and WhatsApp call, so it cannot
disagree with them. Declining the location prompt leads to the suburb search from #34, never to an
empty page.

**When the switch to the real API happened:** there was no fixture phase to switch from. #31 was
written first in this stack, so the page was built on the real discovery service from its first
commit. The fixture it was developed and tested against is the demo directory
(`scripts/db/demo_dataset.py`, seeded by `seed_dev_data`), the same data the team agreed on in
sprint 3.

## Summary

- **`GET /discover`** (whole page), **`/discover/results`** (the fragment htmx swaps) and
  **`/discover/areas`** (the suburb typeahead), each usable with and without JavaScript: htmx gets
  the fragment with `HX-Push-Url`, and a plain browser is redirected to the full page.
- **Two ways in, offered together.** The browser is asked for a location only when the patient
  presses *Use my location*. A refusal, a timeout or a browser that cannot locate says why and moves
  the focus to the suburb search. The position is rounded to about 100 m before it reaches a URL.
- **Public / Private / All, radius and sort** are real radio groups: arrow keys move within each,
  every option is a 44 px target, and a change swaps the list with no reload.
- **Cards lead with what decides the trip:** distance (approximate from a suburb), open or closed
  with when it opens, queue length ("Queue length not reported yet" until tickets exist, never 0),
  the wait ("Wait estimate not available yet" until #42) and a rough travel time.
- **Sector badges differ without colour:** the word, a square or a diamond, and a solid or dashed
  border.
- **Designed states:** a skeleton while a swap is in flight (the component macro), an empty state
  that names the radius and offers the next one, and an error state that keeps the last list and
  shows *Try again* with a toast from `ui-feedback.js`.
- **Sort by shortest queue** in the service, with unmeasured clinics last.
- **A real bug found by the Slow 3G run:** production nginx never compressed JavaScript. Fixed in
  `infra/nginx/platform.conf`.

## Design notes

**The words are decided as data.** `.cursor/rules/testing-strategy.mdc` forbids asserting rendered
HTML, so every sentence a patient reads is built in `src/web/discover.py` as dataclasses
(`DiscoverPage`, `ResultsView`, `ClinicCard`, `EmptyState`). The templates only lay them out, and the
tests assert the data. `queue_label(None)` is "Queue length not reported yet" and only a counted zero
is "No one waiting". `wait_label` is a range or "not available", never one number. `open_label` always
says when a closed clinic opens, in Johannesburg wall-clock time relative to when the search ran.

**Radio groups, not selects.** The first version used `<select>` for radius and sort. The
keyboard-only run could not operate a native select popup headlessly, and on a small phone a native
picker is a scrolling overlay for five choices. Three segmented radio groups behave the same on
every browser, keep one tap per choice, and are submitted by a browser without scripts.

**htmx without surprises.** The filter form is a plain `GET /discover` form with
`hx-get="/discover/results"` on `change`. The fragment's `HX-Push-Url` is the full page's address,
so refresh, back and a shared link all show the same list. A swap with nowhere to search from answers
`422`, which htmx does not swap, and `discover.js` then shows the error panel. `hx-sync="this:replace"`
drops a stale request when the patient taps twice.

**The location prompt respects the patient.** `discover.js` calls `getCurrentPosition` only on the
button press. Any failure calls one function that explains and focuses the suburb input
(*No problem, your location stays private. Type your suburb or township below instead.*). A
position is rounded to three decimals before the page navigates, which is plenty for a clinic list
and means an access log never holds where a patient is standing. The request log already records
paths without query strings.

**The suburb search moves with the page's state.** It comes first when there is nowhere to search
from, and after the results once there is, so a list is never pushed below a form on a small screen.
Its suggestions are plain links, so a tap, Enter or no JavaScript all choose one the same way.

**Shortest queue needs the service.** Queue length is not a column, so `find_nearby_sites(sort=...)`
orders the same bounded candidate set `open_now` uses and paginates afterwards. The constant is
renamed `MAX_CANDIDATES`. Until tickets exist every length is unmeasured, so the order is by
distance, as the tests show with a reader that supplies real counts.

**The nginx fix.** Measuring on Slow 3G showed `htmx-2.0.0.min.js` arriving at 49 KB. `platform.conf`
compresses `application/javascript`, but Starlette serves scripts as `text/javascript` (RFC 9239), so
no script was compressed in production. Adding `text/javascript` (and `image/svg+xml`) brings a cold
load from 183 KB to 108 KB. This is E's file; the change is one line with a comment.

**The landing page's first button** now says *Find a clinic* and goes to `/discover`, so the screen
is reachable from the front door. *See the screens* becomes the quiet button next to it.

**Out of scope:** the map (Issue 33), the clinic detail page (Issue 35), the payment filter (Issue 37)
and the remembered areas on the web page (the API for them is #34's; a signed-in patient's recent
areas on this page are a small follow-up).

## Changes

- **`src/web/discover.py`** (new): the three routes, the page and card dataclasses, the label
  functions, `SECTOR_BADGES` and `BadgeShape`. **`src/main.py`:** router registered.
- **`src/templates/discover/`** (new): `list.html`, `_results.html`, `_more.html`, `_clinic_card.html`,
  `_sector_badge.html`, `_area_suggestions.html`.
- **`src/static/css/discover.css`**, **`src/static/js/discover.js`** (new). No inline style or script;
  `tests/unit/platform/test_ui_shell.py` now also holds `discover.css` to the tokens-only rule.
- **`src/modules/discovery/service.py`:** `sort` (`DiscoverySort`), `MAX_CANDIDATES`, `_queue_order`.
  **`router.py`**, **`schemas.py`:** `sort` on `/api/v1/clinics/nearby`. **`src/commons/enums.py`:**
  `DiscoverySort`.
- **`infra/nginx/platform.conf`:** `text/javascript` and `image/svg+xml` in `gzip_types`.
- **`src/templates/web/index.html`:** *Find a clinic* as the first hero button.
- **`tests/integration/discovery/test_discover_pages.py`** (new, 17 cases);
  **`tests/unit/discovery/test_discover_labels.py`** (new, 13 cases).
- **`docs/GITHUB/PR/M5/assets/pr32/`:** the screenshots below.

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (219 files); the template
      punctuation check finds nothing in `src/templates/discover/`.
- [x] Full suite with PostgreSQL and Redis required, in UTC: **1563 passed, 9 xfailed, 1 failed**.
      The failure is `tests/test_account_security_flow.py::test_email_change_request_does_not_change_the_address`,
      which picks up the SMTP host from the developer's `.env` and failed on a DNS lookup. It passed
      twice when rerun alone on this branch, and it does not touch discovery. Worth an issue of its
      own: the test is not isolated from `.env` (see *Risk*).
- [x] **Browser run** against a migrated, seeded PostgreSQL database (`clinicq_m5_verify`) served by
      `uvicorn`, driven by Playwright on the system Chrome. Transcript, verbatim:

```text
== Decline the location prompt
status: No problem, your location stays private. Type your suburb or township below instead.
focus is on: area-q
suggestion: Soweto · City of Johannesburg Metropolitan Municipality

== Keyboard only (no mouse from the first key press)
  Tab 1: <a> 'Skip to content' visible=True focus-ring=True
  Tab 2: <a> 'BK ClinicQ' visible=True focus-ring=True
  Tab 3: <button> 'Switch to dark theme' visible=True focus-ring=True
  Tab 4: <button> 'Use my location' visible=True focus-ring=True
  Tab 5: <input search> 'Suburb, township or town' visible=True focus-ring=True
  Tab 6: <button submit> 'Search' visible=True focus-ring=True
  (type "Soweeto", Tab, Tab) focused: Soweto · City of Johannesburg Metropolitan Municipality
  Enter -> /discover?area_id=01a097cd-… | 2 clinics within 10 km
  on the toggle: all
  ArrowRight -> …&sector=public | 2 public clinics within 10 km | no reload: True
  ArrowRight -> …&sector=private | empty state: No private clinics within 10 km | no reload: True
  Tab -> radius_m=10000
  ArrowRight on radius -> …&sector=private&radius_m=20000 | 2 private clinics within 20 km
  Tab -> sort=nearest
  ArrowRight on sort -> …&sector=private&radius_m=20000&sort=shortest_queue | no reload: True
  the focused option's label draws an outline: solid

== Automated accessibility check (axe-core 4.10.3, WCAG 2.0/2.1/2.2 A and AA + best practice)
axe light start: 0 violations, 41 rules passed
axe light suggestions: 0 violations, 43 rules passed
axe light results: 0 violations, 45 rules passed
axe light empty: 0 violations, 41 rules passed
axe dark  start: 0 violations, 41 rules passed
axe dark  suggestions: 0 violations, 43 rules passed
axe dark  results: 0 violations, 45 rules passed
axe dark  empty: 0 violations, 41 rules passed

badges (word, border, icon): [('Private', 'dashed', 'path'), ('Public', 'solid', 'rect')]
error state: We could not load clinics just now | toast: No connection. The list shown is the last one that loaded.
after Try again: /discover?lat=-26.2&lon=28.02&sector=public&radius_m=20000 | 3 public clinics within 20 km
```

      The first axe run found one real violation on every page: the OpenStreetMap attribution link
      was told from its sentence by colour alone (`link-in-text-block`). The in-text links are now
      underlined, and the run above is after that fix.

- [x] **Slow 3G and 3G timings.** Chrome DevTools' own presets applied over CDP (Slow 3G: 400 kbit/s,
      2,000 ms latency per request; 3G: 1.44 Mbit/s, 562 ms), a 360×780 phone, served through nginx
      with `platform.conf`'s compression settings, as production serves it:

| Profile | Page | Visit | First contentful paint | Largest contentful paint | Load | Requests / transferred |
|---|---|---|---|---|---|---|
| Slow 3G | `/discover` | first visit | 5060 ms | 5060 ms | 7639 ms | 11 / 108 KB |
| Slow 3G | `/discover` | return visit | n/a | 2100 ms | 2081 ms | 11 / 2 KB |
| Slow 3G | `/discover?area_id=<Soweto>&radius_m=20000` | first visit | 4836 ms | 4836 ms | 7390 ms | 11 / 109 KB |
| Slow 3G | `/discover?area_id=<Soweto>&radius_m=20000` | return visit | n/a | 2096 ms | 2092 ms | 11 / 4 KB |
| 3G | `/discover` | first visit | 1508 ms | 1508 ms | 2111 ms | 11 / 108 KB |
| 3G | `/discover` | return visit | n/a | 624 ms | 621 ms | 12 / 2 KB |
| 3G | `/discover?area_id=<Soweto>&radius_m=20000` | first visit | 1536 ms | 1536 ms | 2117 ms | 11 / 109 KB |
| 3G | `/discover?area_id=<Soweto>&radius_m=20000` | return visit | n/a | 636 ms | 626 ms | 12 / 4 KB |

      Before the nginx fix, the Slow 3G first visit was 6484 ms to first paint with 183 KB
      transferred. Chrome does not report a first-contentful-paint entry for a return visit served
      from cache, so the largest contentful paint is the figure to read there.

- [x] **Screenshots**, from the same run at 2x (360 px wide unless noted).

      The first visit, before any location, light and dark:

      | Light | Dark |
      |---|---|
      | ![Find a clinic before a location is chosen, light](https://github.com/Billykat7/clinicQ/blob/e32d0fd53f8f7fe215f40bc27f4323f09f09b622/docs/GITHUB/PR/M5/assets/pr32/start-light.png?raw=true) | ![Find a clinic before a location is chosen, dark](https://github.com/Billykat7/clinicQ/blob/e32d0fd53f8f7fe215f40bc27f4323f09f09b622/docs/GITHUB/PR/M5/assets/pr32/start-dark.png?raw=true) |

      Location declined: the reason under the button, and the suburb search with focus and a
      suggestion for the misspelt "Soweeto":

      ![The location prompt declined, with the suburb search focused](https://github.com/Billykat7/clinicQ/blob/e32d0fd53f8f7fe215f40bc27f4323f09f09b622/docs/GITHUB/PR/M5/assets/pr32/declined-location-light.png?raw=true)

      The list near Soweto, 20 km, whole page, with the approximate-distance note:

      | Light | Dark |
      |---|---|
      | ![Clinics near Soweto, light](https://github.com/Billykat7/clinicQ/blob/e32d0fd53f8f7fe215f40bc27f4323f09f09b622/docs/GITHUB/PR/M5/assets/pr32/results-light.png?raw=true) | ![Clinics near Soweto, dark](https://github.com/Billykat7/clinicQ/blob/e32d0fd53f8f7fe215f40bc27f4323f09f09b622/docs/GITHUB/PR/M5/assets/pr32/results-dark.png?raw=true) |

      The sector badges in greyscale: the word, a square or a diamond, a solid or dashed border.
      Open and closed are words:

      ![Two clinic cards in greyscale, Public and Private badges](https://github.com/Billykat7/clinicQ/blob/e32d0fd53f8f7fe215f40bc27f4323f09f09b622/docs/GITHUB/PR/M5/assets/pr32/badges-greyscale.png?raw=true)

      The empty state (private clinics within 2 km of Soweto) and the error state (the connection
      dropped during a swap; the last list stays, with *Try again* and a toast):

      | Empty, light | Empty, dark | Error |
      |---|---|---|
      | ![No private clinics within 2 km, light](https://github.com/Billykat7/clinicQ/blob/e32d0fd53f8f7fe215f40bc27f4323f09f09b622/docs/GITHUB/PR/M5/assets/pr32/empty-light.png?raw=true) | ![No private clinics within 2 km, dark](https://github.com/Billykat7/clinicQ/blob/e32d0fd53f8f7fe215f40bc27f4323f09f09b622/docs/GITHUB/PR/M5/assets/pr32/empty-dark.png?raw=true) | ![The error panel after a failed swap](https://github.com/Billykat7/clinicQ/blob/e32d0fd53f8f7fe215f40bc27f4323f09f09b622/docs/GITHUB/PR/M5/assets/pr32/error-light.png?raw=true) |

      The landing page's first button, 1280 px:

      ![The landing hero with Find a clinic as its first button](https://github.com/Billykat7/clinicQ/blob/e32d0fd53f8f7fe215f40bc27f4323f09f09b622/docs/GITHUB/PR/M5/assets/pr32/landing-cta.png?raw=true)

## Acceptance criteria

- [ ] **The list renders usable content in under 2 seconds on a throttled 3G profile.** *Partly
      (Issue 32).* On DevTools' **3G** preset it does: first paint in about 1.5 s cold and 0.6 s on a
      return visit, with the clinic list in that first paint. On the **Slow 3G** preset it does not:
      about 4.8–5.1 s cold and 2.1 s on a return visit. That preset adds 2,000 ms of latency to
      every request, so the HTML alone arrives after 2 s, and the render-blocking stylesheets need a
      second round trip. Getting under 2 s there would take the critical CSS inline under the CSP
      nonce in the kernel's `base.html`, which belongs with the performance work in Issue 105 rather
      than in this page. The compression fix above is what could be done here, and it took the cold
      first paint from 6.5 s to 5.0 s.
- [x] **Toggling sector re-renders the list without a full page reload.** The keyboard run shows the
      URL changing on each arrow key while a marker set on `window` survives. The test
      `test_the_toggle_swaps_a_fragment_and_keeps_the_address_bar_true` shows the fragment and
      `HX-Push-Url` for htmx and a redirect otherwise.
- [x] **Declining location shows the area-search fallback rather than a dead end.** The browser run
      denies the permission: the sentence appears and the focus is on the suburb input, where
      "Soweeto" suggests Soweto. The test
      `test_declining_location_leads_to_the_area_search_and_then_to_clinics` covers the same path as
      data, and `test_an_unusable_origin_says_why_and_still_offers_the_search` covers a bad
      coordinate or a stale area link.
- [x] **Empty and error states are designed, not default browser output.** The empty state names the
      sector and radius and links to the next radius (or, at 50 km, says what else to try). The
      error state keeps the last list, shows a panel with *Try again* and raises a toast. Both are
      in the screenshots and in the tests.
- [x] **Sector badges are distinguishable without relying on colour alone.** The badges differ by
      word, shape (`rect` and `path`) and border (`solid` and `dashed`). This is measured in the
      browser run, shown in greyscale, and pinned in
      `test_every_sector_has_a_badge_told_apart_without_colour`.
- [x] **The page is fully usable with a keyboard and passes an automated accessibility check.**
      The keyboard-only run above goes from the skip link to a sector, radius and sort change with
      no mouse, and every stop has a visible focus ring. axe-core reports 0 violations on four
      states in light and dark.

## Risk and rollback

No migration. Three new public pages that read only what verified clinics publish and the public
place dataset. The service gains a `sort` parameter defaulting to today's behaviour. The nginx change
only adds content types to compression. The landing page change is one link. Rollback is a revert;
#35, #36, #37, #33 and #38 are stacked on this.

**Follow-ups noticed:** Slow 3G cold loads need inline critical CSS in `base.html` (Issue 105);
`test_email_change_request_does_not_change_the_address` reads the SMTP host from a developer's `.env`
and fails when DNS does; the patient layout does not load the `.msg` styles `admin.css` defines, so
the consent page's messages are unstyled (this page styles its own).

Closes #32
