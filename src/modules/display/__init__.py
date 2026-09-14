"""The waiting-room display (M8): what a public screen in a clinic may show, and how it gets there.

Read the files in this order:

* :mod:`.enums` — who is looking at a board, which decides how much the site's display mode may
  show them;
* :mod:`.projection` — **the one privacy projection** (Issue 58, non-negotiable 4). Every board
  response, the page, its JSON and its live stream, is built from what it returns and nothing else:
  a name or a reason the site's mode or the patient's consent forbids is removed from the payload,
  never hidden by a stylesheet.
"""
