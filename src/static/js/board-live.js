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
 *     a new one. An attempt that has neither opened nor failed after as long is given up too, and a poll
 *     gives up after 8 seconds, because a router that drops everything answers nothing at all (Issue 62).
 *   - Each reconnection waits longer than the last: from 1 second, doubling to at most 30, half of it
 *     random, so a clinic's boards coming back after load-shedding do not knock at the same instant.
 *     The browser's "online" event tries at once.
 *   - After three failed attempts the board also asks data-state-url every data-poll-seconds, so a
 *     network that blocks streams still gets updates; the stream keeps being retried on the backoff. A
 *     poll that succeeds after one that failed means the network is back, and the stream is tried at
 *     once, so a board is live again within a poll's interval of the network returning (Issue 62).
 *   - A board whose clinic changed its language reloads, so the page's own words follow.
 *   - When a stream ends, the board asks /state once. A 401 (its kiosk box was removed, Issue 61) sends
 *     it back to the start page, which pairs it again.
 *
 * The connection line (data-board-connection) is hidden while live and shows its words otherwise.
 * Every change of state is announced as a `board:connection` event on the document, and
 * window.ClinicQBoardLive.state() answers what the state is now, and lastNews, when the server last
 * answered at all, which Issue 62's stale banner (board-offline.js) is built on.
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
  var REQUEST_LIMIT_MS = 8000; // a poll or an access check that has not answered by then has failed
  var BOARD_EVENTS = ['board.state', 'ticket.called', 'queue.updated', 'board.config_changed'];

  var line = root.querySelector('[data-board-connection]');
  var source = null;
  var mode = 'connecting'; // connecting | live | reconnecting
  var attempts = 0; // failed attempts since the board was last live
  var lastHeard = Date.now(); // the watchdog's mark: the last thing heard, or the last connection attempt
  var lastNews = null; // the last time the server itself answered: an event, a beat, a poll (Issue 62)
  var pollFailing = false; // the last poll found no network
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
    lastNews = lastHeard;
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

  /* Ask for the board, giving up after REQUEST_LIMIT_MS: on a dead router a request can hang for minutes. */
  function askForBoard() {
    var controller = window.AbortController ? new AbortController() : null;
    var limit = controller ? setTimeout(function () { controller.abort(); }, REQUEST_LIMIT_MS) : null;
    return fetch(STATE_URL, {
      cache: 'no-store',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      signal: controller ? controller.signal : undefined,
    }).then(
      function (response) {
        clearTimeout(limit);
        return response;
      },
      function (error) {
        clearTimeout(limit);
        throw error;
      }
    );
  }

  /* A stream that ended may mean this screen was removed from its clinic: ask once, and leave if so. */
  function checkAccess() {
    askForBoard()
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
    askForBoard()
      .then(function (response) {
        if (response.status === 401) {
          // This screen may no longer show the board (its box was removed): back to pairing (Issue 61).
          leave();
          return null;
        }
        return response.ok ? response.json() : null;
      })
      .then(function (board) {
        if (!board) return;
        lastNews = Date.now();
        if (pollTimer !== null) draw(board);
        if (pollFailing) {
          // The network is back (Issue 62): try the stream now rather than when the backoff says.
          pollFailing = false;
          if (mode !== 'live') {
            attempts = 0;
            connect();
          }
        }
      })
      .catch(function () {
        // Nothing arrived; the board keeps what it has, and board-offline.js says how old it is.
        pollFailing = true;
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
    if (Date.now() - lastHeard <= DEAD_AFTER_MS) return;
    if (mode === 'live') {
      // Two beats missed: the connection is dead whatever the browser thinks.
      closeSource();
      reconnectLater();
    } else if (source && !retryTimer) {
      // An attempt that neither opened nor failed in that time: a router that drops everything and
      // answers nothing (Issue 62). Give it up and try again on the backoff.
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
      return { state: mode, attempts: attempts, lastBoardAt: lastBoardAt, lastHeard: lastHeard, lastNews: lastNews };
    },
  };
})();
