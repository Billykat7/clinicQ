"""The waiting-room board, listened to: what it says aloud when a ticket is called, in Chromium (Issue 60).

A test browser has no speakers and usually no voices, so the page's speech engine is replaced before
the board loads (:data:`_LISTENER`) by a stand-in that says each sentence in a fixed time and writes down
what it was asked to say, in which language, at what volume, and when it started and stopped. The chime
and any recordings are real ``<audio>`` played by Chromium, and every ``play()`` is written down too. So
each criterion is checked against what the board asked the browser to say:

* **A chime, then the number and the room.** The chime starts first; the sentence starts after it ends.
* **Two calls at once are said one after the other.** Neither sentence overlaps the other.
* **Never a name.** With full names on the screen and every consent given, the name is drawn on the board
  and appears in nothing the board says, in no ``board:call`` event, and in no sound it plays.
* **Muting keeps the highlight.** A muted clinic's board says and plays nothing and still highlights the
  call; the volume a clinic sets is the volume of the chime and the voice.
* **No speech, recordings instead.** With speech synthesis removed from the page, the number is spelled
  from recorded clips, one character after another.
* **The clinic's language.** An isiZulu clinic's board speaks isiZulu with an isiZulu voice, and English
  when the browser has no isiZulu voice.
* **Sound without a click.** The browser reads the board's pages as allowed to autoplay their own audio,
  and the dashboard as not.
"""

from __future__ import annotations

import io
import json
import struct
import time
import wave
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import update

from src.commons.enums import (
    BoardLanguage,
    ConsentPurpose,
    DisplayMode,
    PatientChannel,
    TicketSource,
)
from src.database.models import Patient, Queue, Site
from src.modules.patients.consent import record_consent
from src.modules.queue.sequence import issue_ticket
from src.web import display as display_routes
from tests.factories import PatientFactory

pytestmark = pytest.mark.postgres

#: How long the stand-in voice takes to say any sentence.
SPEAK_MS = 400
NAME = "Thandiwe Mokoena-Dlamini"

#: The page's speech engine, replaced before any board script runs. ``voices`` is what the browser offers
#: (``?voices=en-ZA,zu-ZA`` on the board's address), or ``?speech=none`` for a browser with no speech at all.
_LISTENER = """
(() => {
  const params = new URLSearchParams(location.search);
  const said = [];
  const played = [];
  const calls = [];
  window.__said = said;
  window.__played = played;
  window.__calls = calls;
  document.addEventListener('board:call', (event) => calls.push(JSON.stringify(event.detail)));

  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () {
    const entry = { src: this.src, volume: this.volume, at: performance.now(), ended: null, allowed: null };
    played.push(entry);
    this.addEventListener('ended', () => { entry.ended = performance.now(); }, { once: true });
    const result = play.call(this);
    result.then(() => { entry.allowed = true; }, () => { entry.allowed = false; entry.ended = performance.now(); });
    return result;
  };

  if (params.get('speech') === 'none') {
    Object.defineProperty(window, 'speechSynthesis', { value: undefined, configurable: true });
    Object.defineProperty(window, 'SpeechSynthesisUtterance', { value: undefined, configurable: true });
    return;
  }
  const voices = (params.get('voices') || 'en-ZA').split(',').filter(Boolean)
    .map((lang) => ({ lang, name: 'Test ' + lang, default: false, localService: true, voiceURI: lang }));
  // A plain utterance: the real one refuses a voice that is not one of the browser's own.
  Object.defineProperty(window, 'SpeechSynthesisUtterance', {
    configurable: true,
    value: function (text) { this.text = text; this.lang = ''; this.voice = null; this.volume = 1; this.rate = 1; },
  });
  const listeners = {};
  let timer = null;
  let current = null;
  Object.defineProperty(window, 'speechSynthesis', {
    configurable: true,
    value: {
      getVoices: () => voices,
      addEventListener: (name, fn) => { listeners[name] = fn; },
      speak(utterance) {
        const entry = {
          text: utterance.text, lang: utterance.lang, voice: utterance.voice && utterance.voice.lang,
          volume: utterance.volume, at: performance.now(), ended: null,
        };
        said.push(entry);
        current = utterance;
        if (utterance.onstart) utterance.onstart();
        timer = setTimeout(() => { entry.ended = performance.now(); current = null; utterance.onend && utterance.onend(); }, __SPEAK_MS__);
      },
      cancel() { clearTimeout(timer); if (current && current.onend) current.onend(); current = null; },
      get speaking() { return current !== null; },
    },
  });
})();
""".replace("__SPEAK_MS__", str(SPEAK_MS))


def _until(page: Any, predicate: str, timeout: float = 20.0) -> None:
    """Wait until ``predicate`` is true in the page (asked with ``evaluate``)."""
    started = time.monotonic()
    while not page.evaluate(predicate):
        if time.monotonic() - started > timeout:
            raise AssertionError(f"timed out waiting for {predicate}")
        time.sleep(0.05)


def _board(
    day: SimpleNamespace, *, voices: Iterable[str] = ("en-ZA",), speech: bool = True
) -> Any:
    """A paired board with the listener installed, live on its stream."""
    page = day.new_page(1280, 720)
    page.add_init_script(_LISTENER)
    query = "?speech=none" if not speech else "?voices=" + ",".join(voices)
    page.goto(day.clinic.page_path + query)
    page.wait_for_selector(".panel")
    _until(
        page,
        "() => window.ClinicQBoardLive && window.ClinicQBoardLive.state().state === 'live'",
    )
    return page


def _log(page: Any) -> list[dict[str, Any]]:
    return page.evaluate("() => window.ClinicQBoardAnnounce.log()")


def _said(page: Any) -> list[dict[str, Any]]:
    return page.evaluate("() => window.__said")


def _played(page: Any) -> list[dict[str, Any]]:
    return page.evaluate("() => window.__played")


def _settings(day: SimpleNamespace, **values: Any) -> None:
    with day.clinic.session() as db:
        db.execute(update(Site).where(Site.id == day.clinic.site).values(**values))
        db.commit()


def _finished(page: Any, count: int) -> None:
    """Wait until ``count`` calls have been said to the end."""
    _until(
        page,
        f"() => window.ClinicQBoardAnnounce.log().filter((e) => e.ended).length >= {count}",
    )


def test_a_call_plays_the_chime_then_says_the_number_and_the_room(
    board_day: SimpleNamespace,
) -> None:
    """The chime first, the sentence after it ends, spelled so each character is heard."""
    triage, *_ = board_day.open_queues(1)
    board_day.issue(triage, 2)
    page = _board(board_day)

    number = board_day.call(triage)
    _finished(page, 1)

    [entry] = _log(page)
    assert (entry["number"], entry["room"], entry["how"]) == (
        number,
        "Room 2",
        "speech",
    )
    [chime] = _played(page)
    [sentence] = _said(page)
    assert chime["src"].endswith("/static/audio/chime.wav")
    assert chime["allowed"] is True, "the board page may play its chime without a click"
    assert sentence["text"] == f"Number {' '.join(number)}, please go to Room 2."
    assert sentence["lang"] == "en-ZA" and sentence["voice"] == "en-ZA"
    assert chime["ended"] is not None and sentence["at"] >= chime["ended"]
    heard = f"said: {sentence['text']!r}, {sentence['at'] - chime['at']:.0f} ms after the chime began"
    print("\n" + heard)  # noqa: T201 — quoted in the PR


def test_two_calls_at_the_same_moment_are_said_one_after_the_other(
    board_day: SimpleNamespace,
) -> None:
    """Two queues call together; the second chime waits for the first sentence to end."""
    triage, consult = board_day.open_queues(2)
    board_day.issue(triage, 1)
    board_day.issue(consult, 1)
    page = _board(board_day)

    first = board_day.call(triage)
    second = board_day.call(consult)
    _finished(page, 2)

    log = _log(page)
    assert {(e["number"], e["room"]) for e in log} == {
        (first, "Room 2"),
        (second, "Room 4"),
    }
    earlier, later = sorted(_said(page), key=lambda s: s["at"])
    assert later["at"] >= earlier["ended"], (
        "the second call began before the first ended"
    )
    chimes = sorted(p["at"] for p in _played(page))
    assert len(chimes) == 2 and chimes[1] >= earlier["ended"]
    print(  # noqa: T201 — quoted in the PR
        "\n"
        + "\n".join(
            f"{s['text']!r}: {s['at']:.0f}–{s['ended']:.0f} ms"
            for s in (earlier, later)
        )
    )


def test_no_name_is_ever_said_even_with_full_names_on_the_screen(
    board_day: SimpleNamespace,
) -> None:
    """Full names shown, consent given: the name is on the board and in nothing it says or plays."""
    triage, *_ = board_day.open_queues(1)
    _settings(board_day, display_mode=DisplayMode.FULL.value)
    with board_day.clinic.session() as db:
        patient = PatientFactory.create(db, display_name=NAME)
        record_consent(
            db,
            db.get_one(Patient, patient.id),
            ConsentPurpose.DISPLAY_NAME,
            granted=True,
            channel=PatientChannel.WEB,
        )
        issue_ticket(
            db,
            queue=db.get_one(Queue, triage),
            source=TicketSource.WEB,
            patient_id=patient.id,
        )
        db.commit()
    page = _board(board_day)

    number = board_day.call(triage)
    _finished(page, 1)
    _until(page, "() => !!document.querySelector('.serving-name')?.textContent")

    assert NAME in page.locator(".serving-name").first.text_content()
    heard = json.dumps(
        {
            "log": _log(page),
            "said": _said(page),
            "played": _played(page),
            "events": page.evaluate("() => window.__calls"),
        },
        ensure_ascii=False,
    )
    assert number in heard
    for part in NAME.replace("-", " ").split():
        assert part not in heard, f"{part!r} was in what the board said"
    assert page.evaluate("() => window.__calls") == [
        json.dumps({"number": number, "room": "Room 2"}, separators=(",", ":"))
    ]


def test_a_muted_clinic_still_highlights_the_call_and_a_volume_is_the_volume_heard(
    board_day: SimpleNamespace,
) -> None:
    """Muted: no chime, no voice, the highlight as ever. Then unmuted at 35 percent: both at 0.35."""
    triage, *_ = board_day.open_queues(1)
    board_day.issue(triage, 3)
    _settings(board_day, announce_audio=False)
    page = _board(board_day)

    board_day.call(triage)
    _until(page, "() => !!document.querySelector('.serving.is-new')")
    time.sleep(1.5)  # time enough for a chime and a sentence to have started
    assert _log(page) == [] and _said(page) == [] and _played(page) == []
    assert page.locator(".serving.is-new .serving-state").text_content() == "Called now"

    _settings(board_day, announce_audio=True, announce_volume=35)
    board_day.call(triage, finish_previous=True)
    _finished(page, 1)
    [chime] = _played(page)
    [sentence] = _said(page)
    assert chime["volume"] == pytest.approx(0.35) and sentence[
        "volume"
    ] == pytest.approx(0.35)


def _silence(seconds: float = 0.1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(struct.pack("<h", 0) * int(8000 * seconds))
    return buffer.getvalue()


def test_a_browser_without_speech_spells_the_number_from_recorded_clips(
    board_day: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No speech synthesis: the chime, then one clip per character of the number, in order."""
    characters = "0123456789T"
    monkeypatch.setattr(
        display_routes,
        "number_clips",
        lambda language: {c: f"/static/audio/numbers/test/{c}.wav" for c in characters},
    )
    triage, *_ = board_day.open_queues(1)
    board_day.issue(triage, 1)
    page = _board(board_day, speech=False)
    clip = _silence()
    page.route(
        "**/static/audio/numbers/test/*.wav",
        lambda route: route.fulfill(body=clip, content_type="audio/wav"),
    )

    number = board_day.call(triage)
    _finished(page, 1)

    [entry] = _log(page)
    assert entry["how"] == "clips"
    sources = [p["src"].rsplit("/", 1)[-1] for p in _played(page)]
    assert sources == ["chime.wav", *[f"{c}.wav" for c in number]]
    assert all(p["allowed"] for p in _played(page))


def test_an_isizulu_board_speaks_isizulu_and_falls_back_to_english_without_a_voice(
    board_day: SimpleNamespace,
) -> None:
    """With an isiZulu voice, the isiZulu sentence; with only English voices, the English one."""
    triage, *_ = board_day.open_queues(1)
    board_day.issue(triage, 2)
    _settings(board_day, board_language=BoardLanguage.ISIZULU.value)

    zulu = _board(board_day, voices=("en-ZA", "zu-ZA"))
    number = board_day.call(triage)
    _finished(zulu, 1)
    [sentence] = _said(zulu)
    assert sentence["text"] == f"Inombolo {' '.join(number)}, sicela uye ku-Room 2."
    assert (sentence["lang"], sentence["voice"]) == ("zu-ZA", "zu-ZA")

    english_only = _board(board_day, voices=("en-ZA", "en-GB"))
    later = board_day.call(triage, finish_previous=True)
    _finished(english_only, 1)
    [fallback] = _said(english_only)
    assert fallback["text"] == f"Number {' '.join(later)}, please go to Room 2."
    assert fallback["lang"] == "en-ZA"


def test_the_board_may_play_sound_without_a_click_and_the_dashboard_may_not(
    board_day: SimpleNamespace,
) -> None:
    """``Permissions-Policy: autoplay=(self)`` on the board; ``autoplay=()`` everywhere else.

    Asked of the browser itself (``document.featurePolicy``), because the test browser is started with
    autoplay allowed and would play on either page; a kiosk's Chromium applies the policy as it reads it.
    """
    board_day.open_queues(1)
    board = _board(board_day)
    manager = board_day.manager_page()
    manager.goto(f"/dashboard/sites/{board_day.clinic.site}/settings/display")
    allows = "() => document.featurePolicy.allowsFeature('autoplay')"
    assert board.evaluate(allows) is True
    assert manager.evaluate(allows) is False
    played = """async () => {
      try { await new Audio('/static/audio/chime.wav').play(); return 'played'; }
      catch (error) { return error.name; }
    }"""
    assert board.evaluate(played) == "played"
