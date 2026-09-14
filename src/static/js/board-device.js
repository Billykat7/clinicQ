/* A paired kiosk box's heartbeat (Issue 61): proof every minute that its screen is alive.
 *
 * Only on a board opened by a paired box (data-device-heartbeat-url is empty for a staff preview). Every
 * data-device-heartbeat-seconds it posts the version of the page it is running:
 *
 *   - 200: still paired. When the server now runs a newer version, the page reloads at a quiet moment
 *     (no call highlighted), so a box left alone for months still picks up fixes.
 *   - 401: this box was removed from its clinic. It goes back to the start page, which pairs it again.
 *   - anything else, or no answer: nothing to do. The board's own connection line says when it is offline.
 *
 * External file with no inline handlers: the CSP allows script only from 'self'.
 */
(function () {
  'use strict';

  var root = document.querySelector('.kiosk[data-device-heartbeat-url]');
  if (!root) return;
  var URL_ = root.getAttribute('data-device-heartbeat-url');
  if (!URL_) return;
  var EVERY_MS = (parseFloat(root.getAttribute('data-device-heartbeat-seconds')) || 60) * 1000;
  var START = root.getAttribute('data-start-url') || '/display';
  var VERSION = root.getAttribute('data-app-version') || '';
  var newerVersion = false;

  function quiet() {
    return !document.querySelector('.serving.is-new');
  }

  function beat() {
    fetch(URL_, {
      method: 'POST',
      cache: 'no-store',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ app_version: VERSION }),
    })
      .then(function (response) {
        if (response.status === 401) {
          window.location.assign(START);
          return null;
        }
        return response.ok ? response.json() : null;
      })
      .then(function (answer) {
        if (answer && answer.version && VERSION && answer.version !== VERSION) newerVersion = true;
        if (newerVersion && quiet()) window.location.reload();
      })
      .catch(function () {
        // Offline: the board says so. The next beat tries again.
      });
  }

  beat();
  setInterval(beat, EVERY_MS);
})();
