"""Operational alerts from the running application to the team channel (Issue 61, on Issue 14).

Issue 14 gave the team one channel for what needs a person: Gatus posts an outage there, and the deploy
workflow posts every deploy (``scripts/cd/notify_deploy.py``). A silent waiting-room board is the same
kind of news, and only the application knows about it, so the application posts it to the same
incoming webhook (``TEAM_WEBHOOK_URL``, a Slack or Discord URL, ``TEAM_WEBHOOK_KIND``).

The message shapes match ``notify_deploy.py``'s (``{"text": …}`` for Slack, ``{"content": …}`` for
Discord). That script cannot import this module, because it runs on a CI runner before any dependency
is installed, so the few lines are repeated here rather than shared.

**Posting never raises and never waits long.** An alert that cannot be delivered is logged at WARNING
with its text, which the log pipeline (Issue 6) keeps. A chat outage must not stop the sweep that found
the problem.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Final

from src.commons.enums import TeamWebhookKind
from src.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

#: Seconds to wait for the chat service.
TIMEOUT_SECONDS: Final = 10


def webhook_body(kind: TeamWebhookKind, text: str) -> dict[str, str]:
    """The JSON body each chat service expects."""
    return {"content": text} if kind is TeamWebhookKind.DISCORD else {"text": text}


def post_team_alert(text: str, settings: Settings | None = None) -> bool:
    """Log ``text`` as a warning and post it to the team channel; whether the post was delivered.

    Without ``TEAM_WEBHOOK_URL`` the log line is the alert. Never raises.
    """
    cfg = settings or get_settings()
    logger.warning("Team alert: %s", text)
    url = cfg.team_webhook_url.strip()
    if not url:
        return False
    request = urllib.request.Request(
        url,
        data=json.dumps(webhook_body(cfg.team_webhook_kind, text)).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except (OSError, ValueError) as exc:
        logger.warning(
            "Could not post the team alert (%s); it is in this log only.",
            type(exc).__name__,
        )
        return False
