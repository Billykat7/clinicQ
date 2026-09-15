/**
 * Web push on the ticket page (Issue 64): "tell me on this phone when it is my turn".
 *
 * The browser's permission prompt is never shown when a page loads. This file only runs on a ticket's own
 * page, which exists once a patient has joined, and even there it asks only when the patient presses the
 * button. The page data offers the button (push_key) only to the ticket's own signed-in patient.
 *
 * Pressing it: ask for permission, register the patient service worker (/patient-sw.js, scope /t/),
 * subscribe with the server's VAPID key, and send the subscription to push_subscribe_url. Saying no is
 * fine: the notification service falls back to SMS for a patient with no subscription, and the page
 * says so. A browser that cannot do web push (an iPhone outside a Home Screen app, an old browser) is
 * told what it can do instead.
 *
 * External file, no inline handlers, no eval: satisfies script-src 'self'.
 */
(function () {
  "use strict";

  var embedded = document.getElementById("tk-state");
  var box = document.getElementById("tk-push");
  if (!embedded || !box) return;

  var state;
  try {
    state = JSON.parse(embedded.textContent || "null");
  } catch (err) {
    return;
  }
  if (!state || !state.push_key || !state.push_subscribe_url) return;

  var button = document.getElementById("tk-push-start");
  var note = document.getElementById("tk-push-note");
  var SW_URL = "/patient-sw.js";
  var SW_SCOPE = "/t/";

  function say(key) {
    var line = box.querySelector('[data-push-say="' + key + '"]');
    note.textContent = line ? line.textContent : "";
    note.hidden = !note.textContent;
  }

  function supported() {
    return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  }

  function isAppleMobile() {
    return /iPhone|iPad|iPod/.test(navigator.userAgent || "");
  }

  function csrfToken() {
    return window.BKP && typeof window.BKP.csrfToken === "function" ? window.BKP.csrfToken() : "";
  }

  /** The VAPID key as the Uint8Array PushManager.subscribe wants. */
  function keyBytes(base64url) {
    var padded = (base64url + "===".slice((base64url.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
    var raw = window.atob(padded);
    var bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return bytes;
  }

  function send(subscription) {
    return fetch(state.push_subscribe_url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": csrfToken() },
      body: JSON.stringify(subscription.toJSON())
    }).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
    });
  }

  function subscribe() {
    return navigator.serviceWorker
      .register(SW_URL, { scope: SW_SCOPE })
      .then(function () {
        return navigator.serviceWorker.ready;
      })
      .then(function (registration) {
        return registration.pushManager.getSubscription().then(function (existing) {
          return (
            existing ||
            registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(state.push_key) })
          );
        });
      })
      .then(send);
  }

  if (!supported()) {
    box.hidden = false;
    button.hidden = true;
    say(isAppleMobile() ? "ios" : "unsupported");
    return;
  }

  box.hidden = false;
  if (Notification.permission === "denied") {
    button.hidden = true;
    say("declined");
    return;
  }
  if (Notification.permission === "granted") {
    // Already allowed on this phone: make sure the server has this subscription, without asking anything.
    button.hidden = true;
    subscribe().then(
      function () {
        say("on");
      },
      function () {
        button.hidden = false;
      }
    );
    return;
  }

  button.addEventListener("click", function () {
    button.disabled = true;
    Notification.requestPermission().then(function (answer) {
      if (answer !== "granted") {
        button.hidden = true;
        say("declined");
        return;
      }
      subscribe().then(
        function () {
          button.hidden = true;
          say("on");
        },
        function () {
          button.disabled = false;
          say("failed");
        }
      );
    });
  });
})();
