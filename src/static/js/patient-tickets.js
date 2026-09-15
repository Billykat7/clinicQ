/**
 * The tickets this phone has followed, kept for when there is no signal (Issue 69).
 *
 * The ticket page saves every state it shows; the offline page and the app's start page read them back. Kept
 * in the Cache API (cache "clinicq-patient-tickets"), which the service worker's offline page can read as
 * well as any page, one entry per ticket link, each with the moment this phone received it.
 *
 * Bounded: at most MAX_TICKETS entries, the most recently updated, and none older than MAX_AGE_MS. Every save
 * prunes, so however long a phone is used the cache holds a handful of small JSON entries.
 *
 * Only what the page already shows is kept: the ticket number, clinic and queue names, place in line and
 * status. Nothing about the patient. Where the Cache API is missing (an old browser, a private window) every
 * call quietly does nothing.
 *
 * window.BKPTickets:
 *   save(path, state)  -> Promise   keep this state for the ticket page at path (/t/<token>)
 *   get(path)          -> Promise<{path, state, receivedAt} | null>
 *   latest()           -> Promise<{path, state, receivedAt} | null>   the most recently updated
 *   all()              -> Promise<Array<{path, state, receivedAt}>>   newest first
 */
(function () {
  "use strict";

  var CACHE = "clinicq-patient-tickets";
  var KEY_PREFIX = "/t/__kept__/";
  var MAX_TICKETS = 5;
  var MAX_AGE_MS = 18 * 3600 * 1000;
  var PAGE_PATH = /^\/t\/[A-Za-z0-9_-]{43}$/;

  function available() {
    return typeof caches !== "undefined" && typeof caches.open === "function";
  }

  function keyFor(path) {
    return KEY_PREFIX + path.slice(3);
  }

  function open() {
    return caches.open(CACHE);
  }

  function read(cache, request) {
    return cache.match(request).then(function (response) {
      return response ? response.json().catch(function () { return null; }) : null;
    });
  }

  function all() {
    if (!available()) return Promise.resolve([]);
    return open()
      .then(function (cache) {
        return cache.keys().then(function (requests) {
          return Promise.all(
            requests.map(function (request) {
              return read(cache, request).then(function (entry) {
                return { request: request, entry: entry };
              });
            })
          ).then(function (rows) {
            var now = Date.now();
            var fresh = [];
            var drop = [];
            rows.forEach(function (row) {
              var entry = row.entry;
              if (entry && PAGE_PATH.test(entry.path) && entry.state && now - entry.receivedAt < MAX_AGE_MS) {
                fresh.push(row);
              } else {
                drop.push(row.request);
              }
            });
            fresh.sort(function (a, b) {
              return b.entry.receivedAt - a.entry.receivedAt;
            });
            fresh.slice(MAX_TICKETS).forEach(function (row) {
              drop.push(row.request);
            });
            return Promise.all(
              drop.map(function (request) {
                return cache.delete(request);
              })
            ).then(function () {
              return fresh.slice(0, MAX_TICKETS).map(function (row) {
                return row.entry;
              });
            });
          });
        });
      })
      .catch(function () {
        return [];
      });
  }

  function save(path, state) {
    if (!available() || !PAGE_PATH.test(path) || !state) return Promise.resolve();
    var entry = { path: path, state: state, receivedAt: Date.now() };
    return open()
      .then(function (cache) {
        return cache.put(
          keyFor(path),
          new Response(JSON.stringify(entry), { headers: { "Content-Type": "application/json" } })
        );
      })
      .then(all)
      .then(function () {})
      .catch(function () {});
  }

  function get(path) {
    return all().then(function (entries) {
      for (var i = 0; i < entries.length; i++) if (entries[i].path === path) return entries[i];
      return null;
    });
  }

  function latest() {
    return all().then(function (entries) {
      return entries[0] || null;
    });
  }

  window.BKPTickets = { save: save, get: get, latest: latest, all: all, MAX_TICKETS: MAX_TICKETS };
})();
