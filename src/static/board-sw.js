/* The waiting-room board's service worker (Issue 62): a kiosk box that boots with no network still shows
 * its board, with the last numbers it had and how old they are.
 *
 * Served as /display/board-sw.js (src/web/display.py), so it looks after /display and below and nothing
 * else. It is registered only by a paired box's board page (board-offline.js), never by a staff preview.
 * It is separate from the patient app's worker (Issue 69): its own scope, its own cache names, and it
 * never touches another worker's caches.
 *
 * What it does:
 *
 *   - Pages. Every navigation under /display goes to the network first. A board page that arrives
 *     (/display/{clinic}, 200) is kept, as itself and as "the last board". When the network cannot be
 *     reached, the kept copy is served for that address, or the last board for /display, the address the
 *     box opens at boot. A kept page is marked data-from-cache, so the board knows its numbers are old
 *     and does not take their time as the clinic's clock.
 *   - Forgetting. A start page that arrives as the pairing screen (/display, 200) means this box is not
 *     paired any more: every kept page is dropped at once, so a removed box cannot show a board offline.
 *     The page can also ask with a { type: 'forget' } message.
 *   - Assets. The board page sends its own address and the list of what it loaded
 *     ({ type: 'keep', page, urls }): the page is kept as above (its first navigation may have come before
 *     this worker was in charge), and those files are kept. A request for a kept file is answered from the cache at once and refreshed from the
 *     network behind it.
 *   - Everything else (the board's JSON, its stream, the heartbeat, the pairing check) is left to the
 *     network: stale data comes only from the page's own record, which says how old it is.
 *
 * The version below is written in by the server, so a new release installs a new worker, which keeps a
 * new cache and drops the old one.
 */
'use strict';

var VERSION = '__CLINICQ_BOARD_VERSION__';
var PREFIX = 'clinicq-board-';
var CACHE = PREFIX + VERSION;
var LAST_BOARD = '/display/__last-board__';
var START = '/display';
var NAVIGATION_LIMIT_MS = 8000; // a network this slow at boot is as good as none

function isBoardPath(path) {
  return /^\/display\/[^/]+$/.test(path) && path !== LAST_BOARD && !/\.js$/.test(path) && path !== '/display/pairing';
}

self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches
      .keys()
      .then(function (names) {
        return Promise.all(
          names
            .filter(function (name) { return name.indexOf(PREFIX) === 0 && name !== CACHE; })
            .map(function (name) { return caches.delete(name); })
        );
      })
      .then(function () { return self.clients.claim(); })
  );
});

function forget() {
  return caches.keys().then(function (names) {
    return Promise.all(
      names
        .filter(function (name) { return name.indexOf(PREFIX) === 0; })
        .map(function (name) { return caches.delete(name); })
    );
  });
}

self.addEventListener('message', function (event) {
  var data = event.data || {};
  if (data.type === 'forget') {
    event.waitUntil(forget());
  } else if (data.type === 'keep' && Array.isArray(data.urls)) {
    // The page that sent this: kept now, because its own navigation may have happened before this
    // worker was in charge of it (the first load of a box).
    if (typeof data.page === 'string' && isBoardPath(data.page)) {
      event.waitUntil(keepPage(data.page));
    }
    var urls = data.urls.filter(function (url) {
      var parsed = new URL(url, self.location.origin);
      return parsed.origin === self.location.origin && parsed.pathname.indexOf('/static/') === 0;
    });
    event.waitUntil(
      caches.open(CACHE).then(function (cache) {
        return Promise.all(
          urls.map(function (url) {
            return cache.match(url).then(function (hit) {
              return hit || cache.add(url).catch(function () {});
            });
          })
        );
      })
    );
  }
});

/* Fetch a board page and keep it, as itself and as the last board, if it arrives as a board. */
function keepPage(path) {
  return fetch(path, { credentials: 'same-origin', cache: 'no-store', redirect: 'manual' })
    .then(function (response) {
      if (response.type !== 'basic' || response.status !== 200) return undefined;
      var copy = response.clone();
      return caches.open(CACHE).then(function (cache) {
        return Promise.all([cache.put(path, response), cache.put(LAST_BOARD, copy)]);
      });
    })
    .catch(function () {});
}

function withLimit(promise, ms) {
  return new Promise(function (resolve, reject) {
    var timer = setTimeout(function () { reject(new Error('timeout')); }, ms);
    promise.then(
      function (value) { clearTimeout(timer); resolve(value); },
      function (error) { clearTimeout(timer); reject(error); }
    );
  });
}

/* A kept page, marked as coming from the cache. Its headers are kept (the CSP among them), less its length. */
function marked(response) {
  return response.text().then(function (html) {
    var headers = new Headers(response.headers);
    headers.delete('content-length');
    return new Response(html.replace('data-initial=', 'data-from-cache="1" data-initial='), {
      status: 200,
      statusText: 'OK',
      headers: headers,
    });
  });
}

function fromCache(path) {
  return caches.open(CACHE).then(function (cache) {
    var exact = isBoardPath(path) ? cache.match(path) : Promise.resolve(undefined);
    return exact.then(function (hit) {
      if (hit) return hit;
      return path === START || isBoardPath(path) ? cache.match(LAST_BOARD) : undefined;
    });
  });
}

function navigate(event, path) {
  return withLimit(fetch(event.request), NAVIGATION_LIMIT_MS)
    .then(function (response) {
      if (response.type === 'basic' && response.status === 200) {
        var arrived = new URL(response.url).pathname;
        if (isBoardPath(arrived)) {
          var copy = response.clone();
          var second = response.clone();
          event.waitUntil(
            caches.open(CACHE).then(function (cache) {
              return Promise.all([cache.put(arrived, copy), cache.put(LAST_BOARD, second)]);
            })
          );
        } else if (arrived === START) {
          event.waitUntil(forget());
        }
      }
      return response;
    })
    .catch(function () {
      return fromCache(path).then(function (hit) {
        if (hit) return marked(hit);
        return Response.error();
      });
    });
}

function asset(event) {
  return caches.open(CACHE).then(function (cache) {
    return cache.match(event.request).then(function (hit) {
      var refreshed = fetch(event.request)
        .then(function (response) {
          if (response.ok && response.type === 'basic') cache.put(event.request, response.clone());
          return response;
        })
        .catch(function () { return hit || Response.error(); });
      if (hit) {
        event.waitUntil(refreshed.catch(function () {}));
        return hit;
      }
      return refreshed;
    });
  });
}

self.addEventListener('fetch', function (event) {
  var request = event.request;
  if (request.method !== 'GET') return;
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.mode === 'navigate' && (url.pathname === START || url.pathname.indexOf(START + '/') === 0)) {
    event.respondWith(navigate(event, url.pathname));
    return;
  }
  if (url.pathname.indexOf('/static/') === 0) {
    event.respondWith(
      caches.open(CACHE).then(function (cache) {
        return cache.match(request).then(function (hit) {
          return hit ? asset(event) : fetch(request);
        });
      })
    );
  }
});
