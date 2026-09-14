"""What a board may say aloud (Issue 60): the number and the room, in the clinic's language, never a name.

* Every sentence has exactly the two blanks ``{number}`` and ``{room}``, and the check refuses a sentence
  with any other blank, so no future edit can add a patient's name or reason to what a board speaks.
* The five languages the issue names each have their own sentence and voice; any other speaks English.
* Nothing is claimed as checked by a fluent speaker until someone has: ``checked_by`` is empty for all.
* Recordings are found by character, and only characters a ticket number is made of count.
* The committed chime is the one ``scripts/audio/make_chime.py`` makes.
"""

from __future__ import annotations

import io
import struct
import wave
from pathlib import Path

import pytest

from scripts.audio import make_chime
from src.commons.enums import BoardLanguage
from src.modules.display.announcements import (
    ALLOWED_PLACEHOLDERS,
    FALLBACK_LANGUAGE,
    PHRASES,
    AnnouncementPhraseError,
    announcement_for,
    number_clips,
    placeholders,
)
from src.modules.display.enums import BoardPersonalField

#: The languages the issue asks a clinic to be able to choose for its announcements.
ISSUE_LANGUAGES = (
    BoardLanguage.ENGLISH,
    BoardLanguage.ISIZULU,
    BoardLanguage.ISIXHOSA,
    BoardLanguage.AFRIKAANS,
    BoardLanguage.SESOTHO,
)


@pytest.mark.parametrize("language", list(PHRASES))
def test_every_sentence_says_the_number_and_the_room_and_nothing_else(
    language: BoardLanguage,
) -> None:
    """Two blanks, each once: there is no blank a name or a reason could go into."""
    phrase = PHRASES[language]
    assert sorted(placeholders(phrase.call)) == sorted(ALLOWED_PLACEHOLDERS)
    assert phrase.language is language
    assert phrase.voice_tag.split("-")[0] == language.value


@pytest.mark.parametrize(
    "template",
    [
        "Number {number}, {name}, please go to {room}.",
        "Number {number} ({comment}), please go to {room}.",
        "Number {number}, please go to {room}. {patient.display_name}",
        "Number {number!r}, please go to {room}.",
        "Number {number:>5}, please go to {room}.",
        "Please go to {room}.",
        "Number {number}, {number}, please go to {room}.",
    ],
)
def test_a_sentence_with_any_other_blank_is_refused(template: str) -> None:
    """The guard a future edit meets: a name, a reason, a formatted field, a missing or doubled blank."""
    with pytest.raises(AnnouncementPhraseError):
        placeholders(template)


def test_no_personal_field_name_can_be_a_blank() -> None:
    """The board's personal keys (``name``, ``comment``) are not among the allowed blanks."""
    assert not ALLOWED_PLACEHOLDERS & {field.value for field in BoardPersonalField}


def test_each_language_the_issue_names_has_its_own_sentence_and_others_speak_english() -> (
    None
):
    """A clinic choosing one of the five hears it; a clinic choosing another hears English, marked as such."""
    for language in ISSUE_LANGUAGES:
        assert announcement_for(language).language is language
    assert len({PHRASES[language].call for language in ISSUE_LANGUAGES}) == len(
        ISSUE_LANGUAGES
    )
    spoken = announcement_for(BoardLanguage.TSHIVENDA)
    assert spoken.language is FALLBACK_LANGUAGE is BoardLanguage.ENGLISH


def test_no_sentence_claims_a_fluent_speakers_check_it_has_not_had() -> None:
    """``checked_by`` is filled in by the person who checked it, with docs/OPS/BOARD_AUDIO.md, not before."""
    assert all(phrase.checked_by is None for phrase in PHRASES.values())


def test_recordings_are_found_by_character_and_nothing_else_counts(
    tmp_path: Path,
) -> None:
    """``A.wav`` and ``7.wav`` are clips; ``room.wav``, ``a.wav`` and ``A.mp3`` are not; no folder, no clips."""
    folder = tmp_path / "zu"
    folder.mkdir()
    for name in ("A.wav", "7.wav", "0.wav", "room.wav", "a.wav", "B.mp3"):
        (folder / name).write_bytes(b"RIFF")

    clips = number_clips(BoardLanguage.ISIZULU, directory=tmp_path, url="/audio")

    assert clips == {
        "0": "/audio/zu/0.wav",
        "7": "/audio/zu/7.wav",
        "A": "/audio/zu/A.wav",
    }
    assert number_clips(BoardLanguage.SESOTHO, directory=tmp_path) == {}


def test_the_committed_chime_is_the_one_the_script_makes() -> None:
    """Same format and length, and every sample within a rounding step of the generated one."""
    committed = make_chime.CHIME_FILE.read_bytes()
    generated = make_chime.wav_bytes()
    with (
        wave.open(io.BytesIO(committed)) as have,
        wave.open(io.BytesIO(generated)) as want,
    ):
        assert (have.getnchannels(), have.getsampwidth(), have.getframerate()) == (
            1,
            2,
            make_chime.SAMPLE_RATE,
        )
        assert have.getnframes() == want.getnframes()
        frames = have.getnframes()
        ours = struct.unpack(f"<{frames}h", have.readframes(frames))
        theirs = struct.unpack(f"<{frames}h", want.readframes(frames))
    assert max(abs(a - b) for a, b in zip(ours, theirs, strict=True)) <= 1
    assert 1.0 < frames / make_chime.SAMPLE_RATE < 1.5
