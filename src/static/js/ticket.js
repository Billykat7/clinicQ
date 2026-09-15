/**
 * The patient's ticket page (Issue 68): keeps the ticket live, and says so when it is not.
 *
 * Reads the state the page was rendered with (#tk-state), then:
 *   - follows the ticket's stream (ticket.state and heartbeat events, the board's envelope, Issue 57);
 *   - while the stream is down, reads GET <data-api-url> every state.refresh_seconds instead;
 *   - shows how old the data is at all times, and a "Not live" warning once nothing (a state or a
 *     heartbeat) has arrived for state.stale_after_seconds;
 *   - counts the wait range down between updates, so it stays a range and never looks exact;
 *   - makes "you are next" and "please come in now" unmissable: the alert, the page title, a vibration;
 *   - cancels in two taps for the ticket's own patient (the API decides who that is: cancel_url);
 *   - keeps every state it shows on the phone (patient-tickets.js), for the offline page (Issue 69).
 *
 * Every sentence is in the template; this file only chooses which block shows and fills in the values.
 * External file, no inline handlers, no eval: satisfies script-src 'self'.
 */
(function () {
  "use strict";

  var root = document.getElementById("tk");
  var embedded = document.getElementById("tk-state");
  if (!root || !embedded) return;

  var state;
  try {
    state = JSON.parse(embedded.textContent || "null");
  } catch (err) {
    return;
  }
  if (!state) return;

  var apiUrl = root.getAttribute("data-api-url");
  var FINISHED = ["done", "cancelled", "missed", "transferred"];
  var STREAM_RETRY_MS = 60000;
  var baseTitle = document.title;

  /** When the current state reached this browser, and when anything last did (a state or a heartbeat). */
  var receivedAt = Date.now();
  var lastHeard = Date.now();
  var stream = null;
  var pollTimer = null;
  var retryTimer = null;

  var timeFormat = new Intl.DateTimeFormat("en-ZA", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "Africa/Johannesburg"
  });

  function $(id) {
    return document.getElementById(id);
  }

  function isFinished(s) {
    return FINISHED.indexOf(s.headline) !== -1;
  }

  /** "42 s", "3 min", "1 h 5 min": how long ago, in words a glance can read. */
  function age(ms) {
    var seconds = Math.max(0, Math.round(ms / 1000));
    if (seconds < 60) return seconds + " s";
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) return minutes + " min";
    return Math.floor(minutes / 60) + " h " + (minutes % 60) + " min";
  }

  /** The wait range, counted down from when it was read; always a range, "any moment now" at the end. */
  function waitLabel(s) {
    if (!s.wait) return "";
    var elapsed = (Date.now() - receivedAt) / 60000;
    var low = Math.max(0, Math.round(s.wait.low_minutes - elapsed));
    var high = Math.round(s.wait.high_minutes - elapsed);
    if (high <= 0) return "any moment now";
    var range = "~" + low + "–" + high + " min";
    return s.wait.approximate ? range + " (approximate)" : range;
  }

  function fill(name, value) {
    var nodes = root.querySelectorAll('[data-fill="' + name + '"]');
    for (var i = 0; i < nodes.length; i++) nodes[i].textContent = value == null ? "" : String(value);
  }

  function showWhen(headline) {
    var nodes = root.querySelectorAll("[data-when]");
    for (var i = 0; i < nodes.length; i++) {
      var when = nodes[i].getAttribute("data-when").split(" ");
      nodes[i].hidden = when.indexOf(headline) === -1;
    }
    $("tk-alert").hidden = headline !== "next" && headline !== "called";
  }

  /** The moments a patient must not miss: say it in the title and, where the phone allows, by vibrating. */
  function announce(previous, next) {
    var urgent = next.headline === "next" || next.headline === "called";
    if (urgent) {
      document.title = (next.headline === "next" ? "You are next" : "Come in now") + " · " + next.number;
    } else {
      document.title = baseTitle;
    }
    if (urgent && previous !== next.headline && navigator.vibrate) {
      try {
        navigator.vibrate([300, 150, 300, 150, 600]);
      } catch (err) {
        /* not allowed without a tap on this browser: the screen still says it */
      }
    }
  }

  function render(next) {
    var previous = state.headline;
    state = next;
    receivedAt = Date.now();
    lastHeard = receivedAt;
    // Kept on the phone for the offline page and the installed app (Issue 69).
    if (window.BKPTickets) window.BKPTickets.save(window.location.pathname, next);

    root.className = "tk tk-" + next.headline;
    root.setAttribute("data-headline", next.headline);
    showWhen(next.headline);

    fill("number", next.number);
    fill("clinic", next.clinic.name);
    fill("queue", next.queue.name);
    fill("where", next.queue.room || next.queue.name);
    fill("reference_code", next.reference_code);
    fill("position", next.position);
    fill("ahead", next.waiting_ahead);
    fill("ahead-noun", next.waiting_ahead === 1 ? "person" : "people");
    fill("wait", waitLabel(next));
    var aheadLine = root.querySelector("[data-when-ahead]");
    if (aheadLine) aheadLine.hidden = !next.waiting_ahead;

    var nextLink = root.querySelector('[data-fill-href="next_page_url"]');
    if (nextLink) {
      nextLink.hidden = !next.next_page_url;
      if (next.next_page_url) nextLink.setAttribute("href", next.next_page_url);
    }

    var asOf = $("tk-as-of");
    var at = new Date(next.as_of);
    asOf.setAttribute("datetime", next.as_of);
    asOf.textContent = isNaN(at.getTime()) ? "" : timeFormat.format(at);

    $("tk-cancel").hidden = !next.cancel_url;
    if (!next.cancel_url) $("tk-confirm").hidden = true;
    renderTravel(next.call_forward);

    announce(previous, next);
    tick();
  }

  var leaveFormat = new Intl.DateTimeFormat("en-ZA", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Africa/Johannesburg"
  });

  /** The virtual waiting room block (Issue 86): the server decides when to leave; this only shows it. */
  function renderTravel(travel) {
    var block = $("tk-travel");
    if (!block) return;
    block.hidden = !travel;
    if (!travel) return;
    var leaveAt = travel.leave_at ? new Date(travel.leave_at) : null;
    fill("leave_at", leaveAt && !isNaN(leaveAt.getTime()) ? leaveFormat.format(leaveAt) : "");
    fill("travel_minutes", travel.travel_minutes);
    block.querySelector('[data-travel="later"]').hidden = travel.due;
    block.querySelector('[data-travel="now"]').hidden = !travel.due;
    block.querySelector('[data-travel="said"]').hidden = !travel.on_my_way_at;
    $("tk-on-my-way").hidden = !travel.on_my_way_url;
  }

  /** Once a second: the age of the data, the countdown, and whether the page is still live. */
  function tick() {
    var now = Date.now();
    $("tk-age").textContent = " · " + age(now - receivedAt) + " ago";
    fill("wait", waitLabel(state));
    var finished = isFinished(state);
    var stale = !finished && now - lastHeard > state.stale_after_seconds * 1000;
    // A stream that went silent without an error (a dead router) is not trusted any more: poll too.
    if (stale) startPolling();
    $("tk-stale").hidden = !stale;
    $("tk-stale-age").textContent = age(now - receivedAt);
    var live = $("tk-live");
    live.setAttribute("data-live", stale || finished ? "false" : "true");
    live.textContent = finished ? "Ended" : stale ? "Not live" : "Live";
  }

  // ── Following the ticket ──────────────────────────────────────────────────────────────────

  /**
   * One read of the ticket. At most one at a time, and each gives up after POLL_TIMEOUT_MS: on a network
   * that swallows packets a request can hang for minutes, and polls stacking up behind it would fill the
   * browser's six connections to this site, so none could get through when the signal came back.
   */
  var POLL_TIMEOUT_MS = 10000;
  var polling = false;
  function poll() {
    if (!apiUrl || polling) return;
    polling = true;
    var controller = typeof window.AbortController === "function" ? new window.AbortController() : null;
    var timer = window.setTimeout(function () {
      if (controller) controller.abort();
    }, POLL_TIMEOUT_MS);
    fetch(apiUrl, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: controller ? controller.signal : undefined
    })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (next) {
        render(next);
        if (isFinished(next)) stopFollowing();
      })
      .catch(function () {
        /* offline, timed out or the server is away: the age keeps growing and the warning says so */
      })
      .then(function () {
        window.clearTimeout(timer);
        polling = false;
      });
  }

  function startPolling() {
    if (pollTimer || isFinished(state)) return;
    pollTimer = window.setInterval(poll, state.refresh_seconds * 1000);
  }

  function stopPolling() {
    if (pollTimer) window.clearInterval(pollTimer);
    pollTimer = null;
  }

  function stopFollowing() {
    stopPolling();
    if (retryTimer) window.clearTimeout(retryTimer);
    retryTimer = null;
    if (stream) stream.close();
    stream = null;
    tick();
  }

  function openStream() {
    if (!state.stream_url || typeof window.EventSource !== "function" || isFinished(state)) {
      startPolling();
      return;
    }
    stream = new window.EventSource(state.stream_url);
    stream.addEventListener("open", function () {
      // Polling stops once the stream proves itself with an event, not merely by opening.
    });
    stream.addEventListener("ticket.state", function (message) {
      stopPolling();
      try {
        var data = JSON.parse(message.data);
        render(data.ticket);
        if (isFinished(data.ticket)) stopFollowing();
      } catch (err) {
        /* an unreadable event is skipped; the next one or a poll puts it right */
      }
    });
    stream.addEventListener("heartbeat", function () {
      lastHeard = Date.now();
      stopPolling();
    });
    stream.addEventListener("error", function () {
      // The browser reconnects by itself unless the server refused (a 503, a 404): poll meanwhile.
      startPolling();
      if (stream && stream.readyState === window.EventSource.CLOSED) {
        stream = null;
        if (!retryTimer && !isFinished(state)) {
          retryTimer = window.setTimeout(function () {
            retryTimer = null;
            openStream();
          }, STREAM_RETRY_MS);
        }
      }
    });
  }

  // ── Sharing ───────────────────────────────────────────────────────────────────────────────

  var shareButton = $("tk-share");
  var shareNote = $("tk-share-note");
  function sayShared(text) {
    shareNote.textContent = text;
    shareNote.hidden = false;
  }
  shareButton.addEventListener("click", function () {
    var link = window.location.href;
    if (navigator.share) {
      navigator.share({ title: "Ticket " + state.number, url: link }).catch(function () {});
      return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(link).then(
        function () {
          sayShared("Link copied. Anyone with it can follow this ticket and change how you are told, but not cancel it.");
        },
        function () {
          sayShared(link);
        }
      );
      return;
    }
    sayShared(link);
  });

  // ── Cancelling: two taps ──────────────────────────────────────────────────────────────────

  var confirmBox = $("tk-confirm");
  var startButton = $("tk-cancel-start");
  var yesButton = $("tk-cancel-yes");
  var cancelNote = $("tk-cancel-note");

  function csrfToken() {
    return window.BKP && typeof window.BKP.csrfToken === "function" ? window.BKP.csrfToken() : "";
  }

  startButton.addEventListener("click", function () {
    confirmBox.hidden = false;
    startButton.hidden = true;
    yesButton.focus();
  });
  $("tk-cancel-no").addEventListener("click", function () {
    confirmBox.hidden = true;
    startButton.hidden = false;
    startButton.focus();
  });
  yesButton.addEventListener("click", function () {
    if (!state.cancel_url) return;
    yesButton.disabled = true;
    fetch(state.cancel_url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": csrfToken() },
      body: "{}"
    })
      .then(function (response) {
        return response.json().then(function (body) {
          return { ok: response.ok, body: body };
        });
      })
      .then(function (result) {
        yesButton.disabled = false;
        cancelNote.hidden = false;
        if (result.ok) {
          cancelNote.textContent = result.body.message || "Your ticket is cancelled.";
          poll();
        } else {
          cancelNote.textContent = (result.body && result.body.detail) || "The ticket could not be cancelled. Please ask at the front desk.";
          confirmBox.hidden = true;
          startButton.hidden = false;
        }
      })
      .catch(function () {
        yesButton.disabled = false;
        cancelNote.hidden = false;
        cancelNote.textContent = "No connection: the ticket is not cancelled yet. Try again when you have signal.";
      });
  });

  // ── On my way (Issue 86) ──────────────────────────────────────────────────────────────────

  var onMyWay = $("tk-on-my-way");
  var onMyWayNote = $("tk-on-my-way-note");
  if (onMyWay) {
    onMyWay.addEventListener("click", function () {
      var travel = state.call_forward;
      if (!travel || !travel.on_my_way_url) return;
      onMyWay.disabled = true;
      fetch(travel.on_my_way_url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": csrfToken() },
        body: "{}"
      })
        .then(function (response) {
          return response.json().then(function (body) {
            return { ok: response.ok, body: body };
          });
        })
        .then(function (result) {
          onMyWay.disabled = false;
          onMyWayNote.hidden = result.ok;
          if (result.ok) {
            poll();
          } else {
            onMyWayNote.textContent = (result.body && result.body.detail) || "That did not reach the clinic. Please try again.";
          }
        })
        .catch(function () {
          onMyWay.disabled = false;
          onMyWayNote.hidden = false;
          onMyWayNote.textContent = "No connection: the clinic has not been told yet. Try again when you have signal.";
        });
    });
  }

  // ── Start ─────────────────────────────────────────────────────────────────────────────────

  render(state);
  receivedAt = new Date(state.as_of).getTime() || Date.now();
  lastHeard = Date.now();
  window.setInterval(tick, 1000);
  openStream();
})();
