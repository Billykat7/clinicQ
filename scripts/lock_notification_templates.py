"""Write ``src/locales/notifications.lock.json``: the version and a hash of the words of every template (Issue 66).

A sent message names the template version it used, so the words of a version must never change after the
fact. The lock makes that checkable: ``tests/unit/notifications/test_template_registry.py`` fails when a
locale file's words differ from the lock while the version number has not been raised.

Run this after raising a version for new words::

    python -m scripts.lock_notification_templates
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from src.modules.notifications import template_registry as registry

LOCK = registry.LOCALES_DIR / "notifications.lock.json"


def fingerprint(body: str, subject: str | None) -> str:
    """SHA-256 of a version's words."""
    return hashlib.sha256(f"{subject or ''}\x00{body}".encode()).hexdigest()


def current_lock() -> dict[str, dict[str, object]]:
    """What the lock should say for the locale files as they are."""
    lock: dict[str, dict[str, object]] = {}
    for language in registry.languages():
        for (template, channel), text in sorted(
            registry.builtin(language).items(),
            key=lambda item: (item[0][0].value, item[0][1].value),
        ):
            lock[f"{language.value}/{template.value}/{channel.value}"] = {
                "version": text.version,
                "sha256": fingerprint(text.body, text.subject),
            }
    return lock


def main() -> int:
    """Write the lock."""
    registry.builtin.cache_clear()
    Path(LOCK).write_text(
        json.dumps(current_lock(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sys.stdout.write(f"wrote {LOCK.relative_to(Path.cwd())}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
