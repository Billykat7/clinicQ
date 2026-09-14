"""The shell bell is offered exactly where its badge poll is allowed (Refs #48).

Every dashboard page included the bell (``layouts/dashboard.html``), and its script polls
``GET /api/v1/notifications/center/unread-count``, gated on ``communications.notifications`` READ.
No clinic role held that grant, so every signed-in page a receptionist, nurse or clinic manager
opened answered the poll with 403. The operator got the same.

A grant alone cannot fix the clinic roles. They hold their role **at a site**, and the centre routes
name no site, so a site-held role never counts there. The layout now renders the bell and its script
only when the page context's ``show_notification_bell`` is set, and the operator, whose role is unscoped,
holds that grant at ``own``.

What this file proves, from the page context the template renders with and the route's JSON answer
(``docs/IDE/RULES/testing-strategy.mdc``):

* The gate a page renders the bell behind agrees with the route the bell polls, for every person in
  the fixture, on a clinic page and on an account page. A bell is never offered to be refused.
* A clinic manager opening a dashboard page is not offered the bell.
* The operator is offered the bell, and its count is first-person.
* The operator's ``own`` grant does not open the whole-platform delivery viewer under the same prefix.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette import status

from src.commons.enums import NotificationCategory
from src.database.models import InAppNotification

UNREAD_COUNT = "/api/v1/notifications/center/unread-count"
PEOPLE = ("desk.a", "nurse.a", "manager.a", "desk.b", "both", "trainee", "operator")


def _offers_bell(response) -> bool:
    """Whether the server rendered the page with the flag the layout shows the bell behind."""
    assert response.status_code == status.HTTP_200_OK, response.text[:200]
    return response.context["show_notification_bell"]


def _follow(client, path: str):
    """GET ``path`` and follow local redirects, returning the final response."""
    response = client.get(path)
    for _ in range(5):
        if response.status_code != status.HTTP_302_FOUND:
            return response
        response = client.get(response.headers["location"])
    raise AssertionError("too many redirects")


def _add_notification(dashboard: SimpleNamespace, person: str) -> None:
    """Record one unread in-app notification for ``person``."""
    with dashboard.session() as db:
        db.add(
            InAppNotification(
                user_id=dashboard.ids[person],
                category=NotificationCategory.MAINTENANCE.value,
                title="Scheduled maintenance",
                body="The dashboard is unavailable from 22:00 to 22:30.",
            )
        )
        db.commit()


@pytest.mark.parametrize("person", PEOPLE)
def test_the_bell_is_offered_exactly_when_its_poll_is_allowed(
    dashboard: SimpleNamespace, person: str
) -> None:
    """On the landing page and the profile page, the bell's gate and its route give one answer."""
    client = dashboard.client(person)
    allowed = client.get(UNREAD_COUNT).status_code == status.HTTP_200_OK

    assert _offers_bell(_follow(client, "/dashboard")) is allowed
    assert _offers_bell(client.get("/account/profile")) is allowed


def test_a_clinic_manager_is_not_offered_the_bell(dashboard: SimpleNamespace) -> None:
    """The manager's clinic page carries no bell, so nothing polls a route that refuses them."""
    client = dashboard.client("manager.a")

    assert (
        _offers_bell(_follow(client, dashboard.page(dashboard.site_a, "settings")))
        is False
    )
    assert client.get(UNREAD_COUNT).status_code == status.HTTP_403_FORBIDDEN


def test_the_operator_is_offered_its_own_count(dashboard: SimpleNamespace) -> None:
    """The operator's bell opens, and a notification for someone else does not badge it."""
    _add_notification(dashboard, "operator")
    _add_notification(dashboard, "manager.a")
    client = dashboard.client("operator")

    assert _offers_bell(_follow(client, "/dashboard")) is True
    response = client.get(UNREAD_COUNT)
    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json() == {"unread_total": 1}


def test_the_operator_grant_does_not_open_the_delivery_viewer(
    dashboard: SimpleNamespace,
) -> None:
    """The whole-platform delivery log under the same prefix still refuses the ``own`` grant."""
    response = dashboard.client("operator").get("/api/v1/notifications")

    assert response.status_code == status.HTTP_403_FORBIDDEN
