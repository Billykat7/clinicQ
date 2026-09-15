/**
 * The installed app's start page (Issue 69): opens the last ticket this phone followed.
 *
 * The server has already sent a signed-in patient with an open ticket straight to it. Here, with no
 * sign-in, the most recent ticket patient-tickets.js kept is opened if it has not ended; otherwise the page
 * stays as it is, saying how to join a queue and offering the phone sign-in. Only a ticket link shape is
 * ever opened.
 * External file, no inline handlers, no eval: satisfies script-src 'self'.
 */
(function () {
  "use strict";

  var FINISHED = ["done", "cancelled", "missed", "transferred"];

  // Signing in by phone (Issue 200) comes back here: the server then opens the patient's open ticket.
  if (window.BKPSignIn) {
    window.BKPSignIn.start(function () {
      window.location.replace("/t/");
    });
  }

  var store = window.BKPTickets;
  if (!store) return;

  store.latest().then(function (entry) {
    if (!entry || FINISHED.indexOf(entry.state.headline) !== -1) return;
    if (/^\/t\/[A-Za-z0-9_-]{43}$/.test(entry.path)) window.location.replace(entry.path);
  });
})();
