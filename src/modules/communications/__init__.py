"""Communications RBAC manifest home (Issue #145, M26).

Not a routes-owning module — Communications' actual code is split across
``src/modules/messaging`` (messages, announcements), ``src/modules/alerts`` (alerts) and
``src/modules/notifications`` (the notification centre). This package exists solely to hold
``rbac_manifest.py``, the single declaration of the whole ``communications`` resource tree those
three modules enforce against — see that file's docstring, and ``src/modules/maintenance/
rbac_manifest.py`` (Issue #149) for the identical "resource root without its own routes"
precedent (``vendors``).
"""
