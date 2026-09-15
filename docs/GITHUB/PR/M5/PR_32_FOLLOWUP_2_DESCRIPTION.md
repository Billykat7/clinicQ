# PR: Let the discovery page ask for a position, so Use my location works (Issue 32 / M5-32 second follow-up)

**Milestone:** [Milestone 5: Discovery & Geolocation](https://github.com/Billykat7/clinicQ/milestone/5) ·
**Issue:** [#32](https://github.com/Billykat7/clinicQ/issues/32) (closed by its pull request; this is its second
follow-up)

**Use my location** on `/discover` never used the location. Pressing it, with location allowed in the browser,
went straight to "No problem, your location stays private. Type your suburb or township below instead." and
moved the cursor to the suburb search.

**The cause is a response header, not the page's script.** Every page sends
`Permissions-Policy: … geolocation=() …`. The empty list `()` forbids location to every origin, this one
included, so the browser answers `PERMISSION_DENIED` at once, without asking the patient. `discover.js` treats
that, correctly, as "the patient said no" and offers the suburb search.

## Scope

- **In:** `geolocation=(self)` on `/discover` only; a browser test that shows the bug and the fix; a header
  test.
- **Out:** every other page and every other feature in the policy, which stay as they are.

## Summary

- **`/discover` may ask for a position.** Its policy is the same as every page's, with `geolocation=()`
  changed to `geolocation=(self)`. The browser still asks the patient before sharing anything, and the page
  still rounds the position to three decimals.
- **Nowhere else is loosened.** The results fragment, clinic pages, the join page, `/t/`, the board and the
  dashboard keep `geolocation=()`.

## Design notes

- **The narrowest change that works.** The button exists only on `/discover` (`discover/list.html`), and the
  script calls `navigator.geolocation` only from there, so only that exact path needs the permission.
  Loosening the whole `/discover` prefix would also have covered pages that never ask.
- **The same shape as the board's exception** (Issue 60, `autoplay=(self)` under `/display`): the one policy
  string, with one feature changed for one path, chosen in `permissions_policy_for`.

## Changes

- **`src/core/security_headers.py`:** `DISCOVER_PATH` and `_DISCOVER_PERMISSIONS_POLICY`;
  `permissions_policy_for` returns it for `/discover`.
- **`tests/e2e/patient/test_discover_location.py`** (new): Chromium on a phone with location allowed presses
  **Use my location** and must reach `/discover?lat=…&lon=…`. A phone with location not allowed must still be
  offered the suburb search.
- **`tests/integration/platform/test_security_headers.py`:** `test_only_the_discovery_page_may_ask_for_a_position`.

## Testing

**Before the fix**, the new browser test on `main`'s headers. The page never leaves `/discover`:

```text
E       playwright._impl._errors.TimeoutError: Timeout 30000ms exceeded.
FAILED tests/e2e/patient/test_discover_location.py::test_use_my_location_lists_the_clinics_near_the_phone
```

**After** (`TZ=UTC`, PostgreSQL):

```text
tests/e2e/patient/test_discover_location.py ..                          2 passed in 5.47s
tests/integration/platform/test_security_headers.py .............      13 passed in 15.04s
```

The allowed phone lands on `/discover?lat=-33.927&lon=18.417`, the position rounded to three decimals. The
header test asserts:

- `/discover` carries `geolocation=(self)`, and still `camera=()`, `microphone=()`, `payment=()` and
  `autoplay=()`;
- `/`, `/discover/results`, a clinic page, the join page, `/t/`, `/display` and `/dashboard` keep
  `geolocation=()`;
- the served header on `/health/live` is unchanged.

`ruff check .` and `ruff format --check .` are clean.

**Not checked:** a real phone's browser. Chromium enforces the header the same way Chrome on Android does,
which is what the browser test relies on.

## Risk and rollback

- **One page may now ask for location**, which is what its button is for. The browser's own prompt still
  decides, and nothing is stored: the rounded position goes only into the page's address.
- **No migration, no setting.** Rollback is a revert, and the button falls back to the suburb search again.

Refs #32
