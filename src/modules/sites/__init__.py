"""Sites: a clinic, its profile, settings, display mode, staff and reports.

Read the files in this order:

* :mod:`.rbac_manifest` — the module's authorization footprint, declared by Issue 18 ahead of the
  module itself so the roles' grants were settled before anything gated on them;
* :mod:`.schemas` — what a client may send, and what the API returns;
* :mod:`.service` — the rules and every query, including the ``ST_DWithin`` radius search the GiST
  index exists for;
* :mod:`.geocoding` — turning a typed address into a coordinate **on the server**, because the
  Content-Security-Policy allows ``connect-src 'self'`` and a browser cannot reach a geocoder;
* :mod:`.router` — the HTTP surface: platform routes behind a ``business``-tier grant, and
  per-clinic routes behind the site guard (Issue 19).

The ``sites`` model and its CRUD landed with Issue 23. Opening hours come with Issue 24, the
services catalogue with Issue 26, the display and privacy settings with Issue 27, and onboarding
with Issue 29.
"""
