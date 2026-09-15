/**
 * The installable patient app (Issue 69): registers the service worker, and offers the home screen at the
 * right moment.
 *
 * Loaded by every page in the app's scope (/t/: the ticket page, the start page, the offline page).
 *
 *   - Registers /patient-sw.js for /t/. Registering asks the patient nothing. The browser then checks for a
 *     new worker on every navigation, which is how a new deploy reaches an installed app on its next launch.
 *   - Never lets the browser offer installation by itself: the browser's own banner could appear the first
 *     time anyone opens a link. Its offer is held back (beforeinstallprompt), and shown only through the
 *     ticket page's "Add to home screen" box, which the server renders only for the patient who joined, on
 *     their own open ticket (offer_install). A shared link never has the box.
 *   - An iPhone has no install offer to hold: the box says how to add the page by hand instead.
 *   - "Not now" is remembered on this phone, and nothing is offered once the app is already installed.
 *
 * External file, no inline handlers, no eval: satisfies script-src 'self'.
 */
(function () {
  "use strict";

  var SW_URL = "/patient-sw.js";
  var SW_SCOPE = "/t/";
  var DISMISSED = "clinicq.install.dismissed";

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register(SW_URL, { scope: SW_SCOPE }).catch(function () {
      /* no worker (a private window, an old browser): the pages still work online */
    });
  }

  var installed =
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) || navigator.standalone === true;
  var box = document.getElementById("tk-install");
  var held = null;

  function dismissed() {
    try {
      return window.localStorage.getItem(DISMISSED) === "1";
    } catch (err) {
      return false;
    }
  }

  function hide(remember) {
    if (box) box.hidden = true;
    if (!remember) return;
    try {
      window.localStorage.setItem(DISMISSED, "1");
    } catch (err) {
      /* storage blocked: it will be offered again next time */
    }
  }

  // Always hold the browser's own offer, on every page: it is ours to make, after a join.
  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    held = event;
    if (box && !installed && !dismissed()) {
      document.getElementById("tk-install-ios").hidden = true;
      document.getElementById("tk-install-yes").hidden = false;
      box.hidden = false;
    }
  });
  window.addEventListener("appinstalled", function () {
    held = null;
    hide(false);
  });

  if (!box || installed || dismissed()) return;

  var isAppleMobile =
    /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (isAppleMobile) {
    document.getElementById("tk-install-yes").hidden = true;
    document.getElementById("tk-install-ios").hidden = false;
    box.hidden = false;
  }

  document.getElementById("tk-install-yes").addEventListener("click", function () {
    if (!held) return;
    var offer = held;
    held = null;
    offer.prompt();
    offer.userChoice
      .then(function (choice) {
        hide(choice && choice.outcome === "dismissed");
      })
      .catch(function () {
        hide(false);
      });
  });
  document.getElementById("tk-install-no").addEventListener("click", function () {
    hide(true);
  });
})();
