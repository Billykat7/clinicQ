/**
 * The offline page (Issue 69): the last known state of the ticket, and how old it is.
 *
 * The ticket's code for reception (Issue 70) is drawn here too, from the kept state's reference_qr: the QR
 * needs no network and no library, only the path the server sent while the phone was online.
 *
 * The service worker serves this page in place of a /t/ page it could not fetch, so location.pathname is
 * the ticket link the patient opened (or /t/ when the installed app was launched). The state comes from
 * patient-tickets.js: that ticket's, or else the most recent one this phone followed. The age counts up
 * every second from when the phone received it; nothing here suggests the numbers are current.
 *
 * When the network comes back the page reloads, and the worker then fetches the live page: on the browser's
 * "online" event, on "Try again", and on its own every RETRY_MS, because a phone behind a dead router or on a
 * flaky signal is never told it is back online. The check asks for the page itself, with a time limit. External file, no inline handlers, no eval: satisfies script-src 'self'.
 */
(function () {
  "use strict";

  var root = document.getElementById("off");
  if (!root) return;

  var timeFormat = new Intl.DateTimeFormat("en-ZA", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Africa/Johannesburg"
  });
  var dayFormat = new Intl.DateTimeFormat("en-ZA", { weekday: "short", day: "numeric", month: "short", timeZone: "Africa/Johannesburg" });

  function $(id) {
    return document.getElementById(id);
  }

  function age(ms) {
    var seconds = Math.max(0, Math.round(ms / 1000));
    if (seconds < 60) return seconds + " s";
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) return minutes + " min";
    return Math.floor(minutes / 60) + " h " + (minutes % 60) + " min";
  }

  function fill(name, value) {
    var nodes = root.querySelectorAll('[data-fill="' + name + '"]');
    for (var i = 0; i < nodes.length; i++) nodes[i].textContent = value == null ? "" : String(value);
  }

  function show(entry) {
    var state = entry.state;
    fill("number", state.number);
    fill("clinic", state.clinic && state.clinic.name);
    fill("queue", state.queue && state.queue.name);
    fill("where", state.queue && (state.queue.room || state.queue.name));
    fill("position", state.position);
    fill("ahead", state.waiting_ahead);
    fill("ahead-noun", state.waiting_ahead === 1 ? "person was" : "people were");
    var nodes = root.querySelectorAll("[data-when]");
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].hidden = nodes[i].getAttribute("data-when").split(" ").indexOf(state.headline) === -1;
    }
    var ahead = root.querySelector("[data-when-ahead]");
    if (ahead) ahead.hidden = !state.waiting_ahead;

    fill("reference_code", state.reference_code);
    fill("reference_spoken", state.reference_spoken);
    var qr = state.reference_qr;
    if (qr && typeof qr.path === "string" && qr.size > 0) {
      var box = "0 0 " + qr.size + " " + qr.size;
      $("off-qr").setAttribute("viewBox", box);
      $("off-qr").setAttribute("aria-label", "QR code for ticket code " + state.reference_code);
      $("off-qr-ground").setAttribute("width", String(qr.size));
      $("off-qr-ground").setAttribute("height", String(qr.size));
      $("off-qr-path").setAttribute("d", qr.path);
      $("off-code").hidden = false;
    }

    var at = new Date(entry.receivedAt);
    var sameDay = dayFormat.format(at) === dayFormat.format(new Date());
    $("off-as-of").setAttribute("datetime", at.toISOString());
    $("off-as-of").textContent = (sameDay ? "" : dayFormat.format(at) + " ") + timeFormat.format(at);
    root.setAttribute("data-kept-at", String(entry.receivedAt));
    document.title = "Offline · " + state.number;

    $("off-ticket").hidden = false;
    $("off-updated").hidden = false;
    $("off-kept-line").hidden = false;
    tick();
    window.setInterval(tick, 1000);
  }

  function tick() {
    var kept = Number(root.getAttribute("data-kept-at"));
    if (kept) $("off-age").textContent = age(Date.now() - kept);
  }

  var RETRY_MS = 15000;
  var CHECK_LIMIT_MS = 8000;
  var checking = false;

  /** Whether the server answers now: then reload into the live page. At most one check at a time. */
  function check() {
    if (checking || typeof fetch !== "function") return;
    checking = true;
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = window.setTimeout(function () {
      if (controller) controller.abort();
    }, CHECK_LIMIT_MS);
    fetch(window.location.pathname, {
      cache: "no-store",
      credentials: "same-origin",
      headers: { Accept: "text/html" },
      signal: controller ? controller.signal : undefined
    })
      .then(function (response) {
        if (response.ok) window.location.reload();
      })
      .catch(function () {
        /* still no network */
      })
      .then(function () {
        window.clearTimeout(timer);
        checking = false;
      });
  }
  window.setInterval(check, RETRY_MS);

  function none() {
    $("off-none").hidden = false;
  }

  $("off-retry").addEventListener("click", function () {
    window.location.reload();
  });
  window.addEventListener("online", function () {
    window.location.reload();
  });

  var store = window.BKPTickets;
  if (!store) return none();
  store
    .get(window.location.pathname)
    .then(function (entry) {
      return entry || store.latest();
    })
    .then(function (entry) {
      if (entry) show(entry);
      else none();
    })
    .catch(none);
})();
