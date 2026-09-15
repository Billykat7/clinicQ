"""The discovery page's "Use my location", in a real browser (Issue 32 follow-up).

The page asks the browser for a position and lists the clinics near it. Every page used to send
``Permissions-Policy: geolocation=()``, which forbids location even to this origin, so the browser refused at
once and the page fell back to the suburb search, as if the patient had said no. Chromium enforces the policy
header, so a phone that has allowed location is the proof:

* **with location allowed, the page goes to the clinics near that position**, rounded to three decimals;
* **with location refused, it still offers the suburb search**, as before.
"""

from __future__ import annotations

import re
import time
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.postgres

#: Cape Town's Company's Garden: any position works, the page only has to be allowed to read it.
_POSITION = {"latitude": -33.927434, "longitude": 18.417284}


def _until(page: Any, predicate: str, timeout: float = 15.0) -> None:
    started = time.monotonic()
    while not page.evaluate(predicate):
        assert time.monotonic() - started < timeout, (
            f"timed out waiting for {predicate}"
        )
        time.sleep(0.05)


def test_use_my_location_lists_the_clinics_near_the_phone(
    patient_day: SimpleNamespace,
) -> None:
    page = patient_day.follower_page(geolocation=_POSITION, permissions=["geolocation"])
    page.goto("/discover")
    page.get_by_role("button", name="Use my location").click()
    page.wait_for_url(re.compile(r".*/discover\?lat=-33\.927&lon=18\.417$"))


def test_a_refused_location_still_offers_the_suburb_search(
    patient_day: SimpleNamespace,
) -> None:
    page = patient_day.follower_page()  # no permission granted: the browser refuses
    page.goto("/discover")
    page.get_by_role("button", name="Use my location").click()
    _until(
        page,
        "() => document.activeElement && document.activeElement.id === 'area-q'",
    )
    assert page.url.endswith("/discover")
