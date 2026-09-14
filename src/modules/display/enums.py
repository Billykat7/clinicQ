"""Display-module enumerations (Issue 58).

* :class:`BoardViewer` — who is looking at a waiting-room board. The site's display mode says what
  its **own screen** may show; a copy of the board opened by anyone who knows the address is not that
  screen, so the projection caps what it gets.
* :class:`BoardPersonalField` — the payload keys that carry something about a person.
* :class:`PairingState` — what an unpaired kiosk box is told when it asks whether it has been paired.
"""

from enum import StrEnum


class BoardViewer(StrEnum):
    """Who a board response is for, as far as the server can tell.

    - ``ANONYMOUS``: an address typed or shared, with nothing to show it is the clinic's own screen.
      It would get **numbers only**, whatever the site's display mode says: a name the clinic agreed
      to show in its waiting room was not agreed to be shown to the internet.
    - ``STAFF``: somebody signed in who works at this clinic, previewing its screen. They see what
      the waiting room sees, under the site's own mode.
    - ``DEVICE``: a kiosk box paired with this clinic (Issue 61): the clinic's own screen, under the
      site's own mode.

    Since Issue 61 no board route serves ``ANONYMOUS`` at all; the projection still caps it, so a route
    that ever did would show numbers only.
    """

    ANONYMOUS = "anonymous"
    STAFF = "staff"
    DEVICE = "device"

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


class PairingState(StrEnum):
    """What ``GET /display/pairing`` tells a kiosk box about itself (Issue 61).

    - ``WAITING``: its code is on the screen and still valid; ask again shortly.
    - ``PAIRED``: a manager typed the code in; open the board at ``board_url``.
    - ``EXPIRED``: the code ran out, or the box is unknown or removed; reload for a new code.
    """

    WAITING = "waiting"
    PAIRED = "paired"
    EXPIRED = "expired"
