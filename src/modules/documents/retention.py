"""Retention policy for stored documents (Issue #70).

Retention is a property of the *document*, expressed as a
:class:`~src.modules.documents.enums.RetentionClass`. This module is the single place that turns a
class into a concrete expiry instant: the ingest calls :func:`expiry_for` to stamp
``document.expires_at``, and the daily retention sweep purges anything whose stamped expiry has
passed. Keeping the arithmetic here (rather than in the service) means the class-to-lifetime mapping
in :data:`~src.modules.documents.enums.RETENTION_PERIOD_DAYS` is applied one way only.
"""

from datetime import datetime, timedelta

from src.modules.documents.enums import RETENTION_PERIOD_DAYS, RetentionClass


def expiry_for(
    retention_class: RetentionClass, *, ingested_at: datetime
) -> datetime | None:
    """Return the expiry instant for a document of ``retention_class`` ingested at ``ingested_at``.

    ``None`` for a ``permanent`` class (kept indefinitely); otherwise ``ingested_at`` plus the
    class's lifetime in days. The returned instant carries ``ingested_at``'s timezone, so a
    timezone-aware ingest timestamp yields a timezone-aware expiry.
    """
    days = RETENTION_PERIOD_DAYS[retention_class]
    if days is None:
        return None
    return ingested_at + timedelta(days=days)
