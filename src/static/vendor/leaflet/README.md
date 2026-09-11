# Leaflet (vendored)

**Leaflet 1.9.4** — an open-source JavaScript library for interactive maps.

| | |
|---|---|
| Upstream | <https://leafletjs.com> |
| Source | `https://unpkg.com/leaflet@1.9.4/dist/…` |
| Licence | BSD-2-Clause (see the banner at the top of `leaflet.js`) |
| Vendored for | Issue #129 — the property form's address search and map |

## Why it is vendored rather than loaded from a CDN

The app's Content-Security-Policy is `script-src 'self'`. Vendoring keeps the map inside that
policy with **no directive change**: the library loads from this origin, the tiles are images
already covered by `img-src https:`, and the address search goes to our own API (which proxies the
geocoding provider server-side). A CDN would mean widening the policy and taking a runtime
dependency on a third party being up.

## Files

| Path | Purpose |
|---|---|
| `leaflet.js` | The library (minified, as released). |
| `leaflet.css` | Its stylesheet. |
| `images/marker-icon.png`, `images/marker-icon-2x.png`, `images/marker-shadow.png` | The default marker. Leaflet resolves these **relative to `leaflet.css`**, so they must stay in `images/` beside it. |
| `images/layers.png`, `images/layers-2x.png` | The layer-control icon. |

`leaflet.js` checksum (SHA-256): `db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a`

## Updating

Re-fetch every file from the same `dist/` path at the new version, update the version and checksum
above, and re-check the property form (search → pick a result → pin appears; drag the pin).

```bash
curl -fSO https://unpkg.com/leaflet@<version>/dist/leaflet.js
```

Do **not** hand-edit these files. They are stored verbatim — `.gitattributes` disables line-ending
normalisation here and the pre-commit hygiene hooks skip this directory — so each one still matches
its upstream artifact byte-for-byte.
