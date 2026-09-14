# PR: A board everyone in the room can read: AA contrast in three themes, shapes for every status, and a still highlight (Issue 59 / M8-59)

**Milestone:** [Milestone 8: Waiting-room Display Monitor](https://github.com/Billykat7/clinicQ/milestone/8) ·
**Issue:** [#59](https://github.com/Billykat7/clinicQ/issues/59) · **Builds on:** #56 (the board page, PR
#179) and #57 (the live stream, PR #180), both merged · **Feeds:** #101 (the WCAG 2.2 AA audit), through
[`docs/COMPLIANCE/ACCESSIBILITY_BOARD_EVIDENCE.md`](../../COMPLIANCE/ACCESSIBILITY_BOARD_EVIDENCE.md)

A board a patient with low vision, colour blindness or motion sensitivity cannot use has failed at its
only job. This PR measures the board against WCAG 2.2 AA instead of eyeballing it, and fixes what the
measurement found:

- **Three themes, chosen per clinic**:
  - `dim`, the default, for a dim room;
  - `bright`, for a screen in daylight;
  - `high_contrast`, for low vision.

  Every text on every board state reaches **4.5:1** in all three, both as computed from the stylesheet
  and as Chromium draws it.
- **Status by shape and words as well as colour:** ● please come in, ▶ called now, ◆ called again,
  ■ being seen, ⟳ reconnecting. Greyscale screenshots are below.
- **Reduced motion** stops the pulse and the fade, and gives the new call a still inner ring instead.
- **Findings recorded for #101**, including what has not been checked. **The physical five-metre check has
  not been done** (see *The five-metre check*).

## Summary

- **Themes** (`src/commons/enums.py` `BoardTheme`; migration `0028` adds `site.board_theme`, default `dim`):
  - `board.css` defines each theme as the same `--kb-*` tokens on `.kiosk[data-board-theme=…]`.
  - The projection's payload carries `theme`, so `board.js` recolours an open board on
    `board.config_changed` with no reload.
  - Nothing about a patient changes with a theme, so it needs no confirmation. It is audited like every
    display setting.
- **The settings screen and API** (`src/modules/sites/{schemas,router,settings}.py`,
  `settings_display.html`, `site-display-settings.js`): a *Colours of the screen* select, labelled and
  described from `/settings/display-options` (`themes`), saved with the other display settings. A
  receptionist can read the setting and cannot change it.
- **Contrast fixes** (`board.css`): the "nobody being served" dash was drawn in the panel-border colour at
  1.41:1; it now uses the muted text colour, 9.98:1 in `dim`. The reconnecting line has its own `--kb-alert`
  token per theme, because amber on the bright theme's light ground would fail.
- **Shapes** (`board.css`): `::before` shapes per status, with CSS alternative text `""` so a screen reader
  reads only the words.
- **Reduced motion:** the existing rule already stopped animations and transitions; the highlight now gains
  `box-shadow: inset 0 0 0 0.6u`, the pulse's emphasis held still.
- **Tests:**
  - `tests/a11y/display/test_board_contrast.py` (new, CI's `flows` shard) reads the tokens from
    `board.css` and computes every pair in every theme;
  - `tests/e2e/display/test_board_accessibility.py` (new, `browser` shard) measures what is drawn,
    checks greyscale and reduced motion, and checks a live theme change;
  - the display-settings API test covers the theme.
- **Docs:**
  - `docs/COMPLIANCE/ACCESSIBILITY_BOARD_EVIDENCE.md` (new) holds the method, the ratio table, findings
    B-01 to B-05, and what is not checked;
  - `docs/OPS/BOARD_LEGIBILITY.md` records the physical check there and repeats it per theme;
  - `docs/PRODUCT/04-display-monitor.md` describes the themes.

## Design notes

**Two contrast checks, because each misses something.** The token test is exact and fast, and fails
the moment someone edits a colour. It cannot see a text drawn in a colour nobody listed. The browser test
walks every visible text node, takes its computed colour and the first opaque background behind it, and
fails on anything under 4.5:1, so a new element in the wrong colour is caught even if the token list is not
updated. Together they cover every board state: both pages of a five-queue board with a call, a new call, a
recall, a patient being seen and an empty queue, and a board whose server has gone.

**4.5:1 for everything.** Almost all of the board's text is WCAG "large text", which needs only 3:1. The
board is read from five metres by people with poor eyesight, so it holds the normal-text bar. The lowest
ratio anywhere is 6.00:1 (the bright theme's reconnecting line).

**What "readable in greyscale" means here.** Every status has different words and a different shape,
checked under Chromium's achromatopsia emulation. The highlight differs from its panel by luminance
(at least 11.4:1), which is what survives greyscale. Colour adds nothing a reader needs.

**Why a theme, not per-element settings.** A clinic knows its room (a window behind the screen, the lights
kept low), not contrast ratios. Three named themes that all pass the same checks let a manager choose for
the room without any choice being able to break the board.

## The five-metre check

**Not done.** It needs a physical 32-inch screen on a wall, a tape measure and readers who have not seen the
board, and none were available to this work. No result is invented here. What exists instead:

- every number's size measured in a browser against published 5-metre thresholds (#56): served ≥ 49.6 mm,
  up next ≥ 22.1 mm, in every layout at 1080p and 720p;
- the procedure a person follows, in `docs/OPS/BOARD_LEGIBILITY.md`:
  - measure a capital with a ruler;
  - tape a line at 5 m;
  - have three readers, one with glasses, read every number;
  - check that each reader spots a new call, also with reduced motion on;
  - repeat per theme in the room's own light;
- a place to record the result, `docs/COMPLIANCE/ACCESSIBILITY_BOARD_EVIDENCE.md`, which #101 reads.

The criterion stays unticked until someone does it and records it.

## Changes

- **New:**
  - `alembic/versions/0028_site_board_theme.py`;
  - `tests/a11y/__init__.py`, `tests/a11y/display/__init__.py`, `tests/a11y/display/test_board_contrast.py`;
  - `tests/e2e/display/test_board_accessibility.py`;
  - `docs/COMPLIANCE/ACCESSIBILITY_BOARD_EVIDENCE.md`;
  - `docs/GITHUB/PR/M8/assets/pr59/*.png`.
- **Models and enums:** `BoardTheme`, `SITE_DEFAULT_BOARD_THEME`, `Site.board_theme` and
  `board_theme_enum`.
- **Sites:**
  - `schemas.py`: `board_theme` in and out, and `BoardThemeOptionOut`;
  - `settings.py`: `BOARD_THEME_CHOICES`, and the theme in `DisplaySettingsChange` and its audit;
  - `router.py`.
- **Board:**
  - `projection.py`: `theme`;
  - `board.html`: `data-board-theme`;
  - `board.js`: applies a theme change;
  - `board.css`: the themes, the shapes, the dash colour, the alert token, the still ring.
- **Settings screen:** `settings_display.html`, `site-display-settings.js`.
- **Tests updated:**
  - `tests/integration/sites/test_display_settings_api.py`;
  - `tests/unit/display/test_board_privacy.py` (`theme`);
  - `tests/e2e/display/test_board_page.py`, which measures after the stream's first redraw.
- **Docs:**
  - `docs/OPS/BOARD_LEGIBILITY.md`, `docs/PRODUCT/04-display-monitor.md`;
  - M8: the status, and the partly-met legibility exit criterion;
  - the progress: `docs/GITHUB/README.md`, `README.md`, `docs/TEAM/WORKLOAD_SPLIT.md` (sprint 10 is now
    under way).

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` clean (269 files)
- [x] Whole suite as CI runs it, in UTC with PostgreSQL, Redis and Chromium, twice in a row:

  ```text
  $ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest tests/ -q --no-cov -n auto --dist loadscope
  2014 passed, 1 skipped, 9 xfailed in 218.28s
  2014 passed, 1 skipped, 9 xfailed in 217.65s
  ```

- [x] The new tests:

  ```text
  tests/a11y/display/test_board_contrast.py::test_the_contrast_arithmetic_matches_wcags_own_examples
  tests/a11y/display/test_board_contrast.py::test_every_pair_on_every_board_state_passes_wcag_aa[dim]
  tests/a11y/display/test_board_contrast.py::test_every_pair_on_every_board_state_passes_wcag_aa[bright]
  tests/a11y/display/test_board_contrast.py::test_every_pair_on_every_board_state_passes_wcag_aa[high_contrast]
  tests/a11y/display/test_board_contrast.py::test_the_check_fails_on_a_colour_that_does_not_pass
  tests/e2e/display/test_board_accessibility.py::test_every_text_on_every_board_state_passes_wcag_aa_as_drawn[dim]
  tests/e2e/display/test_board_accessibility.py::test_every_text_on_every_board_state_passes_wcag_aa_as_drawn[bright]
  tests/e2e/display/test_board_accessibility.py::test_every_text_on_every_board_state_passes_wcag_aa_as_drawn[high_contrast]
  tests/e2e/display/test_board_accessibility.py::test_each_status_has_its_own_words_and_shape_and_the_board_reads_in_greyscale
  tests/e2e/display/test_board_accessibility.py::test_under_reduced_motion_the_new_call_stops_pulsing_and_gains_a_still_ring
  tests/e2e/display/test_board_accessibility.py::test_a_clinic_changing_its_theme_recolours_open_boards_at_once_without_a_reload
  tests/integration/sites/test_display_settings_api.py::test_a_manager_chooses_the_board_theme_with_no_confirmation_and_it_is_audited
  12 passed in 12.94s
  ```

- [x] **The contrast table the token test prints** (ratio : 1, against a minimum of 4.5, or 3 for the
  highlight):

  ```text
                                                       dim   bright  high_contrast
  clinic name and clock                              18.61    16.43          21.00
  clinic initials                                    12.16     6.93          19.56
  queue name, numbers served and up next             15.75    18.61          21.00
  room                                               10.29     7.85          19.56
  captions, statuses, waiting, earlier calls, dash    9.98     9.44          21.00
  a new call's number, status and reason             13.46    18.61          19.56
  health notice, page count, empty board             11.80     8.33          21.00
  reconnecting line                                  13.46     6.00          19.56
  new-call highlight against its panel (non-text)    11.39    18.61          19.56
  ```

  Before the fix, the dash was `#2a3b57` on `#15233a`: 1.41:1. `test_the_check_fails_on_a_colour_that_does_not_pass`
  keeps that example.

- [x] **On the dev database, the manager's own screen.** Signed in as the seeded clinic manager, *Colours of
  the screen* was set to *High contrast* and saved. The board already open in another tab recoloured without
  reloading. The setting was then saved back to *Dim room*:

  ```text
  White and yellow on black with white borders, for patients with low vision.
  saved: Saved.
  board theme: high_contrast
  after saving dim, the open board is now: dim
  ```

- [ ] **Physical five-metre check:** not done (above).

### Screenshots

Each theme at 1920×1080, from the browser test's board: every status, one new call (General consultation):

![Dim](https://github.com/Billykat7/clinicQ/blob/d75b49b0712cbf04002307bc87178bd9cef25e52/docs/GITHUB/PR/M8/assets/pr59/board-dim.png?raw=true)

![Bright](https://github.com/Billykat7/clinicQ/blob/d75b49b0712cbf04002307bc87178bd9cef25e52/docs/GITHUB/PR/M8/assets/pr59/board-bright.png?raw=true)

![High contrast](https://github.com/Billykat7/clinicQ/blob/d75b49b0712cbf04002307bc87178bd9cef25e52/docs/GITHUB/PR/M8/assets/pr59/board-high_contrast.png?raw=true)

**Greyscale** (Chromium's achromatopsia emulation). Each status is still told apart by its shape and words,
and the new call by its highlight:

![Dim, greyscale](https://github.com/Billykat7/clinicQ/blob/d75b49b0712cbf04002307bc87178bd9cef25e52/docs/GITHUB/PR/M8/assets/pr59/board-dim-greyscale.png?raw=true)

![Bright, greyscale](https://github.com/Billykat7/clinicQ/blob/d75b49b0712cbf04002307bc87178bd9cef25e52/docs/GITHUB/PR/M8/assets/pr59/board-bright-greyscale.png?raw=true)

![High contrast, greyscale](https://github.com/Billykat7/clinicQ/blob/d75b49b0712cbf04002307bc87178bd9cef25e52/docs/GITHUB/PR/M8/assets/pr59/board-high_contrast-greyscale.png?raw=true)

Deuteranopia, the most common colour-vision deficiency (dim theme):

![Dim, deuteranopia](https://github.com/Billykat7/clinicQ/blob/d75b49b0712cbf04002307bc87178bd9cef25e52/docs/GITHUB/PR/M8/assets/pr59/board-dim-deuteranopia.png?raw=true)

The theme choice in the clinic manager's display settings, and the dev clinic's board in high contrast:

![Settings](https://github.com/Billykat7/clinicQ/blob/d75b49b0712cbf04002307bc87178bd9cef25e52/docs/GITHUB/PR/M8/assets/pr59/settings-theme-1366.png?raw=true)

![Dev board in high contrast](https://github.com/Billykat7/clinicQ/blob/d75b49b0712cbf04002307bc87178bd9cef25e52/docs/GITHUB/PR/M8/assets/pr59/board-dev-high_contrast-1280x720.png?raw=true)

## Acceptance criteria

- [x] Automated contrast checks pass on every board state: every pair in every theme from the stylesheet,
      and every visible text as drawn, across all states including a lost connection (tests)
- [ ] Ticket numbers are readable at 5 metres, verified by a physical test documented in the PR. **Not done:**
      no physical screen or readers were available, and no result is claimed. The sizes are measured
      against 5-metre thresholds in a browser (#56). The physical procedure and the place to record it are
      ready (above).
- [x] The board is fully interpretable in greyscale: four statuses with four different words and four
      different shapes under achromatopsia emulation, in every theme (test, screenshots)
- [x] Reduced-motion users get a static highlight that is equally noticeable: the same inverted colours,
      border and words, plus a still inner ring in place of the pulse (test, screenshot in #56)
- [x] The high-contrast theme is selectable from clinic settings (API test; the manager's screen on the
      dev database)
- [x] Findings and evidence are recorded for the M13 accessibility audit:
      `docs/COMPLIANCE/ACCESSIBILITY_BOARD_EVIDENCE.md`

## Risk and rollback

**Migration `0028`** adds one non-null column with a server default. Existing clinics get `dim`, the
board's only look until now. It is reversible: the downgrade drops the column and loses only each clinic's
choice.

The API gains an optional input field and an output field. A client that does not send `board_theme` keeps
the current theme.

Rollback is a revert of this PR and `alembic downgrade 0027`.

**Known limits:**

- Contrast is what the page asks the screen for. A TV's brightness, gamma and viewing angle change what
  readers see, which is one more reason for the physical check.
- Finding B-05 is open: the pulse ring can briefly touch the up-next column in the four-panel layout, never
  under reduced motion.
- No screen reader was run. A kiosk is not operated with one, but the board announces calls in a live
  region.

Closes #59
