"""What the board page is given to say aloud (Issue 60), checked through its context and its JSON.

The browser test (``tests/e2e/display/test_board_announce.py``) listens to what a board actually says.
Here the route is checked for what it hands the page, never its markup
(``docs/IDE/RULES/testing-strategy.mdc``):

* the clinic's sentence and voice for its language, English for a language with no sentence yet, and no
  recordings claimed where none exist;
* **no name, anywhere in what the page is given to speak from**, even with the widest display mode and
  every consent given: the sentences have blanks for the number and the room only;
* the clinic's mute and volume reach every board response, so a change applies to the next call;
* the chime is served to the board.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from starlette import status

from src.commons.enums import BoardLanguage, ConsentPurpose, DisplayMode
from src.database.models import Site
from src.modules.display.announcements import CHIME_PATH, placeholders
from tests.integration.display.conftest import NOMVULA, REASON

#: What the page speaks from: every announcement key of the page's context.
_SPOKEN_KEYS = (
    "announce_call",
    "announce_voice",
    "announce_language",
    "announce_fallback_call",
    "announce_fallback_voice",
    "announce_clips",
    "announce_chime",
)


def _language(board: SimpleNamespace, language: BoardLanguage) -> None:
    with board.session() as db:
        db.get_one(Site, board.world.site_a).board_language = language.value
        db.commit()


def test_the_page_speaks_the_clinics_sentence_in_its_language_and_english_where_there_is_none(
    board: SimpleNamespace,
) -> None:
    """English by default; isiZulu once chosen; Tshivenda, which has no sentence yet, speaks English."""
    device = board.device()
    page = f"/display/{board.world.site_a}"

    english = device.get(page).context  # type: ignore[attr-defined]
    assert english["announce_call"] == "Number {number}, please go to {room}."
    assert english["announce_voice"] == "en-ZA"
    assert english["announce_language"] is BoardLanguage.ENGLISH
    assert english["announce_chime"] == CHIME_PATH
    # No recordings are committed yet, and none is claimed.
    assert english["announce_clips"] == {}

    _language(board, BoardLanguage.ISIZULU)
    zulu = device.get(page).context  # type: ignore[attr-defined]
    assert zulu["announce_language"] is BoardLanguage.ISIZULU
    assert zulu["announce_voice"] == "zu-ZA"
    assert sorted(placeholders(zulu["announce_call"])) == ["number", "room"]
    assert zulu["announce_fallback_call"] == english["announce_call"]

    _language(board, BoardLanguage.TSHIVENDA)
    venda = device.get(page).context  # type: ignore[attr-defined]
    assert venda["announce_language"] is BoardLanguage.ENGLISH
    assert venda["announce_call"] == english["announce_call"]


def test_nothing_the_page_speaks_from_holds_a_name_even_when_the_screen_shows_one(
    board: SimpleNamespace,
) -> None:
    """Full names on screen, every consent given, a reason shown: the name is on the board, not in its voice."""
    board.display(DisplayMode.FULL, show_comment=True)
    patient = board.patient()
    for purpose in (ConsentPurpose.DISPLAY_NAME, ConsentPurpose.DISPLAY_COMMENT):
        board.consent(patient, purpose, granted=True)
    board.ticket(board.world.triage, patient, reason=REASON, comment_consent=True)

    context = board.device().get(f"/display/{board.world.site_a}").context  # type: ignore[attr-defined]

    # The screen's own payload does carry the name: this is the case the voice must not follow.
    assert NOMVULA in json.dumps(context["payload"], ensure_ascii=False)
    spoken = json.dumps(
        {key: str(context[key]) for key in _SPOKEN_KEYS}, ensure_ascii=False
    )
    for word in (*NOMVULA.split(), *REASON.split()):
        assert word not in spoken
    for key in ("announce_call", "announce_fallback_call"):
        assert sorted(placeholders(context[key])) == ["number", "room"]


def test_the_clinics_mute_and_volume_reach_every_board_response(
    board: SimpleNamespace,
) -> None:
    """``announce_audio`` and ``announce_volume`` are in the page's payload and in /state."""
    device = board.device()
    first = device.get(board.state(board.world.site_a)).json()
    assert (first["announce_audio"], first["announce_volume"]) == (True, 80)

    with board.session() as db:
        site = db.get_one(Site, board.world.site_a)
        site.announce_audio = False
        site.announce_volume = 35
        db.commit()

    muted = device.get(board.state(board.world.site_a)).json()
    assert (muted["announce_audio"], muted["announce_volume"]) == (False, 35)
    payload = device.get(f"/display/{board.world.site_a}").context["payload"]  # type: ignore[attr-defined]
    assert (payload["announce_audio"], payload["announce_volume"]) == (False, 35)


def test_the_chime_is_served_to_the_board(board: SimpleNamespace) -> None:
    """A short WAV file from the board's own origin, which its Permissions-Policy lets it play."""
    chime = board.device().get(CHIME_PATH)
    assert chime.status_code == status.HTTP_200_OK
    assert chime.content[:4] == b"RIFF" and chime.content[8:12] == b"WAVE"
