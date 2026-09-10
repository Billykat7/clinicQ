/**
 * Silent access-token refresh (Issue 100).
 *
 * The access token is a short-lived JWT (JWT_ACCESS_EXPIRE_MINUTES, ~15 min) in an
 * httpOnly cookie; a longer-lived refresh cookie backs it. Rather than bounce an active
 * user to sign-in the moment the access cookie expires, this module wraps ``window.fetch``
 * so that a 401 from a guarded ``/api/*`` call transparently triggers
 * ``POST /api/v1/auth/refresh`` (which rotates the refresh token and mints a fresh access
 * cookie) and then replays the original request once — no visible logout.
 *
 * Design notes:
 *   - **Single-flight.** Concurrent 401s share one in-flight refresh so we never fire a
 *     storm of refresh calls (which would trip rotation reuse-detection and kill the
 *     session). Callers all await the same promise.
 *   - **Fail closed, quietly.** If the refresh itself returns 401 (refresh cookie missing,
 *     expired, revoked, or past the idle/absolute cap) the session is genuinely gone: we
 *     stop intercepting and surface the original 401 so the page's own "please sign in"
 *     handling runs. A network error does *not* mark the session dead, so a transient blip
 *     can still recover on the next call.
 *   - **CSRF on replay.** ``/auth/refresh`` rotates the readable CSRF cookie, so a replayed
 *     write must carry the *new* token. We re-read it from the cookie when replaying.
 *   - **Scope.** Only same-origin ``/api/*`` requests are guarded, excluding the sign-in /
 *     refresh / logout endpoints (a 401 there is a real credential result, not an expired
 *     access token) — ``/auth/me`` and ``/auth/me/*`` stay guarded.
 *
 * External file, no inline handlers: satisfies ``script-src 'self'``. Loaded early (before
 * the page apps) so ``window.fetch`` is wrapped before any request fires.
 */
(function () {
  "use strict";

  if (typeof window.fetch !== "function") return;

  var AUTH = "/api/v1/auth";
  var REFRESH_PATH = AUTH + "/refresh";
  var CSRF_HEADER = "X-CSRF-Token";
  var RETRY_FLAG = "__bkpRefreshRetried";

  var originalFetch = window.fetch.bind(window);

  // Shared refresh promise (single-flight) and a latch that stops us hammering a dead
  // session with refresh calls once one has definitively failed.
  var refreshInFlight = null;
  var sessionDead = false;

  /** Current CSRF token from the readable cookie (via csrf-htmx.js), or "" when absent. */
  function csrfToken() {
    return window.BKP && typeof window.BKP.csrfToken === "function"
      ? window.BKP.csrfToken()
      : "";
  }

  /** Resolve the request target to a URL, or null when it cannot be parsed. */
  function requestUrl(input) {
    try {
      if (typeof input === "string") return new URL(input, window.location.origin);
      if (typeof Request !== "undefined" && input instanceof Request) {
        return new URL(input.url, window.location.origin);
      }
      if (typeof URL !== "undefined" && input instanceof URL) return input;
      return new URL(String(input), window.location.origin);
    } catch (err) {
      return null;
    }
  }

  /**
   * The sign-in / session-bootstrap endpoints: a 401/4xx there is a real result (bad
   * code, wrong password, no refresh cookie), never an expired-access situation, so we
   * must not try to silently refresh them. ``/auth/me`` and ``/auth/me/*`` are *not*
   * bootstrap endpoints and stay guarded.
   */
  function isAuthBootstrap(path) {
    return path.indexOf(AUTH + "/") === 0 && path.indexOf(AUTH + "/me") !== 0;
  }

  /** True when a request is eligible for silent refresh + replay on a 401. */
  function isGuarded(url) {
    if (url.origin !== window.location.origin) return false;
    if (url.pathname.indexOf("/api/") !== 0) return false;
    return !isAuthBootstrap(url.pathname);
  }

  /** Copy headers from an object / array / Headers source into a Headers instance. */
  function copyHeadersInto(target, source) {
    if (!source) return;
    if (typeof Headers !== "undefined" && source instanceof Headers) {
      source.forEach(function (value, key) { target.set(key, value); });
    } else if (Array.isArray(source)) {
      source.forEach(function (pair) {
        if (pair && pair.length === 2) target.set(pair[0], pair[1]);
      });
    } else {
      Object.keys(source).forEach(function (key) { target.set(key, source[key]); });
    }
  }

  /**
   * Build the replay init: copy the original init, tag it so the replay is never itself
   * retried, and refresh the CSRF header (rotated by the refresh call) when the original
   * request carried one.
   */
  function replayInit(input, init) {
    var next = {};
    if (init) {
      Object.keys(init).forEach(function (key) { next[key] = init[key]; });
    }
    next[RETRY_FLAG] = true;

    var headers = new Headers();
    if (init && init.headers) {
      copyHeadersInto(headers, init.headers);
    } else if (typeof Request !== "undefined" && input instanceof Request) {
      copyHeadersInto(headers, input.headers);
    }
    if (headers.has(CSRF_HEADER)) {
      var token = csrfToken();
      if (token) headers.set(CSRF_HEADER, token);
      else headers.delete(CSRF_HEADER);
    }
    next.headers = headers;
    return next;
  }

  /**
   * Run one refresh (or await the in-flight one). Resolves true when a fresh access
   * cookie was issued, false otherwise. A 401 marks the session dead so we stop trying;
   * a network error leaves it recoverable.
   */
  function refreshOnce() {
    if (sessionDead) return Promise.resolve(false);
    if (refreshInFlight) return refreshInFlight;

    var headers = {};
    var token = csrfToken();
    if (token) headers[CSRF_HEADER] = token;

    refreshInFlight = originalFetch(REFRESH_PATH, {
      method: "POST",
      headers: headers,
      credentials: "same-origin",
    })
      .then(function (res) {
        refreshInFlight = null;
        if (res.ok) return true;
        // 401/403 etc.: the refresh cookie is gone/expired/revoked — stop intercepting.
        sessionDead = true;
        return false;
      })
      .catch(function () {
        refreshInFlight = null;
        return false; // transient: leave the session recoverable for the next call.
      });
    return refreshInFlight;
  }

  window.fetch = function (input, init) {
    var url = requestUrl(input);
    if (!url || !isGuarded(url) || (init && init[RETRY_FLAG])) {
      return originalFetch.apply(this, arguments);
    }
    return originalFetch(input, init).then(function (res) {
      if (res.status !== 401) return res;
      return refreshOnce().then(function (refreshed) {
        return refreshed ? originalFetch(input, replayInit(input, init)) : res;
      });
    });
  };
})();
