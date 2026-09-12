"""Folding a typed place name into the form it is matched in (Issue 34).

A patient types "Kwa-Thema", "kwathema" or "KwaThéma"; a USSD handset may send any of them in capital
letters. They are one place, so both the stored names and the query are folded the same way before
they meet: accents stripped, case folded, and everything that is not a letter or a digit removed.
What is left is compared by trigram similarity, which is what forgives an actual misspelling
("Soweeto").

The function is here, in ``commons``, rather than in the discovery module because migration ``0014``
uses it to fill ``area_name.search_key``. A migration freezes what it writes, so if this folding ever
changes, a new migration recomputes the stored keys; the unit test pins today's behaviour.
"""

import unicodedata
from typing import Final

#: The longest folded key stored, matching ``area_name.search_key``.
MAX_SEARCH_KEY_LENGTH: Final = 160


def fold_for_search(text: str) -> str:
    """Return ``text`` as lowercase ASCII letters and digits only.

    >>> fold_for_search("Kwa-Théma ")
    'kwathema'
    >>> fold_for_search("Botha's Hill")
    'bothashill'
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = "".join(ch for ch in stripped.casefold() if ch.isascii() and ch.isalnum())
    return folded[:MAX_SEARCH_KEY_LENGTH]
