"""The installable patient app: manifest, service worker, start page and offline page (Issue 69).

What the server promises the browser, read as JSON, headers and files (the browser half, installing, going
offline and picking up a deploy, is ``tests/e2e/patient/test_patient_app.py``):

* **the manifest makes an installable, standalone app** scoped to the ticket pages, with PNG icons of the
  sizes it claims (and a maskable one), in the brand's colours;
* **the service worker carries this release's version**, so a new deploy is a new worker, is never cached by
  the browser, and may control ``/t/`` and nothing wider;
* **the offline shell is complete and bounded**: every file the offline page loads is in the worker's fixed
  list, every listed file exists (a missing one would stop the worker installing), and nothing else is kept;
* **the app opens at the patient's open ticket**, and at a page that finds the last one otherwise;
* **installation is offered only to the patient who joined**, on their own open ticket, never on a shared
  link.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette import status

from src.core.config import Settings
from src.web import ticket as ticket_routes

_STATIC = Path(__file__).resolve().parents[3] / "src" / "static"
_BASE_TEMPLATE = _STATIC.parent / "templates" / "base.html"
_ASSET = re.compile(r'(?:src|href)="(/static/[^"?#]+)"')


def _join(desk: SimpleNamespace) -> tuple[TestClient, dict[str, Any]]:
    client, _patient_id = desk.patient()
    answer = client.post(desk.join_path(desk.triage), json={})
    assert answer.status_code == status.HTTP_201_CREATED, answer.text
    return client, answer.json()


def _page(client: TestClient, page_url: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/tickets/{page_url.removeprefix('/t/')}")
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


def test_the_manifest_describes_an_installable_standalone_app_for_the_ticket_pages(
    desk: SimpleNamespace,
) -> None:
    response = TestClient(desk.app).get("/static/manifest.json")
    assert response.status_code == status.HTTP_200_OK
    manifest = response.json()

    assert manifest["display"] == "standalone"
    assert manifest["scope"] == "/t/" and manifest["id"] == "/t/"
    assert manifest["start_url"].startswith(manifest["scope"]), (
        "the app must open inside its scope"
    )
    assert manifest["name"] == Settings.model_fields["app_name"].default
    assert 0 < len(manifest["short_name"]) <= 12, (
        "a home-screen label is cut after about 12 characters"
    )
    theme = re.search(
        r'name="theme-color" content="([^"]+)"', _BASE_TEMPLATE.read_text()
    )
    assert theme and manifest["theme_color"] == theme.group(1)

    purposes: dict[str, set[str]] = {}
    for icon in manifest["icons"]:
        path = _STATIC / icon["src"].removeprefix("/static/")
        width, height = (int(n) for n in icon["sizes"].split("x"))
        with Image.open(path) as image:
            assert (image.format, image.size) == ("PNG", (width, height)), icon["src"]
        purposes.setdefault(icon["purpose"], set()).add(icon["sizes"])
    assert {"192x192", "512x512"} <= purposes["any"]
    assert "512x512" in purposes["maskable"]
    with Image.open(_STATIC / "icons" / "apple-touch-icon.png") as touch:
        assert touch.size == (180, 180)


def test_the_worker_carries_the_release_version_is_never_cached_and_stays_in_its_scope(
    desk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(desk.app)
    first = client.get("/patient-sw.js")
    assert first.status_code == status.HTTP_200_OK
    assert first.headers["content-type"].startswith("text/javascript")
    assert first.headers["cache-control"] == "no-cache"
    assert first.headers["service-worker-allowed"] == "/t/"
    body = first.text
    assert "__CLINICQ_PATIENT_" not in body, "a mark was left unfilled"
    assert f'var VERSION = "{ticket_routes.worker_version()}";' in body
    shell = json.loads(re.search(r"var SHELL = (\[.*?\]);", body).group(1))  # type: ignore[union-attr]
    assert shell == list(ticket_routes.PATIENT_SHELL)
    # It opens and deletes only its own caches, never the board's (Issue 62).
    assert 'var PREFIX = "clinicq-patient-";' in body and "clinicq-board" not in body

    monkeypatch.setattr(ticket_routes, "worker_version", lambda: "0.9.0-next-deploy")
    second = client.get("/patient-sw.js").text
    assert second != body and 'var VERSION = "0.9.0-next-deploy";' in second


def test_the_offline_shell_lists_everything_the_offline_page_needs_and_nothing_is_missing(
    desk: SimpleNamespace,
) -> None:
    client = TestClient(desk.app)
    offline = client.get(ticket_routes.OFFLINE_PATH)
    assert offline.status_code == status.HTTP_200_OK
    wanted = set(_ASSET.findall(offline.text))
    assert wanted, "the offline page loads its styles and scripts"
    shell = set(ticket_routes.PATIENT_SHELL)
    assert wanted <= shell, (
        f"the offline page needs files the worker does not keep: {wanted - shell}"
    )

    for url in ticket_routes.PATIENT_SHELL:
        assert client.get(url).status_code == status.HTTP_200_OK, (
            f"{url} is missing: the worker would not install"
        )
    assert len(shell) == len(ticket_routes.PATIENT_SHELL) <= 20, (
        "the shell is a short fixed list: its size is the cache's size"
    )


def test_the_app_opens_at_the_patients_open_ticket_and_otherwise_at_the_finder(
    desk: SimpleNamespace,
) -> None:
    owner, answer = _join(desk)
    opened = owner.get("/t/?source=home-screen", follow_redirects=False)
    assert opened.status_code == status.HTTP_303_SEE_OTHER
    assert opened.headers["location"] == answer["page_url"]
    assert opened.headers["cache-control"] == "no-store"

    anonymous = TestClient(desk.app).get("/t/", follow_redirects=False)
    assert anonymous.status_code == status.HTTP_200_OK
    assert anonymous.headers["cache-control"] == "no-store"

    cancel_url = _page(owner, answer["page_url"])["cancel_url"]
    assert owner.post(cancel_url, json={}).status_code == status.HTTP_200_OK
    assert owner.get("/t/", follow_redirects=False).status_code == status.HTTP_200_OK, (
        "an ended ticket is not where the app opens"
    )


def test_installation_is_offered_only_to_the_patient_who_joined_while_the_ticket_is_open(
    desk: SimpleNamespace,
) -> None:
    owner, answer = _join(desk)
    other_patient, _ = _join(desk)
    page_url = answer["page_url"]

    assert _page(owner, page_url)["offer_install"] is True
    assert _page(TestClient(desk.app), page_url)["offer_install"] is False
    assert _page(other_patient, page_url)["offer_install"] is False

    cancel_url = _page(owner, page_url)["cancel_url"]
    owner.post(cancel_url, json={})
    assert _page(owner, page_url)["offer_install"] is False
