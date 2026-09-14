/* The waiting-room board, live: the stream, the heartbeat watchdog and reconnection (Issue 57).
 *
 * A board is installed once and then left alone for months, so it must heal itself. This script keeps it
 * current and says plainly when it is not:
 *
 *   - An EventSource on data-stream-url. Every board event (board.state first on each connection, then
 *     ticket.called, queue.updated, board.config_changed) carries the whole board, the privacy
 *     projection, and is handed to board.js through window.ClinicQBoard.apply. The first event on a
 *     connection is the full board, so every reconnection resyncs.
 *   - The server beats every data-heartbeat-seconds (15). Nothing heard for two beats (30 seconds) means
 *     the connection is dead even if the browser has not noticed: the page says "Reconnecting" and opens
 *     a new one.
 *   - Each reconnection waits longer than the last: from 1 second, doubling to at most 30, half of it
 *     random, so a clinic's boards coming back after load-shedding do not knock at the same instant.
 *     The browser's "online" event tries at once.
 *   - After three failed attempts the board also asks data-state-url every data-poll-seconds, so a
 *     network that blocks streams still gets updates; the stream keeps being retried on the backoff.
 *   - A board whose clinic changed its language reloads, so the page's own words follow.
 *   - When a stream ends, the board asks /state once. A 401 (its kiosk box was removed, Issue 61) sends
 *     it back to the start page, which pairs it again.
 *
 * The connection line (data-board-connection) is hidden while live and shows its words otherwise.
 * Every change of state is announced as a `board:connection` event on the document, and
 * window.ClinicQBoardLive.state() answers what the state is now: Issue 62's stale banner builds on it.
 *
 * External file with no inline handlers: the CSP allows script only from 'self'.
 */
(function () {
  'use strict';

  var root = document.querySelector('.kiosk[data-stream-url]');
  if (!root || !window.ClinicQBoard) return;

  function seconds(name, fallback) {
    var value = parseFloat(root.getAttribute(name));
    return (isFinite(value) && value > 0 ? value : fallback) * 1000;
  }

  var STREAM_URL = root.getAttribute('data-stream-url');
  var STATE_URL = root.getAttribute('data-state-url');
  var HEARTBEAT_MS = seconds('data-heartbeat-seconds', 15);
  var DEAD_AFTER_MS = HEARTBEAT_MS * 2;
  var POLL_MS = seconds('data-poll-seconds', 10);
  var BACKOFF_BASE_MS = 1000;
  var BACKOFF_CAP_MS = 30000;
  var FAILURES_BEFORE_POLLING = 3;
  var WATCH_MS = 1000;
  var BOARD_EVENTS = ['board.state', 'ticket.called', 'queue.updated', 'board.config_changed'];

  var line = root.querySelector('[data-board-connection]');
  var source = null;
  var mode = 'connecting'; // connecting | live | reconnecting
  var attempts = 0; // failed attempts since the board was last live
  var lastHeard = Date.now(); // the last thing heard from the server: an event, a beat, a poll answer
  var lastBoardAt = null; // when the board on screen was last brought, on this box's clock
  var retryTimer = null;
  var pollTimer = null;
  var polling = false;

  function setMode(next) {
    if (mode === next) return;
    mode = next;
    root.setAttribute('data-connection', next);
    if (line) {
      var words = line.getAttribute('data-' + next) || '';
      line.textContent = words;
      line.hidden = next === 'live' || !words;
    }
    document.dispatchEvent(
      new CustomEvent('board:connection', { detail: { state: next, attempts: attempts, lastBoardAt: lastBoardAt } })
    );
  }

  function becameLive() {
    lastHeard = Date.now();
    if (mode !== 'live') {
      attempts = 0;
      stopPolling();
      setMode('live');
    }
  }

  function draw(board) {
    if (!board || !Array.isArray(board.queues)) return;
    if (board.language && root.getAttribute('lang') && board.language !== root.getAttribute('lang')) {
      window.location.reload();
      return;
    }
    lastBoardAt = Date.now();
    window.ClinicQBoard.apply(board);
  }

  function onBoardEvent(event) {
    var data;
    try {
      data = JSON.parse(event.data);
    } catch (error) {
      return;
    }
    becameLive();
    draw(data.board);
  }

  function onHeartbeat() {
    becameLive();
  }

  function closeSource() {
    if (!source) return;
    source.onerror = null;
    source.close();
    source = null;
  }

  function connect() {
    clearTimeout(retryTimer);
    retryTimer = null;
    closeSource();
    lastHeard = Date.now();
    if (!window.EventSource) {
      startPolling();
      return;
    }
    source = new EventSource(STREAM_URL);
    BOARD_EVENTS.forEach(function (name) {
      source.addEventListener(name, onBoardEvent);
    });
    source.addEventListener('heartbeat', onHeartbeat);
    source.onerror = function () {
      // The browser would retry on its own schedule; the board keeps its own, with backoff and jitter.
      closeSource();
      checkAccess();
      reconnectLater();
    };
  }

  function reconnectLater() {
    if (retryTimer) return;
    attempts += 1;
    setMode('reconnecting');
    if (attempts >= FAILURES_BEFORE_POLLING) startPolling();
    var ceiling = Math.min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * Math.pow(2, attempts - 1));
    var wait = ceiling / 2 + Math.random() * (ceiling / 2);
    retryTimer = setTimeout(connect, wait);
  }

  /* A stream that ended may mean this screen was removed from its clinic: ask once, and leave if so. */
  function checkAccess() {
    fetch(STATE_URL, { cache: 'no-store', credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (response) {
        if (response.status === 401) leave();
      })
      .catch(function () {
        // No answer: the network is down, which the reconnection handles.
      });
  }

  function leave() {
    closeSource();
    window.location.assign(root.getAttribute('data-start-url') || '/display');
  }

  function poll() {
    if (polling) return;
    polling = true;
    fetch(STATE_URL, { cache: 'no-store', credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (response) {
        if (response.status === 401) {
          // This screen may no longer show the board (its box was removed): back to pairing (Issue 61).
          leave();
          return null;
        }
        return response.ok ? response.json() : null;
      })
      .then(function (board) {
        if (board && pollTimer !== null) draw(board);
      })
      .catch(function () {
        // Nothing arrived; the board keeps what it has. Issue 62 says how old it is.
      })
      .then(function () {
        polling = false;
        if (pollTimer !== null) pollTimer = setTimeout(poll, POLL_MS);
      });
  }

  function startPolling() {
    if (pollTimer !== null) return;
    pollTimer = setTimeout(poll, 0);
  }

  function stopPolling() {
    clearTimeout(pollTimer);
    pollTimer = null;
  }

  function watch() {
    if (mode === 'live' && Date.now() - lastHeard > DEAD_AFTER_MS) {
      // Two beats missed: the connection is dead whatever the browser thinks.
      closeSource();
      reconnectLater();
    }
  }

  window.addEventListener('online', function () {
    attempts = 0;
    connect();
  });

  setInterval(watch, WATCH_MS);
  lastBoardAt = Date.now(); // the page arrived with a board (data-initial)
  connect();

  window.ClinicQBoardLive = {
    state: function () {
      return { state: mode, attempts: attempts, lastBoardAt: lastBoardAt, lastHeard: lastHeard };
    },
  };
})();
