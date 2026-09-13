# PR: A map of exactly the clinics in the list, that falls back to the list (Issue 33 / M5-33)

**Milestone:** [Milestone 5: Discovery & Geolocation](https://github.com/Billykat7/clinicQ/milestone/5) ·
**Issue:** [#33](https://github.com/Billykat7/clinicQ/issues/33) · **Builds on:** #32 (PR #149), and #31,
#34, #35, #36, #37 in the stack (PRs #147, #148, #150–#152)

> **Merge order:** after PRs #147–#152. This branch is stacked on them, so the Conventions check fails
> on their commits until they merge. Every test job passes.

A map answers "which of these is on my taxi route?" in a way a list cannot, so patients who think in
maps get one. It is built as an enhancement on a list that already works. The map **draws the list's
own clinic cards**: it never makes a second query that could disagree with the list. It loads only
when asked for. If the map tiles fail or are too slow, it gives way to the list with a sentence,
never a blank grey box.

## Summary

- **List / Map** joins the filter form as a fourth radio group. Switching keeps every filter (it is
  the same form), puts `view=map` in the address, and brings the list back at the scroll position it
  was left at.
- **Pins come from the cards.** Each card in `#results` now carries its clinic's coordinates, sector
  shape and directions link as `data-*` attributes. `src/static/js/discover-map.js` pins exactly those
  and redraws after every htmx swap, so Private shows only the private clinics in the list.
- **Shape as well as colour:** a square for public and a diamond for private (the badges' shapes), a
  ring for where the search started, and a legend. Pins that would overlap cluster into a count, and
  choosing the count zooms in.
- **Tap-to-preview:** a pin opens a copy of that clinic's own card (the same data, word for word)
  with **Directions**, which hands off to the phone's maps app. Every list card gains the same
  Directions button.
- **Tile failure falls back to the list:** three tile errors before any tile arrives, or no tile
  within 8 seconds, hides the map and shows *The map tiles could not be loaded. The list below shows
  the same clinics, and Directions on each one still works.*, with a toast.
- **OpenStreetMap attribution** (© OpenStreetMap contributors, linked to the copyright page) is shown
  on the map, as the tile licence requires.
- **The vendored Leaflet was corrupted, and is restored.** See *Design notes*.

## Design notes

**No second query, by construction.** The map has no data source of its own: `cards()` reads
`#results .clinic-card[data-lat]`. The browser run records the requests made while switching to the
map: the same `/discover/results` swap the list makes for any filter change (it updates the address),
Leaflet's two static files from this origin, and tiles from `tile.openstreetmap.org`. Nothing else.
When the list has loaded fewer clinics than matched, the map says *The map shows the 20 nearest of
48 clinics, the same ones as the list. Switch to the list to load more.* rather than implying it
shows every clinic.

**An enhancement, not a dependency.** `leaflet.js` (148 KB) and `leaflet.css` are injected only the
first time the map is shown, so the list view's Slow 3G numbers from #32 are unchanged. In map view the
cards stay in the page (the map is drawn from them) and are hidden by `.is-map`; removing that class
is the whole fallback. Without JavaScript the map section says the list below shows the same clinics.

**The fallback covers both ways a map fails on a weak connection.** The browser run blocks the tile
host outright (the errors path) and separately holds every tile back for 30 seconds (the 8-second
timeout path). Both land on the list with a sentence. The milestone's exit criterion "the map
degrades to the list view on a slow connection rather than blocking the page" is the second one.

**Clustering without a plugin.** `Leaflet.markercluster` is not vendored, and a directory page of up
to 50 pins does not need it. `drawPins()` buckets pins by a 44 px grid in projected coordinates at the
current zoom and redraws on `zoomend`. Clustered and single pins are Leaflet markers with
`keyboard: true`, so each can be focused and chosen with Enter; the title names the clinic and its
sector.

**Scroll position, stated precisely.** The list's position is saved when the map opens and restored
when the list returns, again after the htmx swap settles, because the swap can move it. Tapping the
toggle scrolls the page to the toggle first, so in practice the list comes back where the patient was
when they tapped. The run shows both cases.

**The vendored Leaflet did not match its own README.** `src/static/vendor/leaflet/README.md` records
the upstream SHA-256 `db49d009…` and says the files are stored byte for byte, but `leaflet.js` hashed
`a2ca487b…`. Comparing it with `unpkg.com/leaflet@1.9.4/dist/leaflet.js` showed the kernel rebrand's
search and replace had renamed GeoJSON's `properties` to `clinicq` inside the library, in three places.
That would break any GeoJSON layer, and it was the kind of damage nobody would look for in minified
third-party code. `leaflet.css` had also lost its upstream CRLF line endings. Both files are replaced
with the upstream bytes, `leaflet.js` matches the README's checksum again, and the images were already
identical. `htmx-2.0.0.min.js` differs from upstream only by a trailing newline and is left alone.

**CSP:** no directive changes. Leaflet loads from `'self'`, tiles are images under `img-src https:`,
the pin markup is static SVG with no style attributes, and Leaflet's positioning goes through the
CSSOM, which `style-src` does not govern.

**Out of scope:** turn-by-turn routing (the button hands off to the device) and the list and its
filters (Issue 32).

## Changes

- **`src/static/js/discover-map.js`** (new): lazy Leaflet, pins from cards, grid clustering, preview,
  tile-failure and timeout fallback, scroll restore.
- **`src/templates/discover/map.html`** (new): the map section, legend, status and preview.
  **`list.html`:** the section, the fallback line and the script. **`_clinic_card.html`:** `data-*`
  for the map and a Directions button. **`src/static/css/discover.css`:** map, pins, clusters, legend,
  map-mode list hiding.
- **`src/web/discover.py`:** `DiscoverView`, `view` through the page, the address and the swap;
  `ClinicCard.latitude`/`longitude`/`directions_href`; `ResultsView.map_note`;
  `DiscoverPage.origin_point`/`origin_label`.
- **`src/static/vendor/leaflet/leaflet.js`**, **`leaflet.css`:** restored to Leaflet 1.9.4's upstream
  files. **`README.md`:** what Leaflet is vendored for.
- **`tests/integration/discovery/test_discover_map.py`** (new, 4 cases);
  **`test_discover_pages.py`:** the filter bar now has the view group.
- **`docs/GITHUB/PR/M5/assets/pr33/`:** the screenshots below.

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (225 files); the template
      punctuation check clean; the no-inline-styles guard passes over the new script.
- [x] Full suite with PostgreSQL and Redis required, in UTC: **1624 passed, 9 xfailed**. One earlier
      run failed `test_offload_is_dramatically_faster_than_blocking[0]`, a kernel timing benchmark;
      it passed when run alone and in the next full run.
- [x] **Browser run** on the seeded `clinicq_m5_verify` database, driven by Playwright on the system
      Chrome with real OpenStreetMap tiles. Transcript:

```text
== Filter to Private, then switch to the map
  URL: /discover?lat=-26.2&lon=28.02&sector=private&radius_m=50000&view=map | no reload: True
  cards in the list: 2 ['Private'] | pins: 2 | in clusters: 0 | tiles loaded: 9
  pins: ['Medicross Meldene Medical and Dental Centre (Private)', 'Randburg Medicross (Private)']
  status: 2 clinics on the map, the same ones as in the list. | attribution: © OpenStreetMap contributors
  requests while switching to the map: app ['/discover/results', '/static/favicon.svg', '/static/vendor/leaflet/leaflet.css', '/static/vendor/leaflet/leaflet.js'] | other hosts ['tile.openstreetmap.org']

== Tap a pin: Medicross Meldene Medical and Dental Centre (Private)
  preview is the list card, word for word: True
  preview Directions: https://www.google.com/maps/dir/?api=1&destination=-26.17691%2C28.00069
axe map view with a preview open: 0 violations, 52 rules passed, incomplete: ['color-contrast']

== Back to the list
  after tapping List at the toggle: scroll 131 (where the toggle was tapped, which is where the list was left)
  URL: /discover?lat=-26.2&lon=28.02&sector=private&radius_m=50000 | sector still: private | scroll before 640 after 640 | cards visible: True

== The tile host blocked
  fallback: The map tiles could not be loaded. The list below shows the same clinics, and Directions on each one still works.
  map hidden: True | list shown: grid | cards: 6

== A slow connection: tiles held back past the 8-second limit
  fallback: The map is taking too long to load on this connection. The list below shows the same clinics, and Directions on each one still works.

== Markers in greyscale, zoomed in
  shapes: ['map-pin-origin:circle', 'map-pin-diamond:path', 'map-pin-diamond:path', 'map-pin-square:rect', 'map-pin-square:rect', 'map-pin-square:rect']
```

      axe marks `color-contrast` as *incomplete* (not failed) because text sits over map tile
      imagery it cannot measure. In the in-app browser at 390 px, *All* within 50 km (6 cards) drew 2
      single pins and 2 clusters of 2, a total of 6.

- [x] **Screenshots** (390 px at 2x unless noted).

      Private, on the map, and a pin's preview (the list card, with Directions):

      | Map, Private | Preview |
      |---|---|
      | ![The map showing the two private clinics from the list](https://github.com/Billykat7/clinicQ/blob/6a18410c374db84ed04912f600102ebd75bf647e/docs/GITHUB/PR/M5/assets/pr33/map-private-light.png?raw=true) | ![A pin's preview card with Directions](https://github.com/Billykat7/clinicQ/blob/6a18410c374db84ed04912f600102ebd75bf647e/docs/GITHUB/PR/M5/assets/pr33/map-preview-light.png?raw=true) |

      Markers in greyscale (600 px): squares, diamonds and the origin ring stay distinguishable:

      ![Map markers in greyscale](https://github.com/Billykat7/clinicQ/blob/6a18410c374db84ed04912f600102ebd75bf647e/docs/GITHUB/PR/M5/assets/pr33/markers-greyscale.png?raw=true)

      The tile host blocked: the list, the sentence and the toast, never a grey box:

      | Light | Dark |
      |---|---|
      | ![Tile failure fallback, light](https://github.com/Billykat7/clinicQ/blob/6a18410c374db84ed04912f600102ebd75bf647e/docs/GITHUB/PR/M5/assets/pr33/tiles-blocked-light.png?raw=true) | ![Tile failure fallback, dark](https://github.com/Billykat7/clinicQ/blob/6a18410c374db84ed04912f600102ebd75bf647e/docs/GITHUB/PR/M5/assets/pr33/tiles-blocked-dark.png?raw=true) |

## Acceptance criteria

- [x] **The map shows all clinics currently in the filtered list, and no others.** Pins are read
      from the list's cards and nothing else: under Private, 2 cards gave 2 pins with the same names,
      and the only data request was the list's own swap. When the list has loaded fewer clinics than
      matched, the map says so (`test_the_map_says_when_the_list_has_more_than_it_has_loaded`).
- [x] **Tapping a pin opens a preview card with the same data as the list card.** The preview is a
      copy of the card: *word for word: True* in the run, plus Directions.
- [x] **Switching between list and map preserves filters.** The view is part of the filter form:
      `sector=private` survives both ways in the run, and
      `test_switching_view_keeps_the_filters_in_the_address` checks the pushed address. The list's
      scroll position comes back as well (640 → 640).
- [x] **Markers are distinguishable by shape as well as colour.** `rect` for public, `path` diamond
      for private and `circle` for the origin, measured in the run and shown in greyscale; the legend
      names each.
- [x] **Tile loading failure falls back to the list rather than showing a blank grey area.** Shown
      with the tile host blocked and with tiles stalled past 8 seconds; the map section is hidden, the
      list (6 cards) is shown, and the sentence explains.
- [x] **OpenStreetMap attribution is displayed as the licence requires.** *© OpenStreetMap
      contributors*, linked to `openstreetmap.org/copyright`, on the map in every screenshot. The
      page footer's place-name credit from #32 stays.

## Risk and rollback

No migration and no server behaviour change beyond a `view` query parameter. The map code runs only
in map view, and any failure hides it. Restoring Leaflet to its upstream bytes changes a library
nothing else in the app calls. Rollback is a revert; #38 is stacked on this.

**Follow-ups noticed:** the rebrand that damaged Leaflet ran over other text too, so a search of
`src/static/` and `src/templates/` for `clinicq` where a generic word belongs is worth an hour; the
OpenStreetMap tile servers' usage policy suits a pilot but not heavy production traffic, which needs
a tile provider (or self-hosted tiles) before launch, and any tile host change needs an `img-src`
review.

Closes #33
