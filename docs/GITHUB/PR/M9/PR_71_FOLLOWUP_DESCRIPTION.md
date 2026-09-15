# PR: Fix what writing the notifications contract found (Issue 71 follow-up / M9-71)

**Milestone:** [Milestone 9: Notifications & Patient PWA](https://github.com/Billykat7/clinicQ/milestone/9) ·
**Issue:** [#71](https://github.com/Billykat7/clinicQ/issues/71), closed by PR #194 · **Follows:** PR #194's
"Found while writing the contract, not changed here"

Writing `contracts/notifications.yaml` for #71 turned up some problems in routes that were already merged.
PR #194 listed them as found but not fixed. This PR fixes them:

- **A bounced message was re-sent.** A bounce reported to the delivery webhook set the row to `failed`, which
  the retry sweep picks up, so the same message went out again to an address that had just bounced. That can
  count against the sender's reputation. A bounce now ends the row `dead` with its reason, and a late failure
  never undoes a delivery. SMS receipts already worked this way.
- **The SMS gateway callbacks answered errors differently from every other route.** Their `400`, `404` and
  `503` bodies had `detail` alone, with no `code` and no `request_id`. They now use the error envelope. A wrong
  callback secret is still indistinguishable from a path that does not exist, which is now tested.
- **Three contracts named the wrong session cookie.** The queue, sites and discovery contracts said
  `bk_clinicq_access`, but the cookie is `bk_clinicq_access_token`. A new test fails when any contract names a
  cookie the application does not set, a mistake the drift test cannot see.
- **The web push subscribe route's `200` was missing from the application's own OpenAPI** (a browser
  subscribing again). It is now declared on the route, matching the contract.

**Not changed:** an unconfigured delivery webhook still answers `404` while an unconfigured SMS callback answers
`503`. Both are existing, documented behaviour: the delivery webhook hides that it exists when off, and
`docs/OPS/SMS_GATEWAY.md` tells operators that the SMS callbacks answer `503` while `SMS_WEBHOOK_TOKEN` is
unset. Changing either would change what an operator or a provider sees, so it is left for a decision.

## Changes

- `src/modules/notifications/service.py`: `record_delivery_status` ends a bounce `dead` (with "delivery
  failed: <reason>" and no next attempt), and ignores any receipt for a row already `delivered`.
- `src/api/v1/routes/webhooks.py`: both SMS callbacks raise `HTTPException` for `400`, `404` and `503`, so
  the error handlers add `code` (`http.bad_request`, `http.not_found`, `http.service_unavailable`) and
  `request_id`. The payment webhooks are untouched.
- `src/modules/notifications/router.py`: the delivery webhook's docstring; `responses={200: …}` on
  `POST /web-push/subscriptions`.
- `contracts/notifications.yaml`: a bounce is `dead`; the SMS callback error examples carry `code` and
  `request_id`; the envelope note no longer excepts the SMS callbacks.
- `contracts/queue.yaml`, `sites.yaml`, `discovery.yaml`: `sessionCookie` is `bk_clinicq_access_token`.
- Tests:
  - `tests/unit/notifications/test_notifications.py`:
    `test_a_bounce_is_terminal_never_retried_and_never_undoes_a_delivery` (new);
  - `tests/integration/contracts/test_notifications_contract.py`: the six SMS callback cases now require
    their envelope codes, and `test_a_wrong_callback_secret_is_word_for_word_a_path_that_does_not_exist` is
    new;
  - `tests/integration/contracts/test_openapi_contracts.py`:
    `test_every_cookie_the_contract_names_is_one_the_application_sets`, run for every contract (new).

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (302 files) clean.
- [x] `TZ=UTC pytest tests/ -n auto` on the Docker PostgreSQL 18 and Redis, with browsers and
  `AWS_S3_LOGGING_ENABLED=false`: **2386 passed, 1 skipped, 9 xfailed, 0 failed**.
- [x] `pytest tests/integration/contracts tests/integration/notifications tests/unit/notifications`:
  **290 passed**. The contracts alone: **128 passed**.
- [x] The bounce test sends two messages, bounces the first, delivers the second, then sends a late bounce
  for the second:
  - the first is `dead` ("delivery failed: Mailbox full", no next attempt);
  - the second stays `delivered`;
  - the retry sweep sends nothing, and the provider saw two messages.
- [x] The callback test posts to both SMS callbacks with a forged secret and to a path that does not exist.
  Once the request id is removed, all three bodies are `{"detail": "Not Found", "code": "http.not_found"}`,
  and each carries a request id.

## Risk and rollback

- **A bounce is no longer retried.** A provider that reports a temporary failure through this webhook (a
  full mailbox that clears later) no longer gets a second try. The webhook has no way to say "temporary",
  and the SMS path already treats a failed receipt as final.
- **SMS gateway callback error bodies gain two fields.** Every status code is unchanged; only the JSON body
  of a refusal grows `code` and `request_id`.
- **No migration.** Rollback is a revert.

Refs #71
