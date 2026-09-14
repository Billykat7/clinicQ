/* The waiting-room board when the clinic cannot be reached (Issue 62): the last board it had, said to be old.
 *
 * Load-shedding, a flaky router and a restarting server are routine, so a board keeps showing what it
 * last knew and says plainly how old that is, instead of freezing silently or going blank.
 *
 *   - Keeping. Every board that arrives from the clinic (the page's own, the stream's, a poll's) is kept
 *     in this browser's storage with the time it arrived, and the time the clinic was last heard from is
 *     kept up to date while the board is live.
 *   - Coming back. A page the service worker served from its cache (data-from-cache, the box booted with
 *     no network) draws the kept board at once, if it is newer than the page's own, as old data: its time
 *     is not taken as the clinic's clock, so no old call is highlighted or announced as new.
 *   - Saying so. When nothing has been heard from the clinic (a board, a heartbeat, a poll's answer) for
 *     data-stale-after-seconds (20), or the page came from the cache and the clinic has not been heard
 *     from since, a banner replaces the health notice: "Not up to date. Last updated at 14:32.",
 *     in the clinic's time. It goes as soon as the board is current again.
 *   - Not for ever. A kept board older than data-stale-limit-seconds (four hours) is not shown: the
 *     panels are hidden and the banner says since when the clinic has not been reachable. A kept board
 *     that old is deleted.
 *   - The service worker (data-offline-worker-url, /display/board-sw.js) is registered here, and told
 *     which files this page loaded so it can serve them with no network.
 *
 * Only a paired kiosk box runs this: the server sets data-offline-worker-url for a box and leaves it empty
 * for a staff member's preview, whose browser keeps nothing.
 *
 * External file with no inline handlers: the CSP allows script only from 'self'.
 */
(function () {
  'use strict';

  var root = document.querySelector('.kiosk[data-offline-worker-url]');
  if (!root || !root.getAttribute('data-offline-worker-url') || !window.ClinicQBoard) return;

  function seconds(name, fallback) {
    var value = parseFloat(root.getAttribute(name));
    return (isFinite(value) && value > 0 ? value : fallback) * 1000;
  }

  var WORKER_URL = root.getAttribute('data-offline-worker-url');
  var STALE_AFTER_MS = seconds('data-stale-after-seconds', 10);
  var STALE_LIMIT_MS = seconds('data-stale-limit-seconds', 4 * 3600);
  var TICK_MS = 1000;
  var HEARD_WRITE_MS = 15000; // how often "last heard" is written down while live
  var FROM_CACHE = root.hasAttribute('data-from-cache');

  var banner = root.querySelector('[data-board-stale]');
  var ticker = root.querySelector('[data-board-ticker]');
  var format = new Intl.DateTimeFormat('en-ZA', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Africa/Johannesburg',
  });

  var initial = window.ClinicQBoard.state();
  var siteId = initial && initial.site_id;
  var key = 'clinicq.board.' + (siteId || 'unknown');
  var lastGood = Date.now(); // when this box last knew the board was current, on its own clock
  var lastWritten = 0;
  var replaying = false;
  var heardOnce = false; // whether the clinic has been heard from since this page loaded

  function read() {
    try {
      var kept = JSON.parse(window.localStorage.getItem(key) || 'null');
      return kept && kept.board && Array.isArray(kept.board.queues) ? kept : null;
    } catch (error) {
      return null;
    }
  }

  function write(board) {
    try {
      window.localStorage.setItem(key, JSON.stringify({ board: board, heardAt: lastGood }));
      lastWritten = Date.now();
    } catch (error) {
      // Storage full or refused: the board still works, it just cannot come back with its numbers.
    }
  }

  function forget() {
    try {
      window.localStorage.removeItem(key);
    } catch (error) {
      // Nothing kept.
    }
  }

  /* The time a board says it was made, or 0. */
  function madeAt(board) {
    var at = board ? Date.parse(board.as_of) : NaN;
    return isFinite(at) ? at : 0;
  }

  function fill(template, time) {
    return (template || '').replace('{time}', time);
  }

  function show() {
    if (!banner) return;
    var age = Date.now() - lastGood;
    var stale = (FROM_CACHE && !heardOnce) || age >= STALE_AFTER_MS;
    var expired = stale && age >= STALE_LIMIT_MS;
    var state = expired ? 'expired' : stale ? 'stale' : '';
    if (root.getAttribute('data-stale') !== state) {
      if (state) root.setAttribute('data-stale', state);
      else root.removeAttribute('data-stale');
    }
    var words = state
      ? fill(banner.getAttribute(expired ? 'data-expired' : 'data-template'), format.format(new Date(lastGood)))
      : '';
    if (banner.textContent !== words) banner.textContent = words;
    banner.hidden = !state;
    if (ticker) ticker.hidden = !!state;
  }

  /* A board arrived (board.js tells us). One the clinic sent is kept; a replay of the kept one is not. */
  document.addEventListener('board:applied', function (event) {
    if (replaying || (event.detail && event.detail.stale)) return;
    lastGood = Date.now();
    heardOnce = true;
    write(window.ClinicQBoard.state());
    show();
  });

  /* News from the clinic that brought no new board (a heartbeat, an unchanged poll) still counts. */
  function tick() {
    var live = window.ClinicQBoardLive && window.ClinicQBoardLive.state();
    if (live && live.lastNews && live.lastNews > lastGood) {
      lastGood = live.lastNews;
      heardOnce = true;
      if (Date.now() - lastWritten >= HEARD_WRITE_MS) write(window.ClinicQBoard.state());
    }
    show();
  }

  // Coming back with no network: the kept board, if it is newer than the page the cache had.
  var kept = read();
  if (kept && Date.now() - kept.heardAt >= STALE_LIMIT_MS) {
    forget();
    kept = null;
  }
  if (FROM_CACHE) {
    if (kept && madeAt(kept.board) >= madeAt(initial)) {
      replaying = true;
      window.ClinicQBoard.apply(kept.board, { stale: true });
      replaying = false;
    }
    lastGood = kept ? kept.heardAt : lastGood;
  } else if (initial) {
    write(initial); // the page came straight from the clinic: it is the newest board there is
  }

  show();
  setInterval(tick, TICK_MS);

  // The worker, and the files this page needs with no network.
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker
      .register(WORKER_URL, { scope: '/display' })
      .then(function () { return navigator.serviceWorker.ready; })
      .then(function (registration) {
        var urls = [];
        document.querySelectorAll('link[rel="stylesheet"][href], link[rel="preload"][href], script[src]').forEach(function (el) {
          urls.push(el.getAttribute('href') || el.getAttribute('src'));
        });
        (performance.getEntriesByType ? performance.getEntriesByType('resource') : []).forEach(function (entry) {
          urls.push(entry.name);
        });
        var chime = root.getAttribute('data-announce-chime');
        if (chime) urls.push(chime);
        if (registration.active) registration.active.postMessage({ type: 'keep', page: location.pathname, urls: urls });
      })
      .catch(function () {
        // No worker (an old browser, or storage refused): the board still works online.
      });
  }

  window.ClinicQBoardOffline = {
    state: function () {
      return {
        stale: root.getAttribute('data-stale') || null,
        lastGood: lastGood,
        fromCache: FROM_CACHE,
        kept: !!read(),
      };
    },
  };
})();
