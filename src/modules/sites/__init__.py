"""Sites: a clinic, its profile, settings, display mode, staff and reports.

Read the files in this order:

* :mod:`.rbac_manifest` — the module's authorization footprint, declared by Issue 18 ahead of the
  module itself so the roles' grants were settled before anything gated on them;
* :mod:`.schemas` — what a client may send, and what the API returns;
* :mod:`.service` — the rules and every query, including the ``ST_DWithin`` radius search the GiST
  index exists for;
* :mod:`.hours` — is this clinic open, and when does it open next? Pure functions of their inputs,
  with the precedence (a closure beats a holiday rule beats the weekly schedule) resolved once;
  :mod:`.hours_service` writes them, and :mod:`.availability` is the single join gate every channel
  asks;
* :mod:`.geocoding` — turning a typed address into a coordinate **on the server**, because the
  Content-Security-Policy allows ``connect-src 'self'`` and a browser cannot reach a geocoder;
* :mod:`.settings` — what the waiting-room board may show, and what it takes to change it. This
  is where non-negotiable 4 lives, and the warning text lives next to the rule it describes;
* :mod:`.router` — the HTTP surface: platform routes behind a ``business``-tier grant, and
  per-clinic routes behind the site guard (Issue 19).

The ``sites`` model and its CRUD landed with Issue 23, opening hours and closures with Issue 24,
and the display and privacy settings with Issue 27. The services catalogue comes with Issue 26 and
onboarding with Issue 29.
"""
