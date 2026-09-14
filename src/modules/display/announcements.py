"""What a waiting-room board says aloud when it calls a ticket (Issue 60): the number and the room, nothing else.

Patients look at their phones, and a number that only appears silently on a screen gets missed. So a
board plays a chime and then speaks the call in the clinic's language (``board-announce.js``). This
module holds everything the page needs to do that, decided on the server:

* **The words.** One sentence per language, with exactly two blanks, ``{number}`` and ``{room}``. There
  is no blank for a name or a reason, and :func:`placeholders` refuses a sentence that has one, so an
  announcement cannot speak a patient's name whatever the clinic's display mode allows on the screen.
  The page fills the blanks from the ticket's number and its queue's room, which is all it is given.
* **The voice.** A BCP 47 tag (``zu-ZA``) the browser's speech synthesis picks a voice with.
* **The recordings.** A browser with no speech synthesis, or none for the language, spells the number
  from pre-recorded clips of each letter and digit under ``src/static/audio/numbers/<language>/``
  (:func:`number_clips`). Clips are recorded by a person who speaks the language
  (``docs/OPS/BOARD_AUDIO.md``); a language with none recorded plays the chime alone, and the screen
  still shows the call.

**Checked by a fluent speaker.** Every sentence records who confirmed it is understandable
(:attr:`AnnouncementPhrase.checked_by`). None has been checked yet: the sentences below are drafts
written for this issue, to be confirmed by a speaker on the team (and, for the wider set of languages, by
the translation work of Issue 77). ``docs/OPS/BOARD_AUDIO.md`` is where a check is recorded.

A board set to a language with no sentence here speaks English, the language every pilot clinic's
signage uses, and :func:`announcement_for` says so, so the page marks the speech with the right language.
"""

from __future__ import annotations

import string
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from src.commons.enums import BoardLanguage

#: The only blanks a spoken call may have.
NUMBER: Final = "number"
ROOM: Final = "room"
ALLOWED_PLACEHOLDERS: Final = frozenset({NUMBER, ROOM})

#: The chime played before every call (``scripts/audio/make_chime.py`` makes it).
CHIME_PATH: Final = "/static/audio/chime.wav"
#: Where each language's recordings of letters and digits live, one file per character: ``A.wav``, ``7.wav``.
CLIPS_DIR: Final = Path(__file__).resolve().parents[2] / "static" / "audio" / "numbers"
CLIPS_URL: Final = "/static/audio/numbers"
#: What a recording is: short, mono WAV, named after the one character it says.
CLIP_SUFFIX: Final = ".wav"
#: The characters a ticket number is made of: a queue's prefix (A to Z) and its digits.
NUMBER_CHARACTERS: Final = frozenset(string.ascii_uppercase + string.digits)


class AnnouncementPhraseError(ValueError):
    """A spoken sentence has a blank other than the number and the room, or is missing one."""


@dataclass(frozen=True, slots=True)
class AnnouncementPhrase:
    """How a board in one language calls a ticket."""

    language: BoardLanguage
    #: The sentence, with ``{number}`` and ``{room}`` each exactly once.
    call: str
    #: The BCP 47 tag a speech voice is chosen by.
    voice_tag: str
    #: Who confirmed the sentence is understandable, as a fluent speaker; ``None`` until someone has.
    checked_by: str | None = None


def placeholders(template: str) -> tuple[str, ...]:
    """The blanks in ``template``, in order, refusing any that is not the number or the room.

    Raises:
        AnnouncementPhraseError: A blank is neither ``{number}`` nor ``{room}``, has a format spec or a
            conversion, or the number or the room is missing or repeated.
    """
    found: list[str] = []
    for _literal, field, spec, conversion in string.Formatter().parse(template):
        if field is None:
            continue
        if field not in ALLOWED_PLACEHOLDERS or spec or conversion:
            raise AnnouncementPhraseError(
                f"an announcement may only say {{{NUMBER}}} and {{{ROOM}}}, not {{{field}}}: "
                "a spoken call never includes anything about the patient"
            )
        found.append(field)
    if sorted(found) != sorted(ALLOWED_PLACEHOLDERS):
        raise AnnouncementPhraseError(
            f"an announcement says the number and the room once each: {template!r}"
        )
    return tuple(found)


#: The sentences, by language: the five the issue names. Drafts until ``checked_by`` is filled in.
PHRASES: Final[Mapping[BoardLanguage, AnnouncementPhrase]] = {
    phrase.language: phrase
    for phrase in (
        AnnouncementPhrase(
            BoardLanguage.ENGLISH, "Number {number}, please go to {room}.", "en-ZA"
        ),
        AnnouncementPhrase(
            BoardLanguage.AFRIKAANS,
            "Nommer {number}, gaan asseblief na {room}.",
            "af-ZA",
        ),
        AnnouncementPhrase(
            BoardLanguage.ISIZULU, "Inombolo {number}, sicela uye ku-{room}.", "zu-ZA"
        ),
        AnnouncementPhrase(
            BoardLanguage.ISIXHOSA, "Inombolo {number}, nceda uye ku-{room}.", "xh-ZA"
        ),
        AnnouncementPhrase(
            BoardLanguage.SESOTHO, "Nomoro {number}, ka kopo eya ho {room}.", "st-ZA"
        ),
    )
}

#: What a board speaks when its language has no sentence yet.
FALLBACK_LANGUAGE: Final = BoardLanguage.ENGLISH


def announcement_for(language: BoardLanguage) -> AnnouncementPhrase:
    """The sentence a board set to ``language`` speaks: its own, or English when it has none yet."""
    return PHRASES.get(language, PHRASES[FALLBACK_LANGUAGE])


def number_clips(
    language: BoardLanguage, directory: Path = CLIPS_DIR, url: str = CLIPS_URL
) -> dict[str, str]:
    """The recorded characters for ``language``: each letter or digit, mapped to its clip's address.

    Only files named after one character of a ticket number count. A language with no folder, or an
    empty one, has none, and a board then plays the chime without speaking when speech is unavailable.
    """
    folder = directory / language.value
    if not folder.is_dir():
        return {}
    return {
        clip.stem: f"{url}/{language.value}/{clip.name}"
        for clip in sorted(folder.iterdir())
        if clip.suffix == CLIP_SUFFIX and clip.stem in NUMBER_CHARACTERS
    }
