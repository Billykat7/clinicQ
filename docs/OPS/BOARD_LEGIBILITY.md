# Board legibility: how large a number must be, and how to check it in the room

The waiting-room board (Issue 56) is read from across a room by people who are worried about missing their
turn, and some of them do not see well. This page covers three things: the size a ticket number must be,
how the automated test checks it, and how a person checks it standing in front of a real screen. Issue 59
records the physical check for the accessibility audit (Issue 101).

## The numbers

A letter is legible when it subtends a large enough angle at the eye, so the size needed grows with the
distance. The board uses two published thresholds, at **5 metres**:

| What | Threshold | Cap height at 5 m | Source |
|------|-----------|-------------------|--------|
| A number being served | Signage read from a distance | **≥ 49 mm** | ADA 2010 Standards §703.5.5: 5/8 in at 6 ft, plus 1/8 in for every foot beyond (16.4 ft → 1.925 in) |
| A number up next | A 6/18 letter (15 minutes of arc), the WHO's line for moderate visual impairment | **≥ 21.8 mm** | 5000 mm × tan(15′) |

The board is sized for the target screen, a **32-inch 16:9** panel. That panel is 398.5 mm tall, whatever
its resolution. Every size in `src/static/css/board.css` is a fraction of the screen's height (`--u`, 1%),
so a size is a physical size on that panel: 1 `--u` = 3.98 mm. A 1080p and a 720p panel of the same size
show the same millimetres.

What the board draws in each layout, measured in Chromium:

| Layout | Served number cap height | Up-next cap height |
|--------|--------------------------|--------------------|
| 1 queue | 96.3 mm | 34.0 mm |
| 2 queues | 62.3 mm | 25.5 mm |
| 3 queues | 53.8 mm | 24.1 mm |
| 4 queues, and 5 or more (four per page) | 49.6 mm | 22.1 mm |

Numbers use the UI face (Roboto), whose capitals are 0.711 em tall and whose zero is narrower than the
letter O, so `A034` never reads as `AO34`.

**A smaller screen scales down.** On a 24-inch panel the densest layout's served numbers are 37 mm, which
meets the ADA size for 3.9 m, not 5. Mount a smaller screen closer, or use fewer queues on it.

## The automated check

`tests/e2e/display/test_board_page.py::test_the_board_lays_out_one_two_three_and_five_queues_legibly_at_1080p_and_720p`
opens the board in Chromium at 1920×1080 and at 1280×720 with 1, 2, 3 and 5 queues open. For every number
on the screen it measures the cap height with the browser's own font metrics (`measureText('H')`), converts
it to millimetres on a 32-inch panel and fails below the thresholds above. It also fails if anything scrolls
or spills out of a panel, or if the pointer is visible. It runs in CI's `browser` shard.

It measures what the browser draws, not what a person reads. A screen's brightness, glare from a window
and the room's light all change the answer, which is why the physical check below matters.

## The physical check (a person, a tape measure and a real screen)

Do this once per screen model, and again whenever the board's stylesheet changes the type scale.

1. **Set up.**
   - Mount or stand the screen as the clinic will: centre at about eye height for someone seated, and
     facing away from windows.
   - Open the board for a clinic with **four** queues (the densest layout), each with a number being
     served and at least four waiting.
   - Use the screen's normal brightness, and the room's normal lights.
2. **Measure the size.** Hold a ruler against the screen and measure the height of a capital letter in a
   served number, for example the `T` of `T004`: at least **49 mm**. Measure one up-next number: at least
   **22 mm**. Write both down.
3. **Mark 5 metres.** Measure 5 m from the screen along the floor, in line with where patients sit, and put
   a strip of tape there.
4. **Read it.**
   - Ask **three people who have not seen the board** to stand on the tape one at a time. At least one
     should wear glasses, and should read without them if they can do so safely.
   - Each reads aloud every number on the screen: served and up next, all four panels.
   - Change the numbers between people (call the next patient in each queue), so nobody reads from memory.
5. **Check the call.** From the tape, have someone call the next patient in one queue. Every reader should
   say *which* number was just called within a few seconds, without being told where to look. Repeat with
   the screen's operating system set to reduce motion.
6. **Record** in the pull request or the audit notes:
   - the screen's make, size and resolution;
   - the room (lights on or off, and whether daylight fell on the screen);
   - the two measurements from step 2;
   - for each reader: numbers read correctly out of numbers shown, whether they wore glasses, and whether
     they spotted the new call;
   - who ran the check, and the date.

A number misread by any reader is a finding: raise it on Issue 59 with the layout, the number and the
distance.
