# Waiting-room board announcements: voices, recordings and language checks

When a ticket is called, the board plays a chime and then says the number and the room in the clinic's
language (Issue 60). This page is for whoever sets up a box, records a language, or checks that an
announcement is understandable. How the board decides what to say is in
[04-display-monitor.md](../PRODUCT/04-display-monitor.md#announcements-issue-60). The code is
`src/modules/display/announcements.py` and `src/static/js/board-announce.js`.

**What is not done yet, plainly:**

- **No sentence has been checked by a fluent speaker**, English included. The table in
  [Checks by fluent speakers](#checks-by-fluent-speakers) is empty until someone does it.
- **No number recordings exist.** A box with no voice for its clinic's language says English or plays
  only the chime, as described in [How a call is said](#how-a-call-is-said).
- **No real kiosk box has been heard.** The browser tests replace the speech engine with a stand-in that
  records what it was asked to say. A Raspberry Pi's Chromium voices have not been listed or listened to.

---

## What the board says

A call is one sentence with two blanks: the ticket number, spelled out character by character, and the
queue's room as the clinic typed it (for example *Room 4* or *Window 3*). There is no blank for anything
else. `placeholders()` refuses a sentence with any other blank, so no future edit can add a patient's name
or reason, and `tests/unit/display/test_announcements.py` fails if one tries.

| Language | Board setting | Sentence | Voice asked for | Checked by a fluent speaker |
|----------|---------------|----------|-----------------|-----------------------------|
| English | `en` | Number {number}, please go to {room}. | `en-ZA` | Not yet |
| Afrikaans | `af` | Nommer {number}, gaan asseblief na {room}. | `af-ZA` | Not yet |
| isiZulu | `zu` | Inombolo {number}, sicela uye ku-{room}. | `zu-ZA` | Not yet |
| isiXhosa | `xh` | Inombolo {number}, nceda uye ku-{room}. | `xh-ZA` | Not yet |
| Sesotho | `st` | Nomoro {number}, ka kopo eya ho {room}. | `st-ZA` | Not yet |

These are **drafts written for Issue 60 by an AI assistant (Claude)**, not by a fluent speaker of any of
these languages. The six other board languages (isiNdebele, Sepedi, Setswana, siSwati, Tshivenda, Xitsonga) have
no sentence yet and speak English. The translation work (Issue 77) supplies the rest.

The room is read as typed. A room called *Room 4* is read as English words inside an isiZulu sentence,
which is how clinics already say it at the desk. A clinic that wants the room in its own language names
the room that way in **Clinic settings → Queues**.

## How a call is said

For each call, in this order:

1. **The browser's speech**, with a voice for the clinic's language (the voice's language matches the
   first part of the tag: any `zu-*` voice for `zu-ZA`).
2. **Recordings** of each letter and digit of the number, in the clinic's language, when the browser has
   no speech or no voice for the language, **and** every character of this number has a recording.
3. **English speech**, when the browser has an English voice but neither of the above.
4. **The chime alone.** The screen shows and highlights the call as always.

If a browser lists no voices at all, it may still speak with a built-in default, so step 1 is tried with
the clinic's language when step 2 has nothing. A sentence the speech engine has not started within two
seconds is cancelled, so a box without a working engine does not fall behind or hold on to sentences
all day.

Calls are said one at a time with a short pause between them. At most eight calls wait; past that, the
oldest is dropped (the screen still shows it).

## Settings

In **Clinic settings → Waiting-room screen**, a clinic manager sets:

- *Announce each call aloud* (`announce_audio`). Off, the board plays nothing and still highlights calls.
- *Loudness of announcements* (`announce_volume`, 0–100 %), the chime's and the voice's volume as a share
  of the TV's. Set the TV's own volume first.
- *Language of the screen* (`board_language`), which also chooses the sentence and the voice.

A change to the first two applies to open boards from the next call, with no reload. A change of language
reloads open boards, so the page's own words follow it.

The box's browser must be allowed to play sound nobody clicked for. The board's pages are served with
`Permissions-Policy: autoplay=(self)`; every other page keeps `autoplay=()`. Chromium must be started with
`--autoplay-policy=no-user-gesture-required` ([KIOSK_SETUP.md](KIOSK_SETUP.md), A5).

## Checking the voices on a box

**Not yet done on a real box.** To see which voices a box's Chromium offers, before it is locked into kiosk
mode (or with a keyboard and `Ctrl+Shift+J` on a test run without `--kiosk`), open the board and type in
the console:

```js
speechSynthesis.getVoices().map((v) => v.lang + ' ' + v.name)
```

A board for a clinic whose language is not in the list will use recordings, English or the chime, as
described above. The one machine listed so far has English voices only, so without recordings an isiZulu,
isiXhosa, Sesotho or Afrikaans clinic's board on it would speak English. Record what you find in the table below, so the next person knows what a Pi offers.

| Box and browser | Date | Voices listed for en / af / zu / xh / st | Who |
|-----------------|------|-------------------------------------------|-----|
| *Not a kiosk:* a developer's Mac (macOS 26.5), Playwright's Chromium 151, headless | 2026-09-14 | en: `en-ZA` and five other English voices. af, zu, xh, st: **none** | Listed while building Issue 60 |

## Recording a language

Recordings are for languages the boxes have no voice for. Each is one short clip of one character of a
ticket number: the digits `0`–`9` and the letters queues use as prefixes (`A`–`Z`).

- **Who:** a fluent speaker of the language, in a quiet room.
- **Format:** WAV, mono, 16-bit, 22 050 or 44 100 Hz, with no silence before or after the sound, each under
  one second, all at the same loudness.
- **Names:** the character itself, upper case: `A.wav`, `7.wav`.
- **Where:** `src/static/audio/numbers/<board setting>/`, for example `src/static/audio/numbers/zu/A.wav`.
- **Licence:** recordings made by the team for ClinicQ. Never use clips from a text-to-speech service or a
  sound library whose terms do not allow shipping them in a product.

The board finds the files by name (`number_clips()`). A number is spoken from recordings only when every
one of its characters has a clip, so a partly recorded language is safe.

## Checks by fluent speakers

The acceptance criterion asks that each supported language's announcement is understandable to a native
or fluent speaker on the team. To check one:

1. Set a test clinic's *Language of the screen* to the language and open its board on a box or a laptop
   with a voice for it (or its recordings).
2. Call three tickets from different queues, including one with a two-letter prefix if the clinic has one.
3. Without looking at the screen, write down the number and the room you heard for each.
4. Record the result below: the sentence as it is now, whether each call was understood, and any better
   wording. A changed sentence goes into `PHRASES` in `src/modules/display/announcements.py`, with
   `checked_by` set to the checker's name.

| Language | Checked by | Date | Voice or recordings used | Understood? | Suggested wording |
|----------|------------|------|--------------------------|-------------|-------------------|
| English | | | | | |
| Afrikaans | | | | | |
| isiZulu | | | | | |
| isiXhosa | | | | | |
| Sesotho | | | | | |

## The chime

`src/static/audio/chime.wav` is generated by `python -m scripts.audio.make_chime`: two bell-like notes
falling a major third (E5 then C5), 1.25 seconds, made from sine waves. No sound library or licence is
involved. `tests/unit/display/test_announcements.py` checks that the committed file is the one the script
makes.
