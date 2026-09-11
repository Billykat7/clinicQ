"""The deploy message the team channel gets (Issues 11 and 14).

Essential logic: the message is the only place most of the team learns what runs where, so it must
name the environment, the version, the result and a working release-notes link, and a chat outage
must never fail a deploy.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from scripts.cd.notify_deploy import (
    Result,
    WebhookKind,
    main,
    message,
    payload,
    release_notes_url,
)

RUN_URL = "https://github.com/Billykat7/clinicQ/actions/runs/1"


def test_a_release_links_its_release_note_and_a_pre_release_its_tag() -> None:
    """``0.2.0`` → RELEASE_v0_2_0.md at that tag; ``0.0.2-test`` has no note, so its tag."""
    assert release_notes_url("0.2.0").endswith(
        "/blob/v0.2.0/docs/GITHUB/RELEASES/RELEASE_v0_2_0.md"
    )
    assert release_notes_url("0.0.2-test").endswith("/tree/v0.0.2-test")


@pytest.mark.parametrize("result", list(Result))
def test_the_message_names_environment_version_result_and_links(result: Result) -> None:
    """Everything someone needs from a glance at the channel."""
    text = message("staging", "0.2.0", "0e8d51706d06", result, RUN_URL)
    assert "staging" in text and "0.2.0" in text and "(0e8d517)" in text
    assert "RELEASE_v0_2_0.md" in text and RUN_URL in text


def test_a_failure_says_the_previous_version_keeps_serving() -> None:
    """The deploy's promise, stated where people read it."""
    assert "previous version keeps serving" in message(
        "production", "0.2.0", "", Result.FAILURE, RUN_URL
    )


def test_each_service_gets_the_body_it_expects() -> None:
    """Slack reads ``text``, Discord ``content``."""
    assert payload(WebhookKind.SLACK, "hi") == {"text": "hi"}
    assert payload(WebhookKind.DISCORD, "hi") == {"content": "hi"}


def test_it_posts_to_the_webhook_and_a_dead_channel_does_not_fail_the_deploy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real HTTP post to a local stand-in for the channel; then the channel down."""
    received: list[dict[str, str]] = []

    class Channel(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            """Record the message the channel received."""
            length = int(self.headers["Content-Length"])
            received.append(json.loads(self.rfile.read(length)))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            """Keep the test output quiet."""

    server = HTTPServer(("127.0.0.1", 0), Channel)
    threading.Thread(target=server.handle_request, daemon=True).start()
    arguments = [
        "--environment",
        "staging",
        "--version",
        "0.2.0",
        "--result",
        "success",
    ]
    monkeypatch.setenv(
        "TEAM_WEBHOOK_URL", f"http://127.0.0.1:{server.server_port}/hook"
    )
    monkeypatch.setenv("TEAM_WEBHOOK_KIND", "discord")
    assert main([*arguments, "--run-url", RUN_URL]) == 0
    server.server_close()
    assert "0.2.0" in received[0]["content"]

    monkeypatch.setenv(
        "TEAM_WEBHOOK_URL", f"http://127.0.0.1:{server.server_port}/gone"
    )
    assert main([*arguments, "--run-url", RUN_URL]) == 0
