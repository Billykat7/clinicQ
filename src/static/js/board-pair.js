/* An unpaired kiosk box's start page (Issue 61): wait for a manager to type the code in.
 *
 * Asks data-pairing-url every data-poll-seconds whether this box has been paired. "paired" opens the
 * board it was paired with; "expired" reloads the page, which shows a fresh code. A failed request is
 * tried again on the next beat, so a box that boots before the network is up pairs as soon as it is.
 *
 * It also forgets any board this box kept for offline use (Issue 62): its stored last board, and the
 * service worker's copy of the board page, so a removed box cannot show a board even with no network.
 *
 * External file with no inline handlers: the CSP allows script only from 'self'.
 */
(function () {
  'use strict';

  var root = document.querySelector('.kiosk-pair[data-pairing-url]');
  if (!root) return;
  var URL_ = root.getAttribute('data-pairing-url');
  var POLL_MS = (parseFloat(root.getAttribute('data-poll-seconds')) || 3) * 1000;
  var expiry = root.querySelector('[data-pair-expiry]');

  // A box showing a pairing code is not paired: nothing it kept of a board may outlive that (Issue 62).
  try {
    Object.keys(window.localStorage)
      .filter(function (key) { return key.indexOf('clinicq.board.') === 0; })
      .forEach(function (key) { window.localStorage.removeItem(key); });
  } catch (error) {
    // Storage refused: nothing was kept.
  }
  if (navigator.serviceWorker && navigator.serviceWorker.controller) {
    navigator.serviceWorker.controller.postMessage({ type: 'forget' });
  }

  function ask() {
    fetch(URL_, { cache: 'no-store', credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (answer) {
        if (answer && answer.state === 'paired' && answer.board_url) {
          window.location.assign(answer.board_url);
          return;
        }
        if (answer && answer.state === 'expired') {
          if (expiry) expiry.textContent = expiry.getAttribute('data-expired');
          window.location.reload();
          return;
        }
        setTimeout(ask, POLL_MS);
      })
      .catch(function () {
        setTimeout(ask, POLL_MS);
      });
  }

  setTimeout(ask, POLL_MS);
})();
