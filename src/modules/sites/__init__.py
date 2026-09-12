"""Sites: a clinic, its profile, settings, display mode, staff and reports.

Issue 18 declares the module's authorization footprint (:mod:`.rbac_manifest`) ahead of the module
itself, so the roles' grants are settled and the dashboard shell (Issue 48) can gate on them. The
``sites`` model and its CRUD land with Issue 23, the display and privacy settings with Issue 27.
"""
