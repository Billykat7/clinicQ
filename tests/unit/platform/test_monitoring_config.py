"""The uptime checks keep the promises Issue 14 made, in the file Gatus reads.

Three of the acceptance criteria are properties of one YAML file, and each is a line a later PR
could change without anything failing until the night it mattered: an alert that names only a URL,
a threshold that makes the alert arrive after the clinic has already phoned, or a repeat setting
that pages the team every minute of one incident. Offline: the file is parsed, nothing is started.
"""

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
GATUS_CONFIG = REPO_ROOT / "infra" / "monitoring" / "gatus.yaml"
RUNBOOK = REPO_ROOT / "docs" / "CICD" / "RUNBOOK_ALERTS.md"

#: The environments that must be watched, each on its liveness path.
WATCHED = {"staging", "production"}
HEALTH_PATH = "/health"

#: Issue 14: "An intentional staging outage raises an alert within 5 minutes."
ALERT_WITHIN_SECONDS = 300

#: "Alerts name a responsible role, not just a URL" (docs/TEAM/WORKLOAD_SPLIT.md §1 roles A–F).
NAMES_A_ROLE = re.compile(r"Responsible: .+\(role [A-F]\)")

_DURATION = re.compile(r"^(?P<value>\d+)(?P<unit>[smh])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}


def _config() -> dict[str, Any]:
    """The Gatus configuration, parsed."""
    return yaml.safe_load(GATUS_CONFIG.read_text(encoding="utf-8"))


def _seconds(duration: str) -> int:
    """``60s`` → 60, ``2m`` → 120."""
    match = _DURATION.match(duration)
    assert match, f"unreadable duration {duration!r}"
    return int(match["value"]) * _UNIT_SECONDS[match["unit"]]


def _provider(config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The one alerting provider configured, and its settings."""
    ((name, provider),) = config["alerting"].items()
    return name, provider


def test_staging_and_production_are_each_watched_on_health() -> None:
    """One check per environment, on the liveness path."""
    endpoints = {endpoint["name"]: endpoint for endpoint in _config()["endpoints"]}
    assert set(endpoints) == WATCHED
    for name, endpoint in endpoints.items():
        assert endpoint["url"].endswith(HEALTH_PATH), name


def test_every_alert_names_the_responsible_role_and_the_runbook() -> None:
    """Whoever reads the channel at 07:30 learns who acts, and where the steps are."""
    config = _config()
    provider, _ = _provider(config)
    for endpoint in config["endpoints"]:
        (alert,) = endpoint["alerts"]
        assert alert["type"] == provider, endpoint["name"]
        assert NAMES_A_ROLE.search(alert["description"]), endpoint["name"]
        assert RUNBOOK.name in alert["description"], endpoint["name"]
        # Gatus refuses to start on a description holding " or \ (found running it, Issue 14).
        assert not set(alert["description"]) & {'"', "\\"}, endpoint["name"]


def test_an_outage_alerts_within_five_minutes() -> None:
    """Worst case, the outage starts just after a good check: `failure-threshold` intervals."""
    config = _config()
    _, provider = _provider(config)
    threshold = provider["default-alert"]["failure-threshold"]
    for endpoint in config["endpoints"]:
        worst_case = threshold * _seconds(endpoint["interval"])
        assert worst_case <= ALERT_WITHIN_SECONDS, (endpoint["name"], worst_case)


def test_one_incident_sends_one_alert_and_one_resolution() -> None:
    """A threshold above one (no flapping page for a single slow check), never a repeat."""
    config = _config()
    _, provider = _provider(config)
    default = provider["default-alert"]
    assert default["failure-threshold"] >= 2
    assert default["success-threshold"] >= 2
    assert default["send-on-resolved"] is True
    assert "repeat-interval" not in default
    for endpoint in config["endpoints"]:
        for alert in endpoint["alerts"]:
            assert "repeat-interval" not in alert, endpoint["name"]


def test_the_channel_and_the_hosts_come_from_the_environment() -> None:
    """A webhook URL is a credential: it is never written in the repository."""
    config = _config()
    _, provider = _provider(config)
    assert provider["webhook-url"] == "${TEAM_WEBHOOK_URL}"
    for endpoint in config["endpoints"]:
        assert endpoint["url"].startswith("${"), endpoint["name"]


def test_the_runbook_the_alerts_point_to_covers_them() -> None:
    """Each alert sends people to a section that exists and names the same roles."""
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "## Staging or production is down" in runbook
    assert "role E" in runbook and "role F" in runbook
