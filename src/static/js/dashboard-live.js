/* The front desk and the room, live, and honest about the connection (Issues 49, 50 and 55).
 *
 * The board must never look current when it is not. Clinic internet drops, and load-shedding is a
 * scheduled fact of life, so the line above the cards always says one of these, and how old the cards
 * are whenever the page is not sure it is live:
 *
 *   Live                                              the stream is open and the cards are fresh
 *   Reconnecting (attempt 3), data from HH:MM         the stream dropped; the page is trying again
 *   Updating every 5 seconds, data from HH:MM         live updates are unavailable; the page polls
 *   Offline, data from HH:MM (4 min old)              the network is gone; nothing new can arrive
 *
 * How it stays current:
 *
 *   - An EventSource on the page's stream. Any change event (queue.updated, ticket.called,
 *     board.config_changed) fetches the cards again; the events say what changed, never the new
 *     state, so the cards always come through the page's own gate.
 *   - The staff stream beats every 5 seconds. When nothing has arrived for 7 seconds the page asks
 *     /health, with a 2-second limit: no answer means Offline, within 10 seconds of the network going
 *     (at once when the browser itself reports it offline). 12 silent seconds with /health answering
 *     means the stream alone is dead, and it is reopened.
 *   - Every reconnection waits a little longer than the last: exponential backoff from 1 second,
 *     capped at 30, with jitter, so fifty reception PCs coming back after load-shedding do not all
 *     knock at the same instant. Coming back online tries at once.
 *   - After three failed attempts the page polls the cards every 5 seconds, with no reload, and keeps
 *     retrying the stream on the same backoff. Even while live, the cards are fetched again every
 *     minute as a safety net.
 *   - An expired session never reloads the page (that would lose what the person was doing): the page
 *     announces `session:expired`, dashboard-outbox.js asks the person to sign in again, and
 *     `session:renewed` reconnects.
 *
 * Every change of state is announced as `board:connection` with its state, and `window.ClinicQLive`
 * answers what the state is now: dashboard-outbox.js holds pressed actions while the page is offline
 * and sends them when it is back.
 *
 * Updates never take focus from the person using the page. A card is replaced only when nothing in
 * it has focus, its waiting line is not being dragged and no patient button's request is in flight
 * (dashboard-actions.js); open disclosures, half-written notes and scroll positions are carried over;
 * and nothing is swapped while a dialog (the reason prompt, a confirmation) is open. A refresh that
 * could not be applied is applied when focus leaves. Each replaced card is announced with
 * `board:card-replaced`, so its status line can say again what just happened.
 *
 * External file with no inline handlers: the CSP allows script only from 'self'.
 */
(function () {
  'use strict';

  var status = document.getElementById('board-connection');
  var root = document.getElementById('board-cards');
  if (!status || !root || !window.fetch) return;

  var STREAM_URL = status.getAttribute('data-stream-url');
  var CARDS_URL = status.getAttribute('data-cards-url');
  var HEALTH_URL = '/health';
  var POLL_MS = 5000;
  var SAFETY_REFRESH_MS = 60000;
  var PROBE_AFTER_SILENCE_MS = 7000; // two staff beats are 5 s apart: 7 s of nothing is suspicious
  var PROBE_TIMEOUT_MS = 2000;
  var STREAM_DEAD_AFTER_MS = 12000;
  var BACKOFF_BASE_MS = 1000;
  var BACKOFF_CAP_MS = 30000;
  var FAILURES_BEFORE_POLLING = 3;
  var TICK_MS = 1000;
  var DEBOUNCE_MS = 150;
  var CHANGE_EVENTS = ['queue.updated', 'ticket.called', 'board.config_changed'];

  var source = null;
  var mode = 'connecting'; // connecting | live | reconnecting | polling | offline
  var attempts = 0; // failed attempts since the last time the page was live
  var lastHeard = Date.now(); // the last event from the stream, or answer from the server
  var pollTimer = null;
  var retryTimer = null;
  var probing = false;
  var sessionExpired = false;
  var debounce = null;
  var pending = null; // a fetched set of cards waiting for focus to leave
  var inFlight = false;
  var again = false;

  function asOfLabel() {
    var current = document.getElementById('board-cards');
    return current ? current.getAttribute('data-as-of-label') : '';
  }

  /** How old the cards are, in words, once they are more than a minute old. */
  function ageWords() {
    var current = document.getElementById('board-cards');
    var asOf = current ? Date.parse(current.getAttribute('data-as-of')) : NaN;
    if (isNaN(asOf)) return '';
    var minutes = Math.floor((Date.now() - asOf) / 60000);
    return minutes >= 1 ? ' (' + minutes + ' min old)' : '';
  }

  /** Switch to `next`, say so, and tell the rest of the page. */
  function show(next) {
    var changed = next !== mode;
    mode = next;
    render(next);
    if (changed) {
      document.dispatchEvent(new CustomEvent('board:connection', { detail: { state: next } }));
    }
  }

  /** Say `state` on the line without changing how the page is updating (a failed poll, say). */
  function render(state) {
    status.setAttribute('data-state', state);
    status.setAttribute('data-attempt', String(attempts));
    var text = status.querySelector('.board-connection-text');
    var label = asOfLabel();
    if (state === 'live') text.textContent = 'Live';
    else if (state === 'polling') text.textContent = 'Updating every 5 seconds, data from ' + label;
    else if (state === 'offline') text.textContent = 'Offline, data from ' + label + ageWords();
    else if (state === 'reconnecting') {
      text.textContent = 'Reconnecting' + (attempts ? ' (attempt ' + attempts + ')' : '') + ', data from ' + label;
    } else text.textContent = 'Connecting, data from ' + label;
  }

  window.ClinicQLive = {
    state: function () { return mode; },
    /** Whether a request has a chance of reaching the clinic now. */
    reachable: function () { return mode !== 'offline' && navigator.onLine !== false; },
  };

  // ── Applying new cards without disturbing the person using the page ──────────────────────────

  /** True while someone is typing into something in `card`, or dragging in its line. */
  function busy(card) {
    var active = document.activeElement;
    var typing = active && card.contains(active) && (
      active.isContentEditable || active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.tagName === 'SELECT'
    );
    return Boolean(typing) || Boolean(card.querySelector('.is-dragging')) || card.hasAttribute('data-action-busy');
  }

  /** A selector that finds the focused control again in a replaced card, or null. */
  function focusKey(card) {
    var active = document.activeElement;
    if (!active || !card.contains(active)) return null;
    var ticket = active.closest('[data-ticket-id]');
    if (ticket && active.hasAttribute('data-move-ticket')) {
      return '[data-ticket-id="' + ticket.getAttribute('data-ticket-id') + '"] [data-move-ticket]';
    }
    var details = active.closest('details[data-queue-line]');
    if (details && active.tagName === 'SUMMARY') {
      return 'details[data-queue-line="' + details.getAttribute('data-queue-line') + '"] > summary';
    }
    if (active.hasAttribute('data-action') && window.CSS && CSS.escape) {
      return 'button[data-action-url="' + CSS.escape(active.getAttribute('data-action-url')) + '"]' +
        '[data-action="' + CSS.escape(active.getAttribute('data-action')) + '"]' +
        (active.hasAttribute('data-pending') ? '[data-pending="' + CSS.escape(active.getAttribute('data-pending')) + '"]' : '');
    }
    return null;
  }

  /** Tell the rest of the page a card was swapped in (dashboard-actions.js puts its message back). */
  function announce(card) {
    document.dispatchEvent(new CustomEvent('board:card-replaced', { detail: { queueId: card.getAttribute('data-queue-id') } }));
  }

  function dialogOpen() {
    return Boolean(document.querySelector('dialog[open]'));
  }

  /** Whether the person has typed into or chosen in `field` since the card was drawn. */
  function changedByPerson(field) {
    if (field.tagName === 'SELECT') {
      var initial = Array.prototype.find.call(field.options, function (option) { return option.defaultSelected; }) || field.options[0];
      return Boolean(initial) && field.value !== initial.value;
    }
    return field.value !== field.defaultValue;
  }

  /** Carry what the person has open, typed or scrolled over from the old card to the new one. */
  function carryOver(oldCard, newCard) {
    oldCard.querySelectorAll('details[open][data-queue-line]').forEach(function (details) {
      var match = newCard.querySelector('details[data-queue-line="' + details.getAttribute('data-queue-line') + '"]');
      if (match) match.open = true;
    });
    oldCard.querySelectorAll('details[data-keep-open]').forEach(function (details) {
      var match = newCard.querySelector('details[data-keep-open="' + details.getAttribute('data-keep-open') + '"]');
      if (match) match.open = details.open;
    });
    // A half-written note or a chosen destination is the person's, not the server's: keep it.
    oldCard.querySelectorAll('textarea[id], select[id], input[id]').forEach(function (field) {
      if (field.type === 'hidden' || field.type === 'checkbox' || field.type === 'radio') return;
      if (!changedByPerson(field)) return;
      var match = newCard.querySelector('#' + (window.CSS && CSS.escape ? CSS.escape(field.id) : field.id));
      if (match && match.tagName === field.tagName) match.value = field.value;
    });
    var oldList = oldCard.querySelector('.queue-line-list');
    var newList = newCard.querySelector('.queue-line-list');
    if (oldList && newList) newList.scrollTop = oldList.scrollTop;
  }

  /** Swap in `fresh` (the parsed #board-cards). Returns false if something had to wait. */
  function apply(fresh) {
    var current = document.getElementById('board-cards');
    if (!current) return true;
    if (dialogOpen()) return false;
    var oldCards = current.querySelectorAll('[data-queue-id]');
    var newCards = fresh.querySelectorAll('[data-queue-id]');
    var sameQueues = oldCards.length === newCards.length && Array.prototype.every.call(oldCards, function (card, index) {
      return card.getAttribute('data-queue-id') === newCards[index].getAttribute('data-queue-id');
    });
    if (!sameQueues) {
      // Queues were added, removed or reordered: replace the whole grid unless someone is mid-task.
      if (Array.prototype.some.call(oldCards, busy)) return false;
      current.replaceWith(fresh);
      fresh.querySelectorAll('[data-queue-id]').forEach(announce);
      return true;
    }
    var deferred = false;
    var replaced = [];
    Array.prototype.forEach.call(oldCards, function (oldCard, index) {
      var newCard = newCards[index];
      if (busy(oldCard)) {
        deferred = true;
        return;
      }
      carryOver(oldCard, newCard);
      var refocus = focusKey(oldCard);
      var placedCard = newCard.cloneNode(true);
      oldCard.replaceWith(placedCard);
      // cloneNode copies a field's attribute, not a value set from script: set carried drafts again.
      newCard.querySelectorAll('textarea[id], select[id], input[id]').forEach(function (field) {
        var twin = placedCard.querySelector('#' + (window.CSS && CSS.escape ? CSS.escape(field.id) : field.id));
        if (twin && twin.value !== field.value) twin.value = field.value;
      });
      replaced.push(placedCard);
      if (refocus) {
        var target = current.querySelector(refocus);
        if (target) target.focus({ preventScroll: true });
      }
      // The clone lost the scroll position; set it again on the node now in the page.
      var placed = current.querySelector('[data-queue-id="' + newCard.getAttribute('data-queue-id') + '"] .queue-line-list');
      var from = newCard.querySelector('.queue-line-list');
      if (placed && from) placed.scrollTop = from.scrollTop;
    });
    // The board is only as fresh as its oldest card: keep the old time while one is still waiting.
    if (!deferred) {
      current.setAttribute('data-as-of', fresh.getAttribute('data-as-of'));
      current.setAttribute('data-as-of-label', fresh.getAttribute('data-as-of-label'));
    }
    replaced.forEach(announce);
    return !deferred;
  }

  function parse(html) {
    var holder = document.createElement('template');
    holder.innerHTML = html.trim();
    return holder.content.querySelector('#board-cards');
  }

  /** Fetch the cards and apply them. Failures leave the board as it was, and say how old it is. */
  function refresh() {
    if (inFlight) {
      again = true;
      return Promise.resolve();
    }
    inFlight = true;
    return fetch(CARDS_URL, { credentials: 'same-origin', headers: { Accept: 'text/html' } })
      .then(function (response) {
        lastHeard = Date.now();
        if (response.status === 401) {
          // The session ended. Reloading would lose what the person was doing: ask them to sign in.
          if (!sessionExpired) {
            sessionExpired = true;
            document.dispatchEvent(new CustomEvent('session:expired'));
          }
          return null;
        }
        if (!response.ok) throw new Error(String(response.status));
        return response.text();
      })
      .then(function (html) {
        if (html === null || html === undefined) return;
        var fresh = parse(html);
        if (!fresh) throw new Error('no cards');
        if (!apply(fresh)) pending = fresh;
        else pending = null;
        render(mode);
      })
      .catch(function () {
        // Nothing fresh arrived: the board keeps its cards and says how old they are.
        if (mode === 'live' || mode === 'polling') render('reconnecting');
        probe();
      })
      .then(function () {
        inFlight = false;
        if (again) {
          again = false;
          refresh();
        }
      });
  }

  function refreshSoon() {
    window.clearTimeout(debounce);
    debounce = window.setTimeout(refresh, DEBOUNCE_MS);
  }

  function applyPending() {
    if (pending && apply(pending)) pending = null;
  }

  document.addEventListener('focusout', function () { window.setTimeout(applyPending, 0); });
  document.addEventListener('close', function () { window.setTimeout(applyPending, 0); }, true);
  // Another part of the page (a saved reorder) asks for fresh cards.
  document.addEventListener('board:refresh', refresh);

  // ── The stream, polling, backoff and the offline state ────────────────────────────────────────

  /** How long to wait before attempt `n`: doubling from 1 s, capped at 30 s, with jitter. */
  function backoff(n) {
    var ceiling = Math.min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * Math.pow(2, Math.max(0, n - 1)));
    // Equal jitter: half the delay is fixed, half random, so no two pages retry in lockstep.
    return ceiling / 2 + Math.random() * (ceiling / 2);
  }

  function stopPolling() {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }

  function closeStream() {
    if (source) {
      source.close();
      source = null;
    }
  }

  function scheduleRetry() {
    window.clearTimeout(retryTimer);
    retryTimer = window.setTimeout(connect, backoff(attempts));
  }

  /** One failed attempt at the stream: poll once enough have failed, and try again later. */
  function streamFailed() {
    closeStream();
    if (mode === 'offline') return;
    attempts += 1;
    if (attempts >= FAILURES_BEFORE_POLLING || !window.EventSource) {
      if (!pollTimer) pollTimer = window.setInterval(refresh, POLL_MS);
      show('polling');
    } else {
      show('reconnecting');
    }
    scheduleRetry();
  }

  function goOffline() {
    closeStream();
    stopPolling();
    window.clearTimeout(retryTimer);
    if (mode !== 'offline') attempts = 0;
    show('offline');
    // Ask again later, on the same backoff: the probe reconnects the moment /health answers.
    attempts += 1;
    retryTimer = window.setTimeout(probe, backoff(attempts));
  }

  function backOnline() {
    window.clearTimeout(retryTimer);
    attempts = 0;
    show('connecting');
    connect();
  }

  /** Ask /health whether the clinic is reachable at all; decide between offline and a dead stream. */
  function probe() {
    if (probing) return;
    probing = true;
    var controller = window.AbortController ? new AbortController() : null;
    var timer = window.setTimeout(function () { if (controller) controller.abort(); }, PROBE_TIMEOUT_MS);
    fetch(HEALTH_URL, { cache: 'no-store', credentials: 'omit', signal: controller ? controller.signal : undefined })
      .then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        lastHeard = Date.now();
        if (mode === 'offline') backOnline();
      })
      .catch(function () {
        goOffline();
      })
      .then(function () {
        window.clearTimeout(timer);
        probing = false;
      });
  }

  function connect() {
    window.clearTimeout(retryTimer);
    if (navigator.onLine === false) {
      goOffline();
      return;
    }
    if (sessionExpired) return; // reconnect once the person has signed in again
    if (!window.EventSource) {
      streamFailed();
      return;
    }
    closeStream();
    var opened = new window.EventSource(STREAM_URL, { withCredentials: true });
    source = opened;
    opened.addEventListener('open', function () {
      if (source !== opened) return;
      attempts = 0;
      lastHeard = Date.now();
      stopPolling();
      show('live');
      refresh(); // resync: something may have changed while the stream was down
    });
    opened.addEventListener('heartbeat', function () {
      if (source !== opened) return;
      lastHeard = Date.now();
      if (mode !== 'live') {
        attempts = 0;
        stopPolling();
        show('live');
      }
    });
    CHANGE_EVENTS.forEach(function (type) {
      opened.addEventListener(type, function () {
        if (source !== opened) return;
        lastHeard = Date.now();
        refreshSoon();
      });
    });
    opened.addEventListener('error', function () {
      if (source !== opened) return;
      // Let the probe say whether this is the network or only the stream.
      probe();
      streamFailed();
    });
  }

  // The watchdog: silence first asks /health; a stream silent too long is reopened.
  window.setInterval(function () {
    if (mode === 'offline') {
      render('offline'); // keep the age current
      return;
    }
    var silent = Date.now() - lastHeard;
    if (mode === 'live' && silent > STREAM_DEAD_AFTER_MS) {
      streamFailed();
    } else if (silent > PROBE_AFTER_SILENCE_MS) {
      probe();
    }
  }, TICK_MS);

  // The safety net: fresh cards every minute whatever the stream says.
  window.setInterval(function () {
    if (mode === 'live') refresh();
  }, SAFETY_REFRESH_MS);

  window.addEventListener('offline', goOffline);
  window.addEventListener('online', backOnline);
  document.addEventListener('session:renewed', function () {
    sessionExpired = false;
    backOnline();
    refresh();
  });

  connect();
})();
