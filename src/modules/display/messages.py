"""The board's health-message ticker (Issue 56): short, general notices for a waiting room.

The ticker is the one place a board says something other than a ticket number, so what it may say is
fixed here rather than typed by anyone at a clinic: general health and waiting-room notices, never
anything about a patient, a diagnosis or a person. It shows one message at a time and changes it every
few seconds (``board.js``); it never scrolls, because moving text is hard to read from across a room and
uncomfortable for people sensitive to motion.

Only English is written so far. A board set to another language shows these in English until the
translation framework (Issue 77) supplies its own, and :func:`health_messages` says which language the
messages it returned are in, so the page can mark them with the right ``lang``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from src.commons.enums import BoardLanguage

#: The notices, by language. Short enough to read in one glance at five metres.
HEALTH_MESSAGES: Final[Mapping[BoardLanguage, tuple[str, ...]]] = {
    BoardLanguage.ENGLISH: (
        "Listen for your number and watch this screen.",
        "Keep your ticket with you until you are seen.",
        "Joined from your phone? We will send you a message when you are next.",
        "Wash or sanitise your hands often.",
        "Please tell the front desk if you feel worse while you wait.",
        "Bring your clinic card and your medicine to every visit.",
    ),
}

#: What a board falls back to when its language has no messages yet.
FALLBACK_LANGUAGE: Final = BoardLanguage.ENGLISH


def health_messages(language: BoardLanguage) -> tuple[BoardLanguage, tuple[str, ...]]:
    """``(the language they are in, the messages)`` for a board set to ``language``."""
    if language in HEALTH_MESSAGES:
        return language, HEALTH_MESSAGES[language]
    return FALLBACK_LANGUAGE, HEALTH_MESSAGES[FALLBACK_LANGUAGE]
