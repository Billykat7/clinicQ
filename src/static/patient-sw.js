/**
 * The patient app's service worker: web push (Issue 64) and the offline shell (Issue 69).
 *
 * Served at /patient-sw.js (src/web/ticket.py) and registered with scope /t/, the ticket pages and the
 * installed app's start page, so it never touches the staff dashboard or the waiting-room board, which has
 * its own worker and caches (Issue 62). It only ever opens or deletes caches whose names start with
 * "clinicq-patient-".
 *
 * What it does:
 *
 *   - Pages. Every navigation under /t/ goes to the network first: a ticket page is never served old when
 *     the network can answer. When the network cannot be reached (or takes longer than NAVIGATION_LIMIT_MS),
 *     the offline page is served in its place. That page shows the last known state of the ticket, which the
 *     ticket page itself keeps (ticket.js, in the "clinicq-patient-tickets" cache), with how old it is.
 *   - The shell. The offline page and exactly the files it needs (SHELL, written in by the server) are kept
 *     in a cache named for this release. A request for one of them is answered from that cache at once and
 *     refreshed from the network behind it. Nothing else is ever kept, so the cache cannot grow: its size is
 *     the list.
 *   - Updates. The release's version is written in by the server, so a new deploy is a new worker. The
 *     browser checks for one on every navigation; the new worker installs its shell, takes over at once and
 *     deletes the previous release's shell. The next launch after a deploy therefore runs the new release,
 *     with nothing for the patient to clear.
 *   - Everything else (the ticket's JSON, its stream, push subscriptions, preferences) is left to the network.
 *
 * A push payload is JSON with title, body, url and tag, and nothing else (see
 * src/modules/notifications/transports/webpush.py): the ticket number and the clinic's name. It is shown
 * as it arrives; a message with the same tag replaces the older one, and re-notifies so a phone in a pocket
 * buzzes again for "please come in now" after "you are next".
 */
"use strict";

var VERSION = "__CLINICQ_PATIENT_VERSION__";
var PREFIX = "clinicq-patient-";
var SHELL_PREFIX = PREFIX + "shell-";
var SHELL_CACHE = SHELL_PREFIX + VERSION;
var OFFLINE_PAGE = "/t/offline";
var SHELL = __CLINICQ_PATIENT_SHELL__;
var SCOPE_PATH = "/t/";
var NAVIGATION_LIMIT_MS = 10000;

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then(function (cache) {
        // Straight from the server, past the browser's HTTP cache: this release's files, not the last one's.
        return cache.addAll(
          SHELL.map(function (url) {
            return new Request(url, { cache: "reload", credentials: "same-origin" });
          })
        );
      })
      .then(function () {
        return self.skipWaiting();
      })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches
      .keys()
      .then(function (names) {
        return Promise.all(
          names
            .filter(function (name) {
              return name.indexOf(SHELL_PREFIX) === 0 && name !== SHELL_CACHE;
            })
            .map(function (name) {
              return caches.delete(name);
            })
        );
      })
      .then(function () {
        return self.clients.claim();
      })
  );
});

self.addEventListener("message", function (event) {
  var data = event.data || {};
  if (data.type === "version" && event.ports && event.ports[0]) {
    event.ports[0].postMessage({ version: VERSION, shell: SHELL_CACHE });
  }
});

function withinLimit(promise, ms) {
  return new Promise(function (resolve, reject) {
    var timer = setTimeout(function () {
      reject(new Error("timeout"));
    }, ms);
    promise.then(
      function (value) {
        clearTimeout(timer);
        resolve(value);
      },
      function (err) {
        clearTimeout(timer);
        reject(err);
      }
    );
  });
}

function offlinePage() {
  return caches.open(SHELL_CACHE).then(function (cache) {
    return cache.match(OFFLINE_PAGE).then(function (page) {
      return page || new Response("You are offline.", { status: 503, headers: { "Content-Type": "text/plain" } });
    });
  });
}

function fromShell(request) {
  return caches.open(SHELL_CACHE).then(function (cache) {
    return cache.match(request, { ignoreSearch: true }).then(function (kept) {
      var fresh = fetch(request)
        .then(function (response) {
          if (response && response.ok) return cache.put(request, response.clone()).then(function () { return response; });
          return response;
        })
        .catch(function () {
          return kept;
        });
      return kept || fresh;
    });
  });
}

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate" && url.pathname.indexOf(SCOPE_PATH) === 0) {
    event.respondWith(
      withinLimit(fetch(request), NAVIGATION_LIMIT_MS).catch(function () {
        return offlinePage();
      })
    );
    return;
  }
  if (SHELL.indexOf(url.pathname) !== -1) {
    event.respondWith(fromShell(request));
  }
});

self.addEventListener("push", function (event) {
  var data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (err) {
    data = {};
  }
  var title = typeof data.title === "string" && data.title ? data.title : "Your ticket";
  var url = typeof data.url === "string" && data.url.charAt(0) === "/" ? data.url : "/";
  var options = {
    body: typeof data.body === "string" ? data.body : "",
    tag: typeof data.tag === "string" && data.tag ? data.tag : "clinicq-ticket",
    renotify: true,
    requireInteraction: true,
    icon: "/static/icons/app-192.png",
    badge: "/static/favicon.svg",
    data: { url: url }
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (windows) {
      for (var i = 0; i < windows.length; i++) {
        var client = windows[i];
        if (new URL(client.url).pathname === url && "focus" in client) return client.focus();
      }
      return self.clients.openWindow(url);
    })
  );
});
