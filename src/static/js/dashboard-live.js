/* The front desk, live (Issue 49).
 *
 * The board must never look current when it is not. So the line above it always says one of three
 * things, and the board's age is shown whenever the page is not sure it is live:
 *
 *   Live                                   the stream is open and the cards are fresh
 *   Reconnecting, data from HH:MM          the stream dropped; the cards are as old as HH:MM
 *   Updating every 5 seconds, data from HH:MM   live updates are unavailable; the page polls instead
 *
 * How it stays current:
 *
 *   - An EventSource on /board/stream. Any change event (queue.updated, ticket.called,
 *     board.config_changed) fetches the cards again from /board/cards; the events say what changed,
 *     never the new state, so the cards always come through the page's own gate. A heartbeat arrives
 *     every 15 seconds; two missed beats mean the connection is gone even if the browser has not
 *     noticed.
 *   - When the stream will not open (three failures in a row, or no EventSource at all), the page
 *     switches to fetching the cards every 5 seconds, with no reload, and tries the stream again
 *     every minute.
 *   - Even while live, the cards are fetched again every minute, so an event this instance never
 *     heard (another server behind a balancer) costs a minute of freshness, never a wrong board.
 *
 * Updates never take focus from the person using the page. A card is replaced only when nothing in
 * it has focus and its waiting line is not being dragged; open disclosures and scroll positions are
 * carried over; and nothing is swapped while a dialog (the reason prompt) is open. A refresh that
 * could not be applied is applied when focus leaves.
 *
 * Issue 55 adds exponential backoff with jitter, the offline state and queued actions on top of this.
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
  var POLL_MS = 5000;
  var SAFETY_REFRESH_MS = 60000;
  var RETRY_STREAM_MS = 60000;
  var HEARTBEAT_GRACE_MS = 35000;
  var FAILURES_BEFORE_POLLING = 3;
  var DEBOUNCE_MS = 150;
  var CHANGE_EVENTS = ['queue.updated', 'ticket.called', 'board.config_changed'];

  var source = null;
  var mode = 'connecting'; // live | reconnecting | polling | connecting
  var failures = 0;
  var lastBeat = 0;
  var pollTimer = null;
  var retryTimer = null;
  var debounce = null;
  var pending = null; // a fetched set of cards waiting for focus to leave
  var inFlight = false;
  var again = false;

  function asOfLabel() {
    var current = document.getElementById('board-cards');
    return current ? current.getAttribute('data-as-of-label') : '';
  }

  /** Switch to `next` and say so. */
  function show(next) {
    mode = next;
    render(next);
  }

  /** Say `state` on the line without changing how the page is updating (a failed poll, say). */
  function render(state) {
    var next = state;
    status.setAttribute('data-state', next);
    var text = status.querySelector('.board-connection-text');
    var label = asOfLabel();
    if (next === 'live') text.textContent = 'Live';
    else if (next === 'polling') text.textContent = 'Updating every 5 seconds, data from ' + label;
    else if (next === 'reconnecting') text.textContent = 'Reconnecting, data from ' + label;
    else text.textContent = 'Connecting, data from ' + label;
  }

  // ── Applying new cards without disturbing the person using the page ──────────────────────────

  /** True while someone is typing into something in `card`, or dragging in its line. */
  function busy(card) {
    var active = document.activeElement;
    var typing = active && card.contains(active) && (
      active.isContentEditable || active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.tagName === 'SELECT'
    );
    return Boolean(typing) || Boolean(card.querySelector('.is-dragging'));
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
    return null;
  }

  function dialogOpen() {
    return Boolean(document.querySelector('dialog[open]'));
  }

  /** Carry what the person has open or scrolled over from the old card to the new one. */
  function carryOver(oldCard, newCard) {
    oldCard.querySelectorAll('details[open][data-queue-line]').forEach(function (details) {
      var match = newCard.querySelector('details[data-queue-line="' + details.getAttribute('data-queue-line') + '"]');
      if (match) match.open = true;
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
      return true;
    }
    var deferred = false;
    Array.prototype.forEach.call(oldCards, function (oldCard, index) {
      var newCard = newCards[index];
      if (busy(oldCard)) {
        deferred = true;
        return;
      }
      carryOver(oldCard, newCard);
      var refocus = focusKey(oldCard);
      oldCard.replaceWith(newCard.cloneNode(true));
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
        if (response.status === 401) {
          window.location.reload(); // the session ended: let the page send the person to sign in
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
        render('reconnecting');
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

  // ── The stream, and polling when it is unavailable ─────────────────────────────────────────────

  function stopPolling() {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }

  function startPolling() {
    if (source) {
      source.close();
      source = null;
    }
    show('polling');
    if (!pollTimer) pollTimer = window.setInterval(refresh, POLL_MS);
    window.clearTimeout(retryTimer);
    retryTimer = window.setTimeout(connect, RETRY_STREAM_MS);
  }

  function connect() {
    if (!window.EventSource) {
      startPolling();
      return;
    }
    if (source) source.close();
    source = new window.EventSource(STREAM_URL, { withCredentials: true });
    source.addEventListener('open', function () {
      failures = 0;
      lastBeat = Date.now();
      stopPolling();
      window.clearTimeout(retryTimer);
      show('live');
      refresh(); // resync: something may have changed while the stream was down
    });
    source.addEventListener('heartbeat', function () {
      lastBeat = Date.now();
      if (mode !== 'live') show('live');
    });
    CHANGE_EVENTS.forEach(function (type) {
      source.addEventListener(type, function () {
        lastBeat = Date.now();
        refreshSoon();
      });
    });
    source.addEventListener('error', function () {
      failures += 1;
      if (failures >= FAILURES_BEFORE_POLLING || (source && source.readyState === window.EventSource.CLOSED)) {
        startPolling();
      } else if (!pollTimer) {
        show('reconnecting');
      }
    });
  }

  // Two missed heartbeats: the connection is gone, whatever the browser thinks.
  window.setInterval(function () {
    if (mode === 'live' && lastBeat && Date.now() - lastBeat > HEARTBEAT_GRACE_MS) {
      failures += 1;
      show('reconnecting');
      connect();
    }
  }, 5000);

  // The safety net: fresh cards every minute whatever the stream says.
  window.setInterval(function () {
    if (mode === 'live') refresh();
  }, SAFETY_REFRESH_MS);

  window.addEventListener('offline', function () { show(pollTimer ? 'polling' : 'reconnecting'); });

  connect();
})();
