"""Display-module enumerations (Issue 58).

* :class:`BoardViewer` — who is looking at a waiting-room board. The site's display mode says what
  its **own screen** may show; a copy of the board opened by anyone who knows the address is not that
  screen, so the projection caps what it gets.
* :class:`BoardPersonalField` — the payload keys that carry something about a person.
"""

from enum import StrEnum


class BoardViewer(StrEnum):
    """Who a board response is for, as far as the server can tell.

    - ``ANONYMOUS``: an address typed or shared, with nothing to show it is the clinic's own screen.
      A board is a public page and still opens, but it gets **numbers only**, whatever the site's
      display mode says: a name the clinic agreed to show in its waiting room was not agreed to be
      shown to the internet. Until kiosk devices are paired (Issue 61) this is every board.
    - ``STAFF``: somebody signed in who works at this clinic, previewing its screen. They see what
      the waiting room sees, under the site's own mode.
    """

    ANONYMOUS = "anonymous"
    STAFF = "staff"

    @property
    def sees_site_mode(self) -> bool:
        """Whether the site's own display mode applies, rather than numbers only."""
        return self is not BoardViewer.ANONYMOUS


class BoardPersonalField(StrEnum):
    """The keys of a board payload that say something about a person, and so may be absent.

    A board entry always has a number. These two appear **only** when the projection put a value in
    them; under ``number_only`` neither key exists anywhere in a response (Issue 58), and the payload
    check refuses to send one that has either.

    - ``NAME``: the patient's name, full or shortened, with their consent.
    - ``COMMENT``: the reason for the visit, beside a full name, with standing and per-visit consent.
    """

    NAME = "name"
    COMMENT = "comment"
