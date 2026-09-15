/**
 * The patient's service worker (Issue 64): shows a web push, and opens the ticket when it is tapped.
 *
 * Served at /patient-sw.js and registered with scope /t/, the ticket pages, so it never touches the staff
 * dashboard or the waiting-room board (which has its own worker, Issue 62). Issue 69 adds the offline
 * shell to this same worker.
 *
 * A push payload is JSON with title, body, url and tag, and nothing else (see
 * src/modules/notifications/transports/webpush.py): the ticket number and the clinic's name. It is shown
 * as it arrives; a message with the same tag replaces the older one, and re-notifies so a phone in a pocket
 * buzzes again for "please come in now" after "you are next".
 */
"use strict";

self.addEventListener("install", function () {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
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
    icon: "/static/favicon.svg",
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
