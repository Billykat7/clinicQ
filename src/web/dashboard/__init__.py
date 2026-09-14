"""The clinic dashboard: the screens reception, nurses and the clinic manager run the day from (M7).

* :mod:`.shell`: the frame every screen lives in (Issue 48): the current clinic, the switcher, the
  signed-in identity and the navigation the caller's grants at that clinic open.
* :mod:`.routes`: the pages. Each resolves the frame, then renders; the JSON API each page calls
  re-checks the same grant, because the page governs what is offered and the API what is done.
"""
