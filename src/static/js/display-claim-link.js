/**
 * Making the one-time address a screen opens to become this clinic's board (Issue 237).
 *
 * The counterpart of typing the code a screen displays: here the clinic holds the row open and the
 * *screen* is given the address. It is the only path that needs no Chromecast, no discovery and no
 * second device at all — the link opens in another browser window — so it is what a deployment
 * uses when the Cast receiver is not registered, when the television will not be cast to, and when
 * somebody is checking the board on a laptop.
 *
 * What comes back is a credential, briefly: single-use, and spent by whoever opens it first. So it
 * is shown only after it is asked for, never written to the URL or to storage, and the page says
 * plainly that opening it here claims the row for this browser.
 *
 * External file only, to satisfy `script-src 'self'`.
 */
(function () {
  "use strict";

  var root = document.getElementById("claim-link");
  if (!root) return;

  var button = document.getElementById("make-link");
  var message = document.getElementById("link-msg");
  var result = document.getElementById("link-result");
  var urlField = document.getElementById("link-url");
  var codeField = document.getElementById("link-code");
  var kind = document.getElementById("link-kind");
  var label = document.getElementById("link-label");
  var copyButton = document.getElementById("copy-link");
  var copyMessage = document.getElementById("copy-msg");

  /** Show `text` beside the button; `ok` marks the hopeful kind. */
  function say(text, ok) {
    if (!message) return;
    message.textContent = text || "";
    message.classList.toggle("is-ok", Boolean(ok));
  }

  /** The human sentence in a FastAPI error body, or `fallback`. */
  function errorText(data, fallback) {
    var detail = data && data.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length && detail[0] && detail[0].msg) {
      return detail[0].msg;
    }
    return fallback;
  }

  /** How long the code has left, said the way a person would. */
  function minutesLeft(expiresAt) {
    if (!expiresAt) return "";
    var left = Math.round((new Date(expiresAt).getTime() - Date.now()) / 60000);
    if (!isFinite(left) || left <= 0) return "";
    return left === 1 ? " It works for a minute." : " It works for " + left + " minutes.";
  }

  function show(data) {
    if (urlField) urlField.value = data.claim_url || "";
    if (codeField) codeField.textContent = data.claim_code || "";
    if (result) result.hidden = false;
    if (copyMessage) copyMessage.textContent = "";
    say("Address ready." + minutesLeft(data.expires_at), true);
  }

  button.addEventListener("click", function () {
    button.disabled = true;
    button.textContent = "Making…";
    say("");
    if (result) result.hidden = true;
    fetch(root.dataset.api, {
      method: "POST",
      headers: window.BKP.writeHeaders(),
      credentials: "same-origin",
      body: JSON.stringify({
        kind: kind ? kind.value : "board",
        label: label && label.value.trim() ? label.value.trim() : null,
      }),
    })
      .then(function (res) {
        return res
          .json()
          .catch(function () { return null; })
          .then(function (data) { return { ok: res.ok, data: data }; });
      })
      .then(function (result) {
        button.disabled = false;
        button.textContent = "Make an address";
        if (!result.ok) {
          say(errorText(result.data, "Could not make an address for a screen."));
          return;
        }
        show(result.data || {});
      })
      .catch(function () {
        button.disabled = false;
        button.textContent = "Make an address";
        say("Could not reach the server. Check your connection and try again.");
      });
  });

  if (copyButton && urlField) {
    copyButton.addEventListener("click", function () {
      // Selecting and copying works without the clipboard permission, and on a page served over
      // plain http on a laptop — where navigator.clipboard is not available at all.
      urlField.focus();
      urlField.select();
      var copied = false;
      try {
        copied = document.execCommand("copy");
      } catch (error) {
        copied = false;
      }
      if (copyMessage) {
        copyMessage.textContent = copied
          ? "Copied."
          : "Press " + (navigator.platform.indexOf("Mac") >= 0 ? "⌘C" : "Ctrl+C") + " to copy.";
      }
    });
  }
})();
