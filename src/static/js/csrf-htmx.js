/**
 * CSRF double-submit helper for the admin dashboard (Issue 9).
 *
 * The server sets a *readable* CSRF cookie on sign-in; every unsafe request to
 * ``/api/*`` must echo it back in an ``X-CSRF-Token`` header (see
 * ``src/core/csrf_middleware.py``). This module:
 *
 *   1. exposes ``window.BKP.csrfToken()`` so hand-written ``fetch`` calls can
 *      attach the token, and
 *   2. mirrors the cookie into ``X-CSRF-Token`` for every unsafe HTMX request.
 *
 * The cookie name is read from ``<meta name="bkp-csrf-cookie">`` in base.html,
 * so it stays in sync with ``settings.csrf_cookie_name``.
 *
 * Loaded before the page apps; external file only, to satisfy ``script-src 'self'``.
 */
(function () {
  "use strict";

  var meta = document.querySelector('meta[name="bkp-csrf-cookie"]');
  var CSRF_COOKIE = meta ? meta.getAttribute("content") || "" : "";

  /** Return the current CSRF token from the readable cookie, or "" when absent. */
  function csrfToken() {
    if (!CSRF_COOKIE) return "";
    var prefix = CSRF_COOKIE + "=";
    var parts = document.cookie.split(";");
    for (var i = 0; i < parts.length; i++) {
      var c = parts[i].trim();
      if (c.indexOf(prefix) === 0) {
        return decodeURIComponent(c.substring(prefix.length));
      }
    }
    return "";
  }

  var ns = (window.BKP = window.BKP || {});
  ns.csrfToken = csrfToken;

  /**
   * Build fetch headers for a JSON write, attaching the CSRF token when present.
   * @param {Object} [extra] additional headers to merge in.
   */
  ns.writeHeaders = function writeHeaders(extra) {
    var headers = { "Content-Type": "application/json" };
    var token = csrfToken();
    if (token) headers["X-CSRF-Token"] = token;
    if (extra) {
      for (var k in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, k)) headers[k] = extra[k];
      }
    }
    return headers;
  };

  // Attach the token to every unsafe HTMX request as well.
  document.addEventListener("htmx:configRequest", function (evt) {
    var token = csrfToken();
    if (token) evt.detail.headers["X-CSRF-Token"] = token;
  });
})();
