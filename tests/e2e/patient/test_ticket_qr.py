"""The QR on the ticket page is readable by a real QR reader (Issue 70).

The unit tests prove the drawn modules are the QR symbol for the code. This proves the drawing, as the page
renders it, scans: the page's SVG is rasterised in the browser and read with Chromium's own QR detector
(``BarcodeDetector``, backed by the operating system's reader), which must return exactly the payload the
page data names. ``BarcodeDetector`` exists in Chromium on macOS, Android and ChromeOS but not on Linux, so
the reading is skipped where the browser has no reader; the page's QR block and its sizing are checked
everywhere.

Set ``TICKET_PAGE_SHOTS`` to a folder to save the screenshot quoted in the pull request.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.postgres

SHOTS = os.environ.get("TICKET_PAGE_SHOTS", "")

_READ = """async () => {
    const svg = document.getElementById('tk-qr');
    const box = svg.getBoundingClientRect();
    const state = JSON.parse(document.getElementById('tk-state').textContent);
    const shown = { payload: state.reference_qr.payload, width: box.width, height: box.height };
    if (typeof BarcodeDetector === 'undefined') return { ...shown, reader: false };
    const image = new Image();
    image.src = 'data:image/svg+xml;base64,' + btoa(new XMLSerializer().serializeToString(svg));
    await image.decode();
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 290;
    const pen = canvas.getContext('2d');
    pen.imageSmoothingEnabled = false;
    pen.drawImage(image, 0, 0, 290, 290);
    const found = await new BarcodeDetector({ formats: ['qr_code'] }).detect(canvas);
    return { ...shown, reader: true, read: found.map((code) => code.rawValue) };
}"""


def test_the_ticket_pages_qr_reads_back_as_its_code(
    patient_day: SimpleNamespace,
) -> None:
    mine = patient_day.ticket(ahead=1)
    page = patient_day.follower_page(width=360, height=1400)
    page.goto(mine.path)
    started = time.monotonic()
    while (
        page.evaluate("() => document.getElementById('tk-live').textContent") != "Live"
    ):
        assert time.monotonic() - started < 20
        time.sleep(0.1)

    result = page.evaluate(_READ)
    assert result["payload"].startswith("CLINICQ:")
    assert result["width"] == result["height"] >= 180, (
        "large enough for a desk scanner at arm's length"
    )
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.locator(".tk-code").screenshot(path=str(Path(SHOTS) / "ticket-code.png"))
    if not result["reader"]:
        pytest.skip(
            "this Chromium has no BarcodeDetector (Linux); the modules are checked in the unit tests"
        )
    assert result["read"] == [result["payload"]]
