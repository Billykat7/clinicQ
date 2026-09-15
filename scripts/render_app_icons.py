"""Draw the patient app's home-screen icons from the brand mark (Issue 69).

The mark is ``src/static/favicon.svg``: the discovery lens, white on the brand green. Browsers and Lighthouse
want PNGs at fixed sizes, so this draws the same shapes with Pillow, four times larger and scaled down for
smooth edges, and writes them to ``src/static/icons/``:

* ``app-192.png`` and ``app-512.png``: the rounded square, as the favicon;
* ``app-maskable-512.png``: a full-bleed square with the lens inside the central safe zone (80%), for Android
  launchers that cut their own shape;
* ``apple-touch-icon.png`` (180): full-bleed, because iOS rounds the corners itself.

Run ``python -m scripts.render_app_icons`` after changing the mark, and commit the files.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

BRAND = "#0f6e56"
MARK = "#ffffff"
OUT = Path(__file__).resolve().parents[1] / "src" / "static" / "icons"
_SUPERSAMPLE = 4


def draw(size: int, *, rounded: bool, safe_zone: float = 1.0) -> Image.Image:
    """The mark at ``size`` pixels; ``safe_zone`` shrinks the lens toward the centre (maskable icons)."""
    big = size * _SUPERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    unit = big / 32
    if rounded:
        pen.rounded_rectangle((0, 0, big - 1, big - 1), radius=8 * unit, fill=BRAND)
    else:
        pen.rectangle((0, 0, big, big), fill=BRAND)

    # The favicon's geometry, on its 32-unit grid, scaled about the centre by ``safe_zone``.
    def at(value: float) -> float:
        return (16 + (value - 16) * safe_zone) * unit

    stroke = 2.6 * unit * safe_zone
    cx, cy, r = at(14), at(14), 6.5 * unit * safe_zone
    pen.ellipse(
        (
            cx - r - stroke / 2,
            cy - r - stroke / 2,
            cx + r + stroke / 2,
            cy + r + stroke / 2,
        ),
        fill=MARK,
    )
    inner = r - stroke / 2
    pen.ellipse((cx - inner, cy - inner, cx + inner, cy + inner), fill=BRAND)
    start, end = (at(19.2), at(19.2)), (at(24), at(24))
    pen.line((start, end), fill=MARK, width=round(stroke))
    for x, y in (start, end):
        pen.ellipse(
            (x - stroke / 2, y - stroke / 2, x + stroke / 2, y + stroke / 2), fill=MARK
        )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    draw(192, rounded=True).save(OUT / "app-192.png", optimize=True)
    draw(512, rounded=True).save(OUT / "app-512.png", optimize=True)
    draw(512, rounded=False, safe_zone=0.8).save(
        OUT / "app-maskable-512.png", optimize=True
    )
    draw(180, rounded=False, safe_zone=0.85).save(
        OUT / "apple-touch-icon.png", optimize=True
    )
    print(f"wrote 4 icons to {OUT}")


if __name__ == "__main__":
    main()
