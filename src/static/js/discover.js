/**
 * Find a clinic (Issue 32): the location prompt, and the list's error state.
 *
 * Two jobs, and nothing else; the list itself is server-rendered and swapped by htmx.
 *
 *   1. The location prompt. The browser is asked for a position only when the patient presses
 *      "Use my location". A fix sends them to /discover with the position rounded to three decimal
 *      places (about 100 m). A refusal, a timeout or a browser that cannot locate is never a dead
 *      end: the sentence says why, and the focus moves to the suburb search, which works without a
 *      position at all.
 *   2. The error state. When a swap fails (a server error, or no connection) the last good list
 *      stays, an error panel with "Try again" appears, and window.BKP.toast (ui-feedback.js)
 *      announces it. The skeleton while a swap is in flight is the server's component, shown by
 *      htmx's indicator class.
 *
 * External file, no inline handlers: the CSP allows script-src 'self' only.
 */
(function () {
  "use strict";

  var DECIMALS = 3;
  var GEOLOCATION_OPTIONS = { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 };

  var button = document.getElementById("locate-button");
  var status = document.getElementById("locate-status");
  var areaInput = document.getElementById("area-q");

  function say(text) {
    if (!status) return;
    status.textContent = text;
    status.hidden = false;
  }

  /** The decline path: explain, then hand the patient to the suburb search. */
  function offerAreaSearch(reason) {
    say(reason + " Type your suburb or township below instead.");
    if (areaInput) {
      areaInput.focus();
      areaInput.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  function round(value) {
    var factor = Math.pow(10, DECIMALS);
    return Math.round(value * factor) / factor;
  }

  if (button) {
    if (!("geolocation" in navigator)) {
      say("This browser cannot share a location.");
    } else {
      button.hidden = false;
      button.addEventListener("click", function () {
        button.disabled = true;
        say("Finding clinics near you…");
        navigator.geolocation.getCurrentPosition(
          function (position) {
            var params = new URLSearchParams({
              lat: String(round(position.coords.latitude)),
              lon: String(round(position.coords.longitude))
            });
            window.location.assign("/discover?" + params.toString());
          },
          function (error) {
            button.disabled = false;
            var reason = "We could not find your location.";
            if (error && error.code === 1) reason = "No problem, your location stays private.";
            else if (error && error.code === 3) reason = "Finding your location took too long.";
            offerAreaSearch(reason);
          },
          GEOLOCATION_OPTIONS
        );
      });
    }
  }

  var filters = document.getElementById("filters");
  var results = document.getElementById("results");
  var errorPanel = document.getElementById("results-error");
  var retry = document.getElementById("results-retry");

  if (!filters || !results || !errorPanel) return;

  function failed(message) {
    errorPanel.hidden = false;
    results.setAttribute("aria-busy", "false");
    if (window.BKP && typeof window.BKP.toast === "function") {
      window.BKP.toast(message, { kind: "error" });
    }
  }

  document.body.addEventListener("htmx:beforeRequest", function (event) {
    if (event.detail.target === results) results.setAttribute("aria-busy", "true");
  });
  document.body.addEventListener("htmx:afterSwap", function (event) {
    if (event.detail.target === results) {
      errorPanel.hidden = true;
      results.setAttribute("aria-busy", "false");
    }
  });
  document.body.addEventListener("htmx:responseError", function (event) {
    if (event.detail.target === results) failed("The list could not be updated. Try again.");
  });
  document.body.addEventListener("htmx:sendError", function (event) {
    if (event.detail.target === results) failed("No connection. The list shown is the last one that loaded.");
  });
  if (retry) {
    retry.addEventListener("click", function () {
      errorPanel.hidden = true;
      if (window.htmx) window.htmx.trigger(filters, "change");
    });
  }
})();
