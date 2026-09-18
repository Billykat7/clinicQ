/**
 * The Cast receiver ClinicQ registers with Google (Issue 237).
 *
 * This is what a Chromecast actually runs. A Chromecast is not a browser you can point at a URL: it
 * loads the page registered against an application id and nothing else, so this page is that one
 * page, and everything about *which clinic* arrives afterwards, over the Cast connection, as a
 * message from `src/modules/display/casting.py`.
 *
 * What it does, in order:
 *
 *   1. waits for the dashboard's message on the ClinicQ namespace — it carries a one-time claim code;
 *   2. spends that code at `/display/claim`, which answers with this screen's board address and, in
 *      the same response, sets the httpOnly device cookie every kiosk box keeps;
 *   3. shows the board in a full-screen frame, same-origin, so the receiver itself stays loaded.
 *
 * **Why a frame and not a redirect.** Navigating away would unload the receiver SDK, and a receiver
 * that is no longer running is one the Cast session drops — the screen goes back to its home screen
 * a minute later. Keeping this page alive around the board, with the idle timeout switched off, is
 * what makes the board stay up all day. The frame is same-origin, so `frame-ancestors 'self'` is
 * satisfied and the board behaves exactly as it does on a Raspberry Pi.
 *
 * External file only, to satisfy `script-src 'self'` — the receiver SDK itself is the single
 * third-party script this one page's policy admits (see `src/core/security_headers.py`).
 */
(function () {
  "use strict";

  var NAMESPACE = "urn:x-cast:za.co.clinicq.board";
  var FALLBACK_CLAIM_URL = "/display/claim";

  function el(id) {
    return document.getElementById(id);
  }

  /** Say what is happening on the TV itself; a screen in a waiting room has no other console. */
  function say(message) {
    var slot = el("cast-status");
    if (slot) slot.textContent = message;
  }

  /** Put the board on screen, in a frame that fills it. */
  function showBoard(url) {
    var frame = el("cast-board");
    if (!frame) return;
    frame.src = url;
    frame.hidden = false;
    var splash = el("cast-splash");
    if (splash) splash.hidden = true;
  }

  /**
   * Spend `code` at the claim address and open whatever board comes back.
   * `claimUrl` is absolute when the dashboard sent one, because a screen on a clinic's network may
   * reach the server by an address this page was not served from.
   */
  function claim(code, claimUrl) {
    say("Setting this screen up…");
    return fetch(claimUrl || FALLBACK_CLAIM_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ code: code }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("claim refused: " + res.status);
        return res.json();
      })
      .then(function (data) {
        if (!data || !data.board_url) throw new Error("no board address");
        showBoard(data.board_url);
      })
      .catch(function (err) {
        say(
          "This screen could not be set up. Ask the clinic to send the board again."
        );
        if (window.console) window.console.error(err);
      });
  }

  function start() {
    if (!window.cast || !cast.framework) {
      say("This screen is not running the Cast receiver.");
      return;
    }
    var context = cast.framework.CastReceiverContext.getInstance();

    context.addCustomMessageListener(NAMESPACE, function (event) {
      var data = (event && event.data) || {};
      if (data.type !== "clinicq.board" || !data.claim) return;
      claim(data.claim, data.board_url);
    });

    var options = new cast.framework.CastReceiverOptions();
    // A waiting-room board shows numbers, not media, so the receiver plays nothing and would
    // otherwise be shut down for being idle. The board *is* the point; it must outlive the sender.
    options.disableIdleTimeout = true;
    // Nothing here is media, so the media element and its player are not wanted either.
    options.skipPlayersLoad = true;
    context.start(options);
    say("Waiting for the clinic…");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
