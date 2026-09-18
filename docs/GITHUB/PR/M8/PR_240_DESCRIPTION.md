# PR: The claim address, reachable at last (Issue 240 / M8-240)

**Milestone:** [Milestone 8: Waiting-room Display Monitor](https://github.com/Billykat7/clinicQ/milestone/8) ·
**Issue:** [#240](https://github.com/Billykat7/clinicQ/issues/240) · **Follows:** #238 (Issue 237,
Smart TV casting), merged

Issue 237 shipped this route, and this line in its own acceptance criteria:

> - [x] A television with a browser but no Chromecast can be paired by opening one link

`docs/OPS/SMART_TV_SETUP.md` documents the address:

```
https://clinicq.example/display/claim?code=XXXXXX
```

**Nothing could produce one.** Every claim code that has ever existed was minted inside the cast
path and handed to a screen over a Cast connection. It was never returned to anybody, and there was
no second way to mint one. The route worked; the documented path into it did not exist.

## Who that stranded

| | why it had no way in |
|---|---|
| A television that browses but will not be cast to | the exact case the route was written for |
| A deployment with no `CAST_RECEIVER_APP_ID` | the cast reports *reached, no ClinicQ page to open* — and the row it held was then stranded, with a live code nobody could see |
| **Anyone trying the board out with no television** | **Send the board** only appears once a Chromecast has been discovered, so on a laptop no code could be minted by any route at all |

The third one is why "just show the code on the cast failure" would not have been enough: on
`localhost` you never get as far as a cast.

## What this adds

**Clinic settings → Display boards → Open the board on the screen itself.** Choose what the device
is, name it, press **Make an address**. The card shows the whole address, the code inside it, how
long it has left, and a copy button.

It contacts nothing and searches no network, so it renders on **every** deployment — including one
with `SMART_TV_DISCOVERY_ENABLED=false`, where **Screens on this network** is deliberately hidden
because a cloud instance can never find a clinic's television. That deployment is the one that needs
this most.

`POST /sites/{id}/display-devices/claim-link` is the endpoint. Same `sites.display` update grant
that already lets a manager pair a screen by typing a code, same audit row. The code comes back to
the manager who asked for it, because a code nobody can read is a code nobody can use.

Separately, a cast that was **reached but did not show the board** now offers the same address under
the failure, using the row it is already holding. When the board *is* showing, `claim_url` and
`claim_code` come back empty — the screen has spent the code, and repeating it would only widen its
reach for nothing.

## Verification

Driven in a browser against a seeded local database (26 clinics, signed in as `manager@clinicq.example`).

**Making one, and spending it in a second browser window:**

```
POST /api/v1/sites/01a0b393-…/display-devices/claim-link → 201 Created
  claim_url: http://localhost:8063/display/claim?code=UJFVGX
  claim_code: UJFVGX
  message:   "Address ready. It works for 10 minutes."
```

Opening that address in a **second tab** left it showing *Hillbrow Community Health Centre — Triage /
General consultation / Chronic medication collection / Immunisation*, with *"Listen for your number
and watch this screen."* The dashboard's screen list then read:

```
Unnamed screen · Waiting-room board · Every open queue · Showing the board · 18 Sep 10:15
```

**Single use**, with a fresh client that holds no cookies:

```
$ curl -o /dev/null -w "%{http_code} %{redirect_url}" ".../display/claim?code=UJFVGX"
302 http://localhost:8063/display          # spent
$ curl -o /dev/null -w "%{http_code} %{redirect_url}" ".../display/claim?code=ZZZZZZ"
302 http://localhost:8063/display          # never existed — the same answer, on purpose
```

**In the database and the audit log** (the second row is a later address, still waiting):

```
{'label': None,                 'kind': 'board', 'paired': True,  'has_live_code': False}
{'label': 'TV in the corridor', 'kind': 'board', 'paired': False, 'has_live_code': True}
--- audit ---
  link made for a display board, to be opened on the screen itself
  link made for a display board, to be opened on the screen itself
```

**The suites**, including the contract, which gained the path and a `DisplayDeviceKind` schema it
had never had:

```
$ TZ=UTC pytest tests/integration/display
50 passed, 1 skipped

$ TZ=UTC pytest tests/integration/contracts
143 passed

$ TZ=UTC pytest tests/unit tests/integration
2529 passed, 183 skipped, 9 xfailed
```

**What looking at the page caught.** A line I had written told the reader to *"go to `/display/claim`
and enter"* the code — and there is no form to enter it in; the code travels *in* the address. The
page now says `/display/claim?code=` followed by the code, and a test asserts that string is on the
page so the wrong wording cannot come back.

**Not tested:** an actual television. No Chromecast and no Smart TV were involved — the cast path is
stubbed here exactly as Issue 237 stubbed it, and for the same reason. What is proven is the claim
handover, which is what this PR changes.

## Acceptance criteria

- [x] **A claim address with no television, no Chromecast and no discovery.** Shown above, on a
      laptop, against `localhost`.
- [x] **The card renders where `SMART_TV_DISCOVERY_ENABLED` is false.** Asserted in
      `test_the_page_offers_the_address_even_where_it_cannot_search`, which pins the setting off and
      checks the claim card is present and `screen-finder` is not.
- [x] **Address, code, time left, copy button.** Screenshot evidence in the browser run above.
- [x] **Opening it in another browser makes that window the board.** Second tab, above.
- [x] **Single use.** Both the spent code and an unknown code land on `/display`, above.
- [x] **A reached-but-not-showing cast offers the address.** `test_a_cast_that_did_not_show_the_board_hands_back_the_address`,
      which also opens the returned URL and confirms it claims *the row that was held*, not a new one.
- [x] **A showing cast returns no code.** `test_a_cast_that_worked_hands_back_nothing_to_spend`.
- [x] **Needs the pairing grant, and is audited.** The front desk gets 403, clinic B gets 403/404,
      and the audit row is asserted by entity id.
- [x] **No implied form.** Corrected, and asserted.

## Risk and rollback

**The thing worth a reviewer's judgement:** this puts a credential on screen. For the ten minutes it
lives, whoever holds the address can become one of the clinic's screens.

The argument that it is acceptable: the manager reading it is exactly the person the `sites.display`
update grant already lets pair a screen by typing a code off a wall; it is single-use; it expires;
it is never written to the URL, to `localStorage`, or to a log; the result block is hidden until it
is asked for; and the page says plainly that opening it there claims the row for that browser.

The argument to weigh against it: a code that used to exist only on the clinic's own network now
also appears in a dashboard that may be on a shared screen, and a shoulder-surfer in the office has
ten minutes. If that trade is not wanted, the narrower version is to gate the card behind
`SMART_TV_DISCOVERY_ENABLED` — which would cost exactly the cloud and laptop cases this PR exists
for — or behind a re-authentication.

Rolling back is `git revert` of the merge. Nothing is migrated and no stored data changes shape; a
screen already paired this way keeps working, because it holds an ordinary device secret like any
other. Any unspent address simply expires.

Closes #240

Refs #237, which shipped the route this makes reachable.
