# Accessibility evidence: the waiting-room board

**For:** the WCAG 2.2 AA audit of the whole product (Issue 101) · **Recorded by:** the pull request for
Issue 59 · **Date:** 2026-09-14 · **Scope:** `GET /display/{site_id}`, the kiosk board (Issues 56, 57, 59)

This page records what was checked on the board, how, what was found, what was fixed, and what has
**not** been checked. The audit should start from the last section.

## How the board was checked

| Check | How | Where it lives | Runs in CI |
|-------|-----|----------------|------------|
| Contrast of every colour pair, every theme | WCAG 2.2 contrast ratios computed from the tokens in `board.css` | `tests/a11y/display/test_board_contrast.py` | yes (`flows` shard) |
| Contrast of every visible text as drawn, every theme and state | Chromium: each element's computed colour against the first opaque background behind it | `tests/e2e/display/test_board_accessibility.py` | yes (`browser` shard) |
| Readable without colour | Chromium's achromatopsia emulation (`Emulation.setEmulatedVisionDeficiency`); every status must differ by words and by shape; greyscale and deuteranopia screenshots | same | yes (screenshots when `BOARD_A11Y_SHOTS` is set) |
| Reduced motion | `prefers-reduced-motion: reduce` emulated; no animation, and an inner ring instead | same, and `test_board_page.py` | yes |
| Type size for 5 m | Cap height of every number measured in Chromium, converted to millimetres on a 32-inch panel | `tests/e2e/display/test_board_page.py`; method in [`docs/OPS/BOARD_LEGIBILITY.md`](../OPS/BOARD_LEGIBILITY.md) | yes |
| Five-metre legibility with people | Tape measure, a real screen, three readers | [`docs/OPS/BOARD_LEGIBILITY.md`](../OPS/BOARD_LEGIBILITY.md), *The physical check* | **not done yet** (see below) |

"Every board state" means: a number called a while ago ("● Please come in"), a new call ("▶ Called now"),
a number called again ("◆ Called again"), a patient being seen ("■ Being seen"), a queue with nobody served
or waiting ("—", "Nobody waiting"), several pages ("Page 1 of 2"), the health notice, the lost
connection ("⟳ Reconnecting to the clinic…"), and, since Issue 62, the board that is not up to date
("⚠ Not up to date. Last updated at 14:32.").

## Results

### Contrast (WCAG 2.2 SC 1.4.3 and 1.4.11)

All text is held to **4.5:1**, although almost all of the board's text is large and would need only 3:1.
The new-call highlight, a state shown by a filled area, is held to 3:1 against its panel.

| Pair | Dim | Bright | High contrast |
|------|-----|--------|---------------|
| Clinic name and clock on the page | 18.61 | 16.43 | 21.00 |
| Clinic initials on their tile | 12.16 | 6.93 | 19.56 |
| Queue name and ticket numbers on a panel | 15.75 | 18.61 | 21.00 |
| Room on a panel | 10.29 | 7.85 | 19.56 |
| Captions, statuses, waiting count, earlier calls, the dash | 9.98 | 9.44 | 21.00 |
| A new call's number and words on the highlight | 13.46 | 18.61 | 19.56 |
| Health notice, page count, empty board on the page | 11.80 | 8.33 | 21.00 |
| "Reconnecting" line and "Not up to date" banner on the page | 13.46 | 6.00 | 19.56 |
| New-call highlight against its panel (non-text) | 11.39 | 18.61 | 19.56 |

The rendered check found every visible text in all three themes at 4.5:1 or above, in both page states.

### Colour independence (SC 1.4.1)

Every status carries words and a shape, not only a colour: ● called, ▶ called now, ◆ called again,
■ being seen, ⟳ reconnecting, ⚠ not up to date. Under achromatopsia emulation the four ticket statuses still have four
different words and four different shapes in every theme. The new-call highlight still stands apart from
its panel, because its contrast with the panel is a ratio of luminance (11.4:1 or more).

The screenshots are attached to the Issue 59 pull request: each theme in colour, in greyscale, and with
deuteranopia.

### Motion (SC 2.3.3, and 2.2.2 for the pulse)

The only motion is the new-call pulse: five 1.2-second pulses, then still. The health notices fade for
0.6 s when they change, and never scroll. Under `prefers-reduced-motion: reduce` there is no pulse and no
fade. The highlight keeps its inverted colours, heavy border and words, and gains an inner ring, which holds
the pulse's emphasis still.

### Type size

In every layout, measured at 1080p and 720p: a served number is at least **49.6 mm** tall and an up-next
number at least **22.1 mm** on a 32-inch panel. The ADA §703.5.5 threshold for 5 m is 49 mm, and a 6/18
letter at 5 m is 21.8 mm.

## Findings

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| B-01 | The "nobody being served" dash was drawn in the panel border colour: 1.41:1 on its panel | High (1.4.3) | **Fixed** in Issue 59: the muted text colour, 9.98:1 |
| B-02 | Statuses were words in one colour, with no shape; only "Called now" had a marker | Low (1.4.1: words already carried the meaning) | **Fixed** in Issue 59: a shape per status |
| B-03 | Under reduced motion the highlight lost its pulse with nothing in its place | Medium (a new call less noticeable for motion-sensitive viewers) | **Fixed** in Issue 59: a still inner ring |
| B-04 | One dark palette only: a screen in daylight mirrors the windows, and there was no high-contrast option | Medium | **Fixed** in Issue 59: `bright` and `high_contrast` themes, chosen per clinic in the display settings |
| B-05 | The pulse ring can briefly touch the up-next column in the four-panel layout | Cosmetic | Open: nothing is covered for more than 0.6 s, and never under reduced motion |

## Not yet checked (for Issue 101)

- **The physical five-metre check has not been done.** No physical 32-inch screen, room or readers were
  available when this was recorded, and no result is claimed. The sizes above are measured in a browser
  against published thresholds; a person reading from 5 m is the check that matters.
  [`BOARD_LEGIBILITY.md`](../OPS/BOARD_LEGIBILITY.md) gives the procedure, and its record belongs here.
- **Real screens.** Contrast is computed from the colours the page asks for. A cheap TV's gamma, brightness
  and viewing angle change what is seen, so the physical check should include the bright theme in daylight
  and the dim theme with the lights off.
- **Screen readers.** A kiosk board is not operated with a screen reader. The board still announces each
  call once through an assertive live region, and the status shapes are marked as decoration so they are
  not read out. No screen reader was run against it.
- **Audio** (Issue 60) is recorded with that issue.
